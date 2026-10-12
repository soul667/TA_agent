#!/usr/bin/env python3
"""把作业 ZIP 拆成「每个学生一份 Markdown」，供出题时阅读。

只读取文本，绝不解压到磁盘或执行学生文件。支持：
- Blackboard 批量下载包（<作业>_<学号>_attempt_<时间>[_文件名]），每人只取最新一次提交；
- 普通压缩包：路径中第一个 6–20 位数字串视为学号（如 12345678/main.c、张三_12345678.zip）；
- 嵌套 ZIP（最多 3 层）、UTF-8 / GB18030 / UTF-16 编码、Blackboard 文本框里粘贴的代码。

用法：python unpack.py 作业.zip [--out 输出目录]
输出：<out>/index.md（名单）、<out>/submissions/<学号>.md（带行号的代码，给 agent 读）、
     <out>/students.json（同样内容，给 build.py 生成查询页面）。
"""
from __future__ import annotations

import argparse
import hashlib
import html
import io
import json
import os
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path, PurePosixPath

BLACKBOARD = re.compile(
    r"^.+_(?P<sid>[0-9]{4,20})_attempt_"
    r"(?P<stamp>\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})(?P<tail>.*)$")
SID = re.compile(r"(?<!\d)\d{6,20}(?!\d)")
CODE_EXT = {".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hh", ".py", ".java", ".js", ".ts",
            ".go", ".rs", ".cs", ".kt", ".swift", ".m", ".r", ".sql", ".sh", ".txt", ".md"}
MAX_READ_BYTES = 64 * 1024 * 1024   # 整个包里实际读取的总量
MAX_FILE_BYTES = 512 * 1024         # 单个源文件
MAX_ENTRIES = 10000
TEXTBOX = "文本框.txt"                # Blackboard 文本框里的内容，当作一个文件处理


def decode(raw: bytes) -> str | None:
    encodings = ["utf-8-sig", "gb18030"]
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings.insert(0, "utf-16")
    for encoding in encodings:
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "\x00" not in text:
            return text.replace("\r\n", "\n").replace("\r", "\n")
    return None


def parse_metadata(text: str) -> dict:
    """Blackboard 每次提交附带的 .txt：姓名、作业名、文本框内容。"""
    def field(name):
        m = re.search(rf"^{name}:\s*(.+)$", text, re.M)
        return m.group(1).strip() if m else ""
    m = re.search(r"Submission Field:\s*\n(.*?)(?:\nComments:|\nFiles:|$)", text, re.S)
    body = m.group(1).strip() if m else ""
    if body == "There is no student submission text data for this assignment.":
        body = ""
    if re.search(r"<(?:p|br|div|pre)(?:\s|>|/)", body, re.I):  # 富文本：段落换成换行，再还原 &lt; 等实体
        body = re.sub(r"<br\s*/?>|</?(?:p|div|pre)\b[^>]*>", "\n", body, flags=re.I)
        body = html.unescape(re.sub(r"<[^>]+>", "", body)).strip()
    name = re.sub(r"\s*\(\d+\)\s*$", "", field("Name"))
    return {"name": name, "assignment": field("Assignment"), "text": body.replace("\xa0", " ")}


def same_code(a: str, b: str) -> bool:
    # Blackboard 富文本会插入空行和缩进；只按非空行比较，不改动行内空格
    def lines(s):
        return [line.strip() for line in s.splitlines() if line.strip()]
    return lines(a) == lines(b)


def dedupe(student: dict) -> list:
    """学生常把同样的文件既单独交、又打包进 zip 再交一次；内容相同的只留第一份。"""
    kept = []
    for name, code in sorted(student["files"], key=lambda f: f[0].count("/")):  # 路径浅的（散文件）优先保留
        twin = next((n for n, c in kept if same_code(code, c)), None)
        if twin:
            student["notes"].append(f"{name} 与 {twin} 内容相同，已省略")
        else:
            kept.append((name, code))
    return sorted(kept)


def safe(name: str) -> bool:
    path = PurePosixPath(name.replace("\\", "/"))
    return not path.is_absolute() and ".." not in path.parts and not re.match(r"^[A-Za-z]:", name)


def junk(name: str) -> bool:
    parts = name.replace("\\", "/").split("/")
    return "__MACOSX" in parts or parts[-1].startswith("._") or parts[-1] in {".DS_Store", "Thumbs.db"}


class Reader:
    def __init__(self):
        self.budget = 0

    def read(self, z: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
        self.budget += info.file_size
        if self.budget > MAX_READ_BYTES:
            raise ValueError("压缩包内容超过读取上限（64MB），请拆分后再试")
        return z.read(info)

    def collect(self, z, info, display, student, depth=0):
        """把一个附件读进 student：源码进 files，嵌套 ZIP 递归，其余记为忽略。"""
        if junk(display):
            return
        if not safe(display):
            student["notes"].append(f"跳过不安全的路径：{display}")
            return
        suffix = PurePosixPath(display).suffix.lower()
        if suffix == ".zip" and depth < 3:
            try:
                with zipfile.ZipFile(io.BytesIO(self.read(z, info))) as inner:
                    for item in sorted(inner.infolist(), key=lambda i: i.filename):
                        if not item.is_dir():
                            self.collect(inner, item, f"{display}/{item.filename}", student, depth + 1)
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
                student["notes"].append(f"压缩附件无法读取：{display}（{type(exc).__name__}）")
            return
        if suffix not in CODE_EXT:
            student["ignored"].append(display)
            return
        if info.file_size > MAX_FILE_BYTES:
            student["notes"].append(f"文件过大未读取：{display}")
            return
        text = decode(self.read(z, info))
        if text is None:
            student["notes"].append(f"无法识别编码：{display}")
        elif text.strip():
            student["files"].append((display, text))


def generic_sid(path: str, skip: int) -> tuple[str, str] | None:
    """普通包：返回 (学号, 学号之后的显示路径)。先跳过所有文件共有的外层目录（如 PA1_20260914/）。"""
    parts = path.split("/")
    for start in (skip, 0) if skip else (0,):
        for i in range(start, len(parts)):
            m = SID.search(parts[i])
            if m:
                return m.group(), "/".join(parts[i + 1:]) or parts[i]
    return None


def unpack(archive: Path) -> tuple[dict, list[str]]:
    reader = Reader()
    groups = defaultdict(lambda: defaultdict(list))   # sid -> stamp -> [(info, kind, display)]
    unknown = []
    with zipfile.ZipFile(archive) as z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        if len(infos) > MAX_ENTRIES:
            raise ValueError("压缩包文件数量过多")
        infos = [i for i in infos if not junk(i.filename)]
        skip = len(os.path.commonprefix([i.filename.split("/")[:-1] for i in infos]))
        for info in infos:
            m = BLACKBOARD.fullmatch(info.filename)
            if m and m["tail"] == ".txt":
                groups[m["sid"]][m["stamp"]].append((info, "meta", ""))
            elif m and m["tail"].startswith("_"):
                groups[m["sid"]][m["stamp"]].append((info, "file", m["tail"][1:]))
            elif (hit := generic_sid(info.filename, skip)) is not None:
                groups[hit[0]][""].append((info, "file", hit[1]))
            else:
                unknown.append(info.filename)
        students = {}
        for sid, attempts in sorted(groups.items()):
            stamp = max(attempts)
            s = {"files": [], "ignored": [], "notes": [], "name": "", "assignment": "", "text": "",
                 "submitted": "" if not stamp else stamp[:10] + " " + stamp[11:].replace("-", ":")}
            if len(attempts) > 1:
                s["notes"].append(f"共 {len(attempts)} 次提交，只取最新一次")
            for info, kind, display in sorted(attempts[stamp], key=lambda e: e[0].filename):
                if kind == "meta":
                    s.update(parse_metadata(decode(reader.read(z, info)) or ""))
                else:
                    reader.collect(z, info, display, s)
            s["files"] = dedupe(s)
            text = s.pop("text")
            if text and any(same_code(text, code) for _, code in s["files"]):
                s["notes"].append("文本框重复粘贴了附件代码，已省略")
            elif text:
                s["files"].append((TEXTBOX, text))
            students[sid] = s
    if not students:
        raise ValueError("没有识别到任何学号：既不是 Blackboard 下载包，路径里也没有 6–20 位数字学号")
    return students, unknown


def numbered(text: str, suffix: str) -> str:
    lines = text.rstrip("\n").split("\n")
    width = len(str(len(lines)))
    body = "\n".join(f"{n:>{width}} | {line}" for n, line in enumerate(lines, 1))
    fence = "`" * max(3, max((len(r) for r in re.findall(r"`+", text)), default=0) + 1)
    return f"{fence}{suffix.lstrip('.')}\n{body}\n{fence}"


def fingerprint(s: dict) -> str:
    h = hashlib.sha256()
    for name, code in s["files"]:
        h.update(name.encode() + b"\0" + code.encode() + b"\0")
    return h.hexdigest()[:12]


def render(sid: str, s: dict) -> str:
    out = [f"# {sid}" + (f" {s['name']}" if s["name"] else ""), ""]
    if s["submitted"]:
        out.append(f"- 提交时间：{s['submitted']}")
    out.append(f"- 指纹：{fingerprint(s)}")
    out += [f"- 备注：{note}" for note in s["notes"]]
    if s["ignored"]:
        out.append(f"- 未读取的非文本附件：{', '.join(s['ignored'])}")
    for name, code in s["files"]:
        out += ["", f"## {name}", "", numbered(code, PurePosixPath(name).suffix.lower())]
    if not s["files"]:
        out += ["", "**这份提交里没有可读的代码或文字，不要为该生出题，请 TA 核对原始提交。**"]
    return "\n".join(out) + "\n"


def write(students: dict, unknown: list[str], archive: Path, out: Path) -> str:
    folder = out / "submissions"
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.md"):
        old.unlink()
    for sid, s in students.items():
        (folder / f"{sid}.md").write_text(render(sid, s), encoding="utf-8")
    titles = sorted({s["assignment"] for s in students.values() if s["assignment"]})
    empty = [sid for sid, s in students.items() if not s["files"]]
    summary = [
        f"# {archive.name}", "",
        f"- 作业：{' / '.join(titles) or '（包里未写明，需从代码推断或询问 TA）'}",
        f"- 学生 {len(students)} 人，有内容 {len(students) - len(empty)} 人"
        + (f"；无可读代码：{', '.join(empty)}" if empty else ""),
    ]
    if unknown:
        summary.append(f"- 未识别学号的条目 {len(unknown)} 个：{', '.join(unknown[:20])}"
                       + (" …" if len(unknown) > 20 else ""))
    table = ["", "| 学号 | 姓名 | 文件 | 行数 |", "|---|---|---|---|"]
    for sid, s in students.items():
        names = ", ".join(n for n, _ in s["files"]) or "—"
        lines = sum(len(c.splitlines()) for _, c in s["files"])
        table.append(f"| {sid} | {s['name']} | {names} | {lines} |")
    (out / "index.md").write_text("\n".join(summary + table) + "\n", encoding="utf-8")
    (out / "students.json").write_text(json.dumps({
        "archive": archive.name, "assignment": " / ".join(titles),
        "students": {sid: {"name": s["name"], "submitted": s["submitted"], "notes": s["notes"],
                           "fingerprint": fingerprint(s),
                           "files": [{"name": n, "code": c} for n, c in s["files"]]}
                     for sid, s in students.items()},
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    return "\n".join(summary)


def main(argv=None):
    parser = argparse.ArgumentParser(description="把作业 ZIP 拆成每个学生一份 Markdown")
    parser.add_argument("archive", type=Path)
    parser.add_argument("--out", type=Path, help="输出目录，默认 workspace/<压缩包名>")
    args = parser.parse_args(argv)
    out = args.out or Path("workspace") / args.archive.stem
    try:
        students, unknown = unpack(args.archive)
    except (ValueError, zipfile.BadZipFile) as exc:
        sys.exit(f"解包失败：{exc}")
    print(write(students, unknown, args.archive, out))
    print(f"\n已写入 {out}/index.md 与 {out}/submissions/")


if __name__ == "__main__":
    main()
