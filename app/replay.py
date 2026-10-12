"""离线回放：对台账里的原始输出重跑解析与校验，零 API 成本（LangChain 优化·第二批）。

用途：修改 extract_json / 草稿 Schema / pre_clean 后，对历史真实输出做回归——
不用重新调用模型即可确认修复效果（回放的前提是 raw 已在台账里）。

用法：
  python -m app.replay                          # 回放 {AA_ROOT}/responses/llm_journal.jsonl
  python -m app.replay --journal PATH --kind questionnaire
  python -m app.replay --json report.json       # 另存机器可读报告

指标说明：
  parse_ok        extract_json 能提取到 JSON 对象
  strict_ok       原始输出直接通过草稿 Schema 校验（不经 pre_clean）
  lenient_ok      经 pre_clean 宽松修复后通过草稿 Schema 校验
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from pydantic import ValidationError

from . import journal
from .agent import _pre_clean as clean_questionnaire
from .evaluator import _pre_clean as clean_evaluation
from .llm import extract_json
from .schemas import EvaluationDraft, QuestionnaireDraft

_DRAFTS = {
    "questionnaire": (QuestionnaireDraft, clean_questionnaire),
    "evaluation": (EvaluationDraft, clean_evaluation),
}


def replay_one(entry: dict) -> dict:
    """对单条台账 attempt 记录重跑解析与两级校验，返回指标字典。"""
    kind = str(entry.get("kind", ""))
    result = {
        "kind": kind,
        "ts": entry.get("ts", ""),
        "attempt": entry.get("attempt"),
        "outcome": entry.get("outcome", ""),
        "parse_ok": False,
        "strict_ok": False,
        "lenient_ok": False,
        "errors": [],
    }
    draft_pair = _DRAFTS.get(kind)
    if draft_pair is None:
        result["errors"].append(f"unknown kind: {kind}")
        return result
    draft_cls, cleaner = draft_pair
    raw = str(entry.get("raw", "") or "")
    data = extract_json(raw)
    if data is None:
        result["errors"].append("parse_failed")
        return result
    result["parse_ok"] = True
    try:
        draft_cls.model_validate(data)
        result["strict_ok"] = True
    except ValidationError as e:
        result["errors"].append("strict: " + str(e)[:200])
    try:
        # pre_clean 会原地修改 dict，深拷贝避免污染 strict 结果
        draft_cls.model_validate(cleaner(json.loads(json.dumps(data))))
        result["lenient_ok"] = True
    except ValidationError as e:
        result["errors"].append("lenient: " + str(e)[:200])
    return result


def aggregate(results: list[dict]) -> dict:
    total = len(results)
    def rate(key: str) -> float:
        return round(sum(1 for r in results if r[key]) / total, 4) if total else 0.0
    return {
        "total": total,
        "parse_rate": rate("parse_ok"),
        "strict_rate": rate("strict_ok"),
        "lenient_rate": rate("lenient_ok"),
        "top_errors": Counter(
            e.split("：")[0][:80]
            for r in results for e in r["errors"]
        ).most_common(5),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--journal", default="", help="台账路径，默认 {AA_ROOT}/responses/llm_journal.jsonl")
    ap.add_argument("--kind", choices=["questionnaire", "evaluation", "all"], default="all")
    ap.add_argument("--json", dest="json_out", default="", help="另存机器可读报告")
    args = ap.parse_args()

    entries = journal.read_entries(Path(args.journal) if args.journal else None)
    attempts = [
        e for e in entries
        if e.get("event") == "attempt"
        and (args.kind == "all" or e.get("kind") == args.kind)
    ]
    results = [replay_one(e) for e in attempts]
    summary = aggregate(results)

    print(f"台账回放报告（kind={args.kind}）")
    print(f"  attempt 总数:      {summary['total']}")
    print(f"  解析成功:          {summary['parse_rate']:.1%}")
    print(f"  严格校验通过:      {summary['strict_rate']:.1%}")
    print(f"  宽松(pre_clean)通过: {summary['lenient_rate']:.1%}")
    if summary["top_errors"]:
        print("  高频错误 top5:")
        for err, n in summary["top_errors"]:
            print(f"    [{n}] {err}")
    by_pv = Counter(e.get("prompt_version", "") for e in attempts)
    if len(by_pv) > 1:
        print(f"  prompt_version 分布: {dict(by_pv)}（跨版本回归时关注）")

    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(
            json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  机器可读报告已写入: {args.json_out}")


if __name__ == "__main__":
    main()
