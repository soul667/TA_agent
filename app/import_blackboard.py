"""Import Blackboard submissions without executing or extracting uploaded programs.

The original archive and an audit manifest are retained under a separate AA_ROOT.
Each student's latest attempt includes all attached C sources, including nested ZIPs.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import zipfile
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath

from .models import SourceFile, Submission, now_utc
from .storage import save_json_atomic

ENTRY = re.compile(
    r"^(?P<label>.+)_(?P<sid>[0-9]{4,20})_attempt_"
    r"(?P<stamp>\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})(?P<tail>.*)$"
)
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_SOURCE_BYTES = 2 * 1024 * 1024


def decode_source(raw: bytes) -> tuple[str, str]:
    encodings = ["utf-8-sig", "gb18030"]
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings.insert(0, "utf-16")
    for encoding in encodings:
        try:
            text = raw.decode(encoding)
            if "\x00" not in text:
                return text.replace("\r\n", "\n").replace("\r", "\n"), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError("无法识别文本编码，需人工检查原文件")


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in {"p", "br", "div", "pre"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"p", "div", "pre"}:
            self.parts.append("\n")


def submission_text(metadata: str) -> str:
    match = re.search(r"Submission Field:\s*\n(.*?)(?:\nComments:|\nFiles:|$)", metadata, re.S)
    text = match.group(1).strip() if match else ""
    if text == "There is no student submission text data for this assignment.":
        return ""
    if re.search(r"<(?:p|br|div|pre)(?:\s|>|/)", text, re.I):
        parser = _HTMLText()
        parser.feed(text)
        text = "".join(parser.parts).strip()
    return text


def _same_pasted_code(left: str, right: str) -> bool:
    # Blackboard HTML introduces blank lines/indentation. Do not remove spaces
    # inside C expressions or string literals when checking duplicate pastes.
    def lines(value):
        return tuple(line.strip().replace("\xa0", " ") for line in value.splitlines() if line.strip())
    return lines(left) == lines(right)


def _safe_name(name: str) -> bool:
    path = PurePosixPath(name.replace("\\", "/"))
    return not path.is_absolute() and ".." not in path.parts and not re.match(r"^[A-Za-z]:", name)


def _read_file(z: zipfile.ZipFile, info: zipfile.ZipInfo, budget: list[int]) -> bytes:
    if info.file_size > MAX_TOTAL_BYTES or budget[0] + info.file_size > MAX_TOTAL_BYTES:
        raise ValueError("提交包解压大小超过导入上限")
    budget[0] += info.file_size
    return z.read(info)


def _sources(z, info, display_name, member, budget, sources, ignored, notes, depth=0):
    if not _safe_name(display_name):
        notes.append(f"跳过不安全的文件路径：{display_name}")
        ignored.append(member)
        return
    suffix = PurePosixPath(display_name).suffix.lower()
    if suffix == ".zip" and depth < 3:
        try:
            with zipfile.ZipFile(io.BytesIO(_read_file(z, info, budget))) as nested:
                for inner in sorted(nested.infolist(), key=lambda item: item.filename):
                    if not inner.is_dir():
                        _sources(nested, inner, inner.filename, f"{member}!/{inner.filename}",
                                 budget, sources, ignored, notes, depth + 1)
        except (zipfile.BadZipFile, RuntimeError) as exc:
            notes.append(f"压缩附件无法读取：{display_name}（{type(exc).__name__}）")
        return
    if suffix not in {".c", ".h"}:
        ignored.append(member)
        return
    if info.file_size > MAX_SOURCE_BYTES:
        notes.append(f"源文件过大，需人工检查：{display_name}")
        ignored.append(member)
        return
    raw = _read_file(z, info, budget)
    try:
        code, encoding = decode_source(raw)
    except ValueError as exc:
        notes.append(f"{display_name}：{exc}")
        ignored.append(member)
        return
    if not code.strip():
        notes.append(f"空源文件：{display_name}")
        ignored.append(member)
        return
    sources.append(SourceFile(filename=display_name, code=code, archive_member=member,
                              sha256=hashlib.sha256(raw).hexdigest(), encoding=encoding))


def combine_sources(sources: list[SourceFile]) -> str:
    if len(sources) == 1:
        sources[0].line_start = 1
        return sources[0].code
    sections = []
    line = 1
    for source in sources:
        # A quoted filename cannot inject a newline or terminate a C comment.
        marker = "// FILE: " + json.dumps(source.filename, ensure_ascii=False)
        source.line_start = line + 1
        section = marker + "\n" + source.code.rstrip("\n") + "\n\n"
        sections.append(section)
        line += section.count("\n")
    return "".join(sections)


def import_archive(archive: Path, destination: Path, assignment_id: str) -> dict:
    archive = archive.resolve()
    destination = destination.resolve()
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    attempts = defaultdict(lambda: defaultdict(list))
    unknown = []
    budget = [0]
    submissions = []
    rows = []
    with zipfile.ZipFile(archive) as z:
        if len(z.infolist()) > 10000:
            raise ValueError("提交包文件数量超过导入上限")
        for info in z.infolist():
            if info.is_dir():
                continue
            match = ENTRY.fullmatch(info.filename)
            if not match or (match['tail'] != '.txt' and not match['tail'].startswith('_')):
                unknown.append(info.filename)
                continue
            attempts[match['sid']][match['stamp']].append((info, match['tail']))
        if not attempts:
            raise ValueError("未识别到 Blackboard 学号与提交时间；没有写入任何记录")
        for sid, versions in sorted(attempts.items()):
            stamp = max(versions)
            sources, ignored, notes = [], [], []
            metadata = ""
            for info, tail in sorted(versions[stamp], key=lambda item: item[0].filename):
                if tail == ".txt":
                    metadata, _ = decode_source(_read_file(z, info, budget))
                else:
                    _sources(z, info, tail[1:], info.filename, budget, sources, ignored, notes)
            inline = submission_text(metadata)
            if inline and re.search(r"#\s*include|\b(?:int|void)\s+main\s*\(", inline):
                if any(_same_pasted_code(inline, source.code) for source in sources):
                    notes.append("文本框重复粘贴了附件代码；展示附件原文件，原文本另行保留")
                else:
                    sources.append(SourceFile(filename="submission-text.c", code=inline,
                        archive_member=f"submission-field:{sid}:{stamp}",
                        sha256=hashlib.sha256(inline.encode()).hexdigest()))
                    notes.append("含 Blackboard 文本框中的代码")
            if not sources:
                notes.append("该次提交没有可读取的 C 源代码，请核对原始提交")
            if len(sources) > 1:
                notes.append(f"保留该次提交的全部 {len(sources)} 个源文件；文件边界已标注")
            if ignored:
                notes.append("图片、可执行文件和编译中间文件仅保留在原始压缩包中")
            submitted_at = stamp[:10] + "T" + stamp[11:].replace("-", ":") + "+08:00"
            submission = Submission(student_id=sid, assignment_id=assignment_id,
                code=combine_sources(sources), submitted_at=submitted_at, source_files=sources,
                import_notes=notes, submission_text=inline, source_archive_sha256=digest)
            submissions.append(submission)
            rows.append({"student_id": sid, "submitted_at": submitted_at,
                "status": "ready" if sources else "missing_source",
                "source_files": [{"filename": s.filename, "archive_member": s.archive_member,
                    "sha256": s.sha256, "encoding": s.encoding, "line_start": s.line_start}
                    for s in sources], "ignored_files": ignored, "notes": notes,
                "older_attempts": sorted(set(versions) - {stamp})})
    original = destination / "source" / f"submissions-{digest[:12]}.zip"
    original.parent.mkdir(parents=True, exist_ok=True)
    if not original.exists():
        shutil.copy2(archive, original)
        original.chmod(0o600)
    for submission in submissions:
        save_json_atomic(destination / "data/submissions" / f"{submission.student_id}.json",
                         submission.model_dump(mode="json"))
    manifest = {
        "assignment_id": assignment_id, "imported_at": now_utc().isoformat(),
        "archive_name": archive.name, "archive_sha256": digest,
        "original_archive": str(original.relative_to(destination)),
        "student_count": len(rows), "ready_count": sum(r['status'] == 'ready' for r in rows),
        "missing_source_count": sum(r['status'] == 'missing_source' for r in rows),
        "source_file_count": sum(len(r['source_files']) for r in rows),
        "unrecognized_entries": unknown, "students": rows,
    }
    save_json_atomic(destination / "data/imports" / f"{digest}.json", manifest)
    save_json_atomic(destination / "data/imports/latest.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--assignment-id", required=True)
    args = parser.parse_args()
    manifest = import_archive(args.archive, args.root, args.assignment_id)
    print(json.dumps({k: manifest[k] for k in (
        "assignment_id", "student_count", "ready_count", "missing_source_count", "source_file_count")},
        ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
