#!/usr/bin/env python3
"""检查 papers/<学号>.json 与 reviews/<学号>.json 的格式，生成两个单文件页面（双击即可打开）：
  quiz.html    按学号查看个性化抽查题
  review.html  AI 辅助线索排查页（有 reviews/ 时才生成；families.json、similar.json 存在时一并展示）

用法：python build.py workspace/<作业名>
有格式错误时逐条列出并以退出码 1 结束；修好后重跑即可。
"""
from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path

TYPES = {"single", "multi", "text"}
PRIORITIES = {"normal", "follow_up", "verify"}
KINDS = {"disclosed", "rule", "style", "similar", "mismatch"}
ASSETS = Path(__file__).resolve().parent.parent / "assets"


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


def check_review(review, student: dict) -> list[str]:
    if not isinstance(review, dict) or review.get("priority") not in PRIORITIES:
        return [f"priority 只能是 {' / '.join(sorted(PRIORITIES))}"]
    signals = review.get("signals", [])
    if not isinstance(signals, list):
        return ["signals 必须是列表"]
    errors = []
    if not str(review.get("summary", "")).strip():
        errors.append("summary 为空")
    if review["priority"] != "normal" and not signals:
        errors.append("提高追问优先级必须给出具体的代码线索（signals 不能为空）")
    files = {f["name"]: len(f["code"].splitlines()) for f in student["files"]}
    for i, sg in enumerate(signals, 1):
        if not isinstance(sg, dict):
            errors.append(f"线索 {i} 不是对象")
            continue
        if sg.get("kind") not in KINDS:
            errors.append(f"线索 {i} 的 kind 只能是 {' / '.join(sorted(KINDS))}")
        for key in ("observation", "ask"):
            if not str(sg.get(key, "")).strip():
                errors.append(f"线索 {i} 的 {key} 为空")
        refs, ref_errors = check_refs([sg.get("ref", "")], files)
        sg["ref"] = refs[0] if refs else ""
        errors += [f"线索 {i} {e}" for e in ref_errors]
    return errors


def check_families(families, students: dict) -> list[str]:
    """families.json：多人共有的做法 [{id, title, strength, note, members, example?}]，学号必须真实存在。"""
    if not isinstance(families, list):
        return ["families.json 必须是列表"]
    errors = []
    for i, fam in enumerate(families, 1):
        if not isinstance(fam, dict) or not all(str(fam.get(k, "")).strip() for k in ("title", "note")):
            errors.append(f"families.json 第 {i} 项缺少 title 或 note")
            continue
        if fam.get("strength") not in ("strong", "medium", "weak"):
            errors.append(f"families.json 第 {i} 项的 strength 只能是 strong / medium / weak")
        unknown = [m for m in fam.get("members", []) if m not in students]
        if unknown or not fam.get("members"):
            errors.append(f"families.json 第 {i} 项的 members 为空或含有名单里没有的学号：{', '.join(unknown)}")
    return errors


def load_dir(root: Path, folder: str, students: dict, checker) -> tuple[dict, list[str], list[str]]:
    """读取 <folder>/<学号>.json 并逐份检查，返回 (通过的, 错误, 代码已变的学号)。"""
    good, problems, stale = {}, [], []
    for path in sorted((root / folder).glob("*.json")):
        sid = path.stem
        if sid not in students:
            problems.append(f"{folder}/{sid}：名单里没有这个学号")
            continue
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"{folder}/{sid}：JSON 解析失败（{exc}）")
            continue
        errors = checker(item, students[sid])
        if errors:
            problems += [f"{folder}/{sid}：{e}" for e in errors]
            continue
        item["stale"] = item.get("fingerprint") != students[sid]["fingerprint"]
        if item["stale"]:
            stale.append(sid)
        good[sid] = item
    return good, problems, stale


FONTS = {"TASans-Regular": ("TA Sans", 400), "TASans-Bold": ("TA Sans", 700),
         "TAMono-Regular": ("TA Mono", 400), "TAMono-Medium": ("TA Mono", 500), "TAMono-Bold": ("TA Mono", 700)}


def fonts_css() -> str:
    """把 assets/fonts 里的字体子集以 data: URI 写进页面，在任何电脑、任何查看器里字形一致（离线也行）。"""
    rules = []
    for stem, (family, weight) in FONTS.items():
        path = ASSETS / "fonts" / f"{stem}.woff2"
        if path.exists():
            b64 = base64.b64encode(path.read_bytes()).decode("ascii")
            rules.append(f'@font-face{{font-family:"{family}";font-weight:{weight};font-style:normal;font-display:swap;'
                         f'src:url(data:font/woff2;base64,{b64}) format("woff2")}}')
    return "\n".join(rules)


def render(template: str, payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    page = (ASSETS / template).read_text(encoding="utf-8")
    for marker, text in (("/*__FONTS__*/", fonts_css()),
                         ("/*__SHARED_CSS__*/", (ASSETS / "shared.css").read_text(encoding="utf-8")),
                         ("//__SHARED_JS__", (ASSETS / "shared.js").read_text(encoding="utf-8"))):
        page = page.replace(marker, text)
    return page.replace("__DATA__", data)    # 数据最后替换，学生代码里出现什么字样都不会被当成占位符


def build(root: Path) -> tuple[str, bool]:
    data = json.loads((root / "students.json").read_text(encoding="utf-8"))
    students = data["students"]
    papers, problems, stale = load_dir(root, "papers", students, check)
    (root / "quiz.html").write_text(render("quiz.html", {**data, "papers": papers, "has_review": (root / "reviews").is_dir()}),
                                    encoding="utf-8")
    todo = [sid for sid, s in students.items() if s["files"] and sid not in papers]
    lines = [f"已出题 {len(papers)} / {sum(bool(s['files']) for s in students.values())} 人"
             f" → {root / 'quiz.html'}"]
    if todo:
        lines.append(f"还没出题（或格式有误）：{', '.join(todo)}")
    if stale:
        lines.append(f"代码已变、题目可能过期：{', '.join(stale)}")
    letters = [q["answer"] for p in papers.values() for q in p["questions"] if q["type"] == "single"]
    if len(letters) >= 8 and max(letters.count(x) for x in set(letters)) > len(letters) * 0.5:
        top = max(set(letters), key=letters.count)
        lines.append(f"提醒：{letters.count(top)} / {len(letters)} 道单选题的答案都是 {top}，请把正确选项的位置打乱，免得一猜就中")
    if (root / "reviews").is_dir():
        reviews, review_problems, review_stale = load_dir(root, "reviews", students, check_review)
        problems += review_problems
        similar_path = root / "similar.json"
        similar = json.loads(similar_path.read_text(encoding="utf-8")) if similar_path.exists() else []
        families_path = root / "families.json"
        families = json.loads(families_path.read_text(encoding="utf-8")) if families_path.exists() else []
        problems += check_families(families, students)
        (root / "review.html").write_text(render("review.html", {
            "archive": data["archive"], "assignment": data["assignment"], "reviews": reviews, "similar": similar,
            "families": families,
            "students": {sid: {"name": s["name"], "files": s["files"], "has_paper": sid in papers}
                         for sid, s in students.items()}}), encoding="utf-8")
        counts = {k: sum(r["priority"] == k for r in reviews.values()) for k in PRIORITIES}
        lines.append(f"已评审 {len(reviews)} 人：需核实 {counts['verify']}、建议追问 {counts['follow_up']}、无线索 "
                     f"{counts['normal']}；雷同 {len(similar)} 组 → {root / 'review.html'}")
        unreviewed = [sid for sid, s in students.items() if s["files"] and sid not in reviews]
        if unreviewed:
            lines.append(f"还没评审（或格式有误）：{', '.join(unreviewed)}")
        if review_stale:
            lines.append(f"代码已变、评审可能过期：{', '.join(review_stale)}")
    if problems:
        lines += ["格式错误，请修正对应 JSON 后重跑："] + [f"  - {p}" for p in problems]
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
