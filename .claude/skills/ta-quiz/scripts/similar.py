#!/usr/bin/env python3
"""找出代码雷同的学生对，写入 <ws>/similar.json，供 AI / 雷同线索页使用。

作业很短、写法被题意固定时，两份独立写的代码也会很像，所以光看相似度没有意义。
这里的证据是「两份代码共有、而全班很少有人写过」的行：同样的拼写错误、同样的注释、同样古怪的写法。
只比较同一道题的文件（按内容里的关键词归类，不看文件名）；注释保留，空白压成一个空格。

用法：python similar.py workspace/<作业名> [--min-shared 3] [--rare 3]
  --min-shared  至少共有多少行罕见代码才列出（默认 3）
  --rare        全班最多几人写过这一行才算「罕见」（默认 3）
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
from collections import Counter
from itertools import combinations
from pathlib import Path

TOKEN = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])\'|[A-Za-z_]\w*|\d+\.?\d*|\S', re.S)
MIN_TOKENS = 40          # 太短的程序不比较
MIN_LINE = 8             # 太短的行（`}`、`return 0;`）不算证据


def task_of(code: str) -> str:
    low = code.lower()
    if "celsius" in low or "fahrenheit" in low or "temperature" in low:
        return "temperature"
    if "grade" in low or "letter" in low:
        return "grade"
    if "%p" in low or "long" in low:
        return "calc_long"
    return "other"


def lines_of(code: str) -> dict[str, str]:
    """{去掉所有空白的行: 原样的行}。空格、缩进不同的同一行算同一行，只剩内容上的差别。"""
    out = {}
    for line in code.splitlines():
        key = re.sub(r"\s+", "", line)
        if len(key) >= MIN_LINE:
            out.setdefault(key, re.sub(r"\s+", " ", line).strip())
    return out


def code_tokens(code: str) -> list[str]:
    return [t for t in TOKEN.findall(code) if not t.startswith(("//", "/*"))]


def compare(students: dict, min_shared: int = 3, rare: int = 3) -> list[dict]:
    files = []
    for sid, s in students.items():
        for f in s["files"]:
            toks = code_tokens(f["code"])
            if not f["name"].endswith(".txt") and len(toks) >= MIN_TOKENS:
                files.append((sid, f["name"], task_of(f["code"]), lines_of(f["code"]), toks))
    owners = Counter(key for x in files for key in x[3])
    best = {}
    for x, y in combinations(files, 2):
        if x[0] == y[0] or x[2] != y[2]:
            continue
        shared = [key for key in x[3] if key in y[3] and owners[key] <= rare]
        if len(shared) < min_shared:
            continue
        pair = {"a": x[0], "b": y[0], "task": x[2], "file_a": x[1], "file_b": y[1],
                "ratio": round(difflib.SequenceMatcher(None, x[4], y[4], autojunk=False).ratio(), 2),
                "shared_count": len(shared), "lines_a": len(x[3]), "lines_b": len(y[3]),
                "shared": [{"line": x[3][key], "students": owners[key]} for key in shared[:6]]}
        key = (x[0], y[0], x[2])
        if key not in best or best[key]["shared_count"] < pair["shared_count"]:
            best[key] = pair
    return sorted(best.values(), key=lambda p: (-p["shared_count"], -p["ratio"], p["a"]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--min-shared", type=int, default=3)
    parser.add_argument("--rare", type=int, default=3)
    args = parser.parse_args(argv)
    data = json.loads((args.workspace / "students.json").read_text(encoding="utf-8"))
    pairs = compare(data["students"], args.min_shared, args.rare)
    (args.workspace / "similar.json").write_text(json.dumps(pairs, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"共有 ≥{args.min_shared} 行罕见代码的学生对：{len(pairs)} 组 → {args.workspace / 'similar.json'}")
    for p in pairs[:15]:
        print(f"  {p['a']} ~ {p['b']}  {p['task']:11} 共 {p['shared_count']} 行（共 {p['lines_a']}/{p['lines_b']} 行） 整体 {p['ratio']:.2f}")


if __name__ == "__main__":
    main()
