#!/usr/bin/env python3
"""检查 papers/<学号>.json 的格式，并生成查询页面 quiz.html（单文件，双击即可打开）。

用法：python build.py workspace/<作业名>
有格式错误时逐条列出并以退出码 1 结束；修好后重跑即可。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

TYPES = {"single", "multi", "text"}
TEMPLATE = Path(__file__).resolve().parent.parent / "assets" / "quiz.html"


def check_refs(refs, files: dict[str, int]) -> tuple[list[str], list[str]]:
    """把 "main.c:7" / "main.c:7-9"（单文件时可只写 7）规范化，并核对行号确实存在。"""
    if not isinstance(refs, list):
        return [], ["lines 必须是列表，如 [\"main.c:7\"]"]
    good, errors = [], []
    for ref in refs:
        name, _, span = str(ref).rpartition(":")
        if not name and len(files) == 1:
            name = next(iter(files))
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", span.strip())
        if name not in files or not m or not 1 <= int(m[1]) <= int(m[2] or m[1]) <= files[name]:
            errors.append(f"行号引用无效：{ref}（可用文件：{', '.join(files)}）")
        else:
            good.append(f"{name}:{span.strip()}")
    return good, errors


def check(paper, student: dict) -> list[str]:
    if not isinstance(paper, dict) or not isinstance(paper.get("questions"), list):
        return ["顶层必须是 {\"fingerprint\", \"summary\", \"questions\": [...]}"]
    questions = paper["questions"]
    errors = [] if 3 <= len(questions) <= 5 else [f"要 3–5 道题，现在 {len(questions)} 道"]
    files = {f["name"]: len(f["code"].splitlines()) for f in student["files"]}
    for i, q in enumerate(questions, 1):
        if not isinstance(q, dict):
            errors.append(f"第 {i} 题不是对象")
            continue
        kind, answer = q.get("type"), str(q.get("answer", "")).strip()
        problems = []
        if kind not in TYPES:
            problems.append(f"type 只能是 single / multi / text，现在是 {kind!r}")
        if not str(q.get("question", "")).strip():
            problems.append("question 为空")
        if kind in ("single", "multi"):
            choices = q.get("choices")
            if not (isinstance(choices, list) and 3 <= len(choices) <= 5
                    and all(isinstance(c, str) and c.strip() for c in choices)):
                problems.append("choices 要 3–5 个非空选项")
            else:
                letters = [x.strip().upper() for x in answer.split(",") if x.strip()]
                valid = "ABCDE"[:len(choices)]
                if not letters or any(x not in valid for x in letters) or len(set(letters)) < len(letters):
                    problems.append(f"answer 要写选项字母（{valid[0]}–{valid[-1]}），现在是 {answer!r}")
                elif kind == "single" and len(letters) != 1:
                    problems.append("单选题 answer 只能有一个字母")
                elif kind == "multi" and len(letters) < 2:
                    problems.append("多选题至少两个正确选项，否则请改成单选")
                else:
                    q["answer"] = ",".join(sorted(letters))
        elif kind == "text" and not answer:
            problems.append("简答题 answer 要写参考要点")
        q["lines"], ref_errors = check_refs(q.get("lines", []), files)
        errors += [f"第 {i} 题 {p}" for p in problems + ref_errors]
    if not any(isinstance(q, dict) and q.get("type") == "text" for q in questions):
        errors.append("至少要有 1 道简答题（让学生解释自己的写法）")
    return errors


def build(root: Path) -> tuple[str, bool]:
    data = json.loads((root / "students.json").read_text(encoding="utf-8"))
    students = data["students"]
    papers, problems, stale = {}, [], []
    for path in sorted((root / "papers").glob("*.json")):
        sid = path.stem
        if sid not in students:
            problems.append(f"{sid}：名单里没有这个学号")
            continue
        try:
            paper = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"{sid}：JSON 解析失败（{exc}）")
            continue
        errors = check(paper, students[sid])
        if errors:
            problems += [f"{sid}：{e}" for e in errors]
            continue
        paper["stale"] = paper.get("fingerprint") != students[sid]["fingerprint"]
        if paper["stale"]:
            stale.append(sid)
        papers[sid] = paper
    payload = json.dumps({**data, "papers": papers}, ensure_ascii=False).replace("<", "\\u003c")
    (root / "quiz.html").write_text(
        TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", payload), encoding="utf-8")
    todo = [sid for sid, s in students.items() if s["files"] and sid not in papers]
    lines = [f"已出题 {len(papers)} / {sum(bool(s['files']) for s in students.values())} 人"
             f" → {root / 'quiz.html'}"]
    if todo:
        lines.append(f"还没出题（或格式有误）：{', '.join(todo)}")
    if stale:
        lines.append(f"代码已变、题目可能过期：{', '.join(stale)}")
    if problems:
        lines += ["格式错误，请修正对应 papers/<学号>.json 后重跑："] + [f"  - {p}" for p in problems]
    return "\n".join(lines), not problems


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        sys.exit("用法：python build.py workspace/<作业名>")
    report, ok = build(Path(args[0]))
    print(report)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
