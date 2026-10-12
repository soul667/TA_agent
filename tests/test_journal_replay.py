"""第二批测试：LLM 台账、prompt 注册表、离线回放、provenance 盖章。"""
from __future__ import annotations

import json

import pytest

from app import journal, prompts, storage
from app.agent import generate_questionnaire
from app.evaluator import evaluate
from app.llm import MockLLMProvider
from app.models import ResponseFile, now_utc
from app.replay import aggregate, replay_one


def _gen_one():
    provider = MockLLMProvider()
    assignment = storage.load_assignment()
    submission = next(s for s in storage.list_submissions() if s.student_id == "20260003")
    return generate_questionnaire(provider, assignment, submission)


# ---------------- prompt 注册表 ----------------

def test_prompt_version_is_content_hash_and_stable():
    v1 = prompts.version("questionnaire")
    v2 = prompts.version("questionnaire")
    assert v1 == v2 and v1.startswith("pv-") and len(v1) == 15
    assert prompts.version("evaluation") != v1


def test_agent_and_evaluator_use_registry_prompts():
    from app.agent import SYSTEM_PROMPT as AGENT_PROMPT
    from app.evaluator import SYSTEM_PROMPT as EVAL_PROMPT

    assert AGENT_PROMPT == prompts.get_prompt("questionnaire")
    assert EVAL_PROMPT == prompts.get_prompt("evaluation")
    assert "6 到 7 道问题" in AGENT_PROMPT  # 超产要求在注册表文件里，而非内联字符串


# ---------------- provenance 盖章 ----------------

def test_questionnaire_and_evaluation_carry_prompt_version():
    q = _gen_one()
    assert q.prompt_version == prompts.version("questionnaire")
    provider = MockLLMProvider()
    assignment = storage.load_assignment()
    submission = next(s for s in storage.list_submissions() if s.student_id == "20260003")
    response = ResponseFile(
        student_id=submission.student_id,
        submitted_at=now_utc(),
        answers={x.id: x.expected_answer for x in q.questions},
        attempt_count=1,
    )
    ev = evaluate(provider, assignment, submission, q, response)
    assert ev.prompt_version == prompts.version("evaluation")


# ---------------- 台账 ----------------

def test_journal_records_attempts_with_raw_and_prompt_version():
    _gen_one()
    path = journal.journal_path()
    assert path.exists()
    entries = journal.read_entries()
    attempts = [e for e in entries if e.get("event") == "attempt"]
    assert attempts, "至少应有一条 attempt 台账"
    entry = attempts[0]
    assert entry["kind"] == "questionnaire"
    assert entry["provider"] == "mock"
    assert entry["prompt_version"].startswith("pv-")
    assert entry["outcome"] in ("ok", "invalid", "parse_failed")
    assert entry["latency_ms"] >= 0
    assert entry["raw"]  # 原始输出被保留（回放的前提）
    assert isinstance(entry["convo"], list) and entry["convo"]


def test_journal_can_be_disabled(monkeypatch):
    monkeypatch.setenv("AA_JOURNAL", "0")
    assert not journal.enabled()
    journal.record({"event": "noop"})
    assert not journal.journal_path().exists()


def test_journal_survives_malformed_lines():
    path = journal.journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    good = json.dumps({"event": "attempt", "kind": "questionnaire", "raw": "{}"})
    path.write_text(good + "\n{broken半行\n", encoding="utf-8")
    entries = journal.read_entries()
    assert len(entries) == 1


# ---------------- 离线回放 ----------------

def test_replay_one_reports_parse_and_two_tier_validation():
    good_entry = {
        "event": "attempt", "kind": "questionnaire", "raw": json.dumps({
            "diagnosis": {"summary": "x", "issues": []},
            "questions": [{
                "id": "q1", "type": "single_choice", "question": "?",
                "choices": ["A", "B"], "target_issue": "t", "expected_answer": "A",
            }, {
                "id": "q2", "type": "text", "question": "?",
                "target_issue": "t", "expected_answer": "要点",
            }, {
                "id": "q3", "type": "text", "question": "?",
                "target_issue": "t", "expected_answer": "要点",
            }],
        }, ensure_ascii=False),
    }
    r = replay_one(good_entry)
    assert r["parse_ok"] and r["strict_ok"] and r["lenient_ok"]

    # 需要 pre_clean 才能过的输出：宽松通过、严格失败（severity 非法值由 pre_clean 兜底）
    dirty = json.loads(good_entry["raw"])
    dirty["diagnosis"]["issues"] = [{"type": "x", "description": "y", "severity": "critical"}]
    dirty_entry = {**good_entry, "raw": json.dumps(dirty, ensure_ascii=False)}
    r2 = replay_one(dirty_entry)
    assert r2["parse_ok"] and not r2["strict_ok"] and r2["lenient_ok"]

    garbage = replay_one({**good_entry, "raw": "抱歉无法输出"})
    assert not garbage["parse_ok"] and garbage["errors"] == ["parse_failed"]

    summary = aggregate([r, r2, garbage])
    assert summary["total"] == 3
    assert summary["parse_rate"] == round(2 / 3, 4)
    assert summary["lenient_rate"] == round(2 / 3, 4)
    assert summary["strict_rate"] == 0.3333


def test_replay_cli_runs_against_journal(tmp_path, capsys):
    _gen_one()  # 产生真实台账（Mock 路径）
    from app.replay import main

    import sys
    out_json = tmp_path / "report.json"
    argv = ["replay", "--kind", "questionnaire", "--json", str(out_json)]
    old = sys.argv
    sys.argv = argv
    try:
        main()
    finally:
        sys.argv = old
    captured = capsys.readouterr()
    assert "台账回放报告" in captured.out
    report = json.loads(out_json.read_text(encoding="utf-8"))
    assert report["summary"]["total"] >= 1
    assert report["results"]
