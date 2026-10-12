"""金标准评测：18 案例库跑真实 LLM，量化出题与判分质量（LangChain 优化·第二批）。

对应交付文档的遗留项「真实 LLM 评价质量未做系统评估」：
- 出题腿：每份案例的代码作为提交喂给 generate_questionnaire，统计解析成功率、
  题目数量、双语完备度、code_variant 占比、target_issue 对案例诊断的覆盖率；
- 判分腿：用案例 mastered 档案的预置作答走 evaluate()，统计 knowledge_points
  uncovered 占比与 needs_ta_review（重点暴露 C07-C18 英文 text 题覆盖缺口）；
- 每条案例的原始 LLM 输出固化为 tests/cassettes/{case_id}.json，供
  pytest -m cassette 在无网络环境回放真实解析链路。

用法（需真实 key，见 README）：
  set -a; source .env.local; set +a
  python -m app.eval_harness                     # 全部案例
  python -m app.eval_harness --cases C01,C07     # 指定案例
  python -m app.eval_harness --out docs          # 同时落 Markdown 报告
  python -m app.eval_harness --no-cassette       # 不写盒带
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from app import cases, prompts, storage
from app.agent import generate_questionnaire
from app.evaluator import evaluate
from app.llm import LLMError, LLMProvider, MockLLMProvider, get_provider
from app.models import ResponseFile, Submission, now_utc

CASSETTE_DIR = Path(__file__).resolve().parent.parent / "tests" / "cassettes"


class _RecordingProvider(LLMProvider):
    """包装真实 Provider，录制每次原始输出（盒带）。"""

    def __init__(self, inner: LLMProvider):
        self.inner = inner
        self.name = inner.name
        self.raws: list[str] = []

    def generate(self, messages, response_schema=None):
        raw = self.inner.generate(messages, response_schema)
        self.raws.append(raw)
        return raw


def _case_issue_types(case: cases.MockCase) -> set[str]:
    issues = case.diagnosis.get("issues") if isinstance(case.diagnosis, dict) else []
    return {str(i.get("type", "")) for i in (issues or []) if isinstance(i, dict)}


def eval_case(provider: LLMProvider, assignment, case: cases.MockCase, cassette: bool) -> dict:
    submission = Submission(
        student_id=f"eval-{case.case_id.lower()}",
        assignment_id=assignment.id,
        code=case.code,
    )
    m: dict = {"case_id": case.case_id, "title": case.title}

    # ---- 出题腿 ----
    gen = _RecordingProvider(provider)
    try:
        q = generate_questionnaire(gen, assignment, submission)
    except LLMError as e:
        m.update(parse_ok=False, error=str(e)[:200], generation_raws=gen.raws)
        return m
    choice_qs = [x for x in q.questions if x.choices]
    m.update(
        parse_ok=True,
        n_questions=len(q.questions),
        question_en_ratio=round(
            sum(1 for x in q.questions if x.question_en.strip()) / len(q.questions), 2),
        choices_en_ratio=round(
            sum(1 for x in choice_qs if x.choices_en) / len(choice_qs), 2) if choice_qs else 1.0,
        n_variant=sum(1 for x in q.questions if x.type.value == "code_variant"),
        issue_coverage=round(
            len({x.target_issue for x in q.questions} & _case_issue_types(case))
            / max(1, len(_case_issue_types(case))), 2),
    )

    # ---- 判分腿：案例 mastered 档案作为标准作答 ----
    profile = next((p for p in case.profiles if p.profile_id == "mastered"), None)
    if profile is None:
        m["eval_ok"] = None
    else:
        response = ResponseFile(
            student_id=submission.student_id,
            submitted_at=now_utc(),
            answers=dict(profile.answers),
            attempt_count=1,
        )
        ev = _RecordingProvider(provider)
        try:
            result = evaluate(ev, assignment, submission, q, response)
            m.update(
                eval_ok=True,
                understanding=result.understanding.value,
                needs_ta_review=result.needs_ta_review,
                n_kp=len(result.knowledge_points),
                uncovered=sum(1 for kp in result.knowledge_points if kp.status == "uncovered"),
            )
        except LLMError as e:
            m.update(eval_ok=False, eval_error=str(e)[:200])

    if cassette:
        CASSETTE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "case_id": case.case_id,
            "model": provider.name,
            "prompt_version": prompts.version("questionnaire"),
            "generation_raws": gen.raws,
            "evaluation": (
                {"answers": dict(profile.answers), "raws": ev.raws}
                if profile is not None else None
            ),
        }
        (CASSETTE_DIR / f"{case.case_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    return m


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cases", default="", help="逗号分隔案例 ID（默认全部）")
    ap.add_argument("--out", default="", help="Markdown 报告输出目录（如 docs）")
    ap.add_argument("--json", dest="json_out", default="", help="机器可读指标输出路径")
    ap.add_argument("--no-cassette", action="store_true", help="不写测试盒带")
    ap.add_argument("--allow-mock", action="store_true", help="允许 Mock 模式（仅调试流水线）")
    args = ap.parse_args()

    provider = get_provider()
    if isinstance(provider, MockLLMProvider) and not args.allow_mock:
        raise SystemExit(
            "当前是 Mock 模式，金标评测需要真实 LLM。"
            "请 source .env.local 后重试；调试流水线可加 --allow-mock。"
        )

    wanted = {c.strip().upper() for c in args.cases.split(",") if c.strip()}
    all_cases = [c for c in cases.list_cases() if not wanted or c.case_id.upper() in wanted]
    if not all_cases:
        raise SystemExit(f"未找到案例: {wanted or '(空)'}")

    assignment = storage.load_assignment()
    results = [eval_case(provider, assignment, c, cassette=not args.no_cassette) for c in all_cases]

    gen_ok = [r for r in results if r.get("parse_ok")]
    eval_legs = [r for r in results if r.get("eval_ok")]
    print(f"\n金标评测报告（{len(results)} 案例，provider={provider.name}）")
    print(f"  出题解析成功:      {len(gen_ok)}/{len(results)}")
    if gen_ok:
        n = len(gen_ok)
        print(f"  平均题数:          {sum(r['n_questions'] for r in gen_ok) / n:.1f}")
        print(f"  题干英文完备率:    {sum(r['question_en_ratio'] for r in gen_ok) / n:.0%}")
        print(f"  选项英文完备率:    {sum(r['choices_en_ratio'] for r in gen_ok) / n:.0%}")
        print(f"  案例问题覆盖率:    {sum(r['issue_coverage'] for r in gen_ok) / n:.0%}")
    if eval_legs:
        n = len(eval_legs)
        uncovered = sum(r["uncovered"] for r in eval_legs)
        total_kp = sum(r["n_kp"] for r in eval_legs)
        print(f"  判分腿成功:        {n}/{len(results)}")
        print(f"  knowledge_points:  {total_kp} 条，uncovered {uncovered} 条"
              f"（{uncovered / total_kp:.0%}）" if total_kp else "  knowledge_points:  0 条")
        print(f"  needs_ta_review:   {sum(1 for r in eval_legs if r['needs_ta_review'])}/{n}")
    failed = [r["case_id"] for r in results if not r.get("parse_ok")]
    if failed:
        print(f"  解析失败案例:      {failed}")

    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  指标 JSON: {args.json_out}")
    if args.out:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        report = out_dir / f"eval-report-{date.today().isoformat()}.md"
        lines = [
            f"# 出题/判分金标评测报告（{date.today().isoformat()}）",
            "",
            f"- provider: `{provider.name}`",
            f"- 案例: {len(results)}，出题解析成功 {len(gen_ok)}",
            "",
            "| case | 解析 | 题数 | 题干en | 选项en | 问题覆盖 | 判分 | uncovered | 待复核 |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for r in results:
            lines.append(
                "| {case_id} | {parse} | {nq} | {qe} | {ce} | {ic} | {ev} | {unc} | {ta} |".format(
                    case_id=r["case_id"],
                    parse="✅" if r.get("parse_ok") else "❌",
                    nq=r.get("n_questions", "-"),
                    qe=r.get("question_en_ratio", "-"),
                    ce=r.get("choices_en_ratio", "-"),
                    ic=r.get("issue_coverage", "-"),
                    ev="✅" if r.get("eval_ok") else ("-" if r.get("eval_ok") is None else "❌"),
                    unc=r.get("uncovered", "-"),
                    ta="是" if r.get("needs_ta_review") else "否",
                )
            )
        report.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"  Markdown 报告: {report}")


if __name__ == "__main__":
    main()
