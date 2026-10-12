"""LangChain 优化第一批测试：失败分类退避 / 定点修复 / 超产裁剪 / 能力降级 / Mock 混沌。"""
from __future__ import annotations

import json

import pytest

from app import storage
from app.agent import _pick_questions, generate_questionnaire
from app.llm import (
    LLMEmptyError,
    LLMError,
    LLMProvider,
    LLMTransientError,
    MockLLMProvider,
    OpenAICompatProvider,
    run_structured,
)
from app.models import Questionnaire


def _q(i: int, type_: str = "single_choice", expected: str = "B", issue: str = "index-range") -> dict:
    q = {
        "id": f"q{i}",
        "type": type_,
        "question": f"问题 {i}",
        "target_issue": issue,
        "expected_answer": expected,
    }
    if type_ != "text":
        q["choices"] = ["A 选项", "B 选项", "C 选项"]
    return q


def _payload(questions: list[dict]) -> dict:
    return {
        "student_id": "20260003",
        "assignment_id": "c-find-max",
        "diagnosis": {"summary": "测试", "issues": []},
        "questions": questions,
    }


class ScriptedProvider(LLMProvider):
    """按脚本依次返回输出或抛异常。"""

    name = "scripted"

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.last_response_schema = None

    def generate(self, messages, response_schema=None):
        self.calls += 1
        self.last_response_schema = response_schema
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _clean(d: dict) -> dict:
    d.setdefault("student_id", "20260003")
    d.setdefault("assignment_id", "c-find-max")
    return d


def _gen_with_script(script):
    provider = ScriptedProvider(script)
    assignment = storage.load_assignment()
    submission = next(s for s in storage.list_submissions() if s.student_id == "20260003")
    return provider, generate_questionnaire(provider, assignment, submission)


# ---------------- 失败分类 ----------------

def test_transient_error_backoff_does_not_consume_attempts(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("app.llm.time.sleep", lambda s: sleeps.append(s))
    good = json.dumps(_payload([_q(1), _q(2, "text"), _q(3, "multiple_choice", "A,B")]))
    provider = ScriptedProvider([
        LLMTransientError("429 rate limit"),
        LLMTransientError("connection reset"),
        good,
    ])
    result = run_structured(
        provider, [{"role": "user", "content": "x"}], Questionnaire,
        kind="questionnaire", pre_clean=_clean,
    )
    assert isinstance(result, Questionnaire)
    assert provider.calls == 3
    assert sleeps == [1.0, 2.0]  # 指数退避，且不消耗结构重试次数


def test_transient_retries_exhausted_raises_llm_error(monkeypatch):
    monkeypatch.setattr("app.llm.time.sleep", lambda s: None)
    provider = ScriptedProvider([LLMTransientError("timeout")] * 5)
    with pytest.raises(LLMError, match="暂时性错误"):
        run_structured(
            provider, [{"role": "user", "content": "x"}], Questionnaire,
            kind="questionnaire", max_attempts=3,
        )


def test_empty_content_retries_then_shape_retry():
    good = json.dumps(_payload([_q(1), _q(2, "text"), _q(3, "multiple_choice", "A,B")]))
    provider = ScriptedProvider([LLMEmptyError("思考耗尽 token"), "垃圾", good])
    result = run_structured(
        provider, [{"role": "user", "content": "x"}], Questionnaire,
        kind="questionnaire", pre_clean=_clean,
    )
    assert isinstance(result, Questionnaire)
    assert provider.calls == 3


# ---------------- 定点修复与超产裁剪（端到端走 agent 装配） ----------------

def test_salvage_drops_single_invalid_question_without_retry():
    # 4 道题里 1 道坏（expected_answer=Z）→ 剩 3 道有效，直接装配成功，不重试
    questions = [
        _q(1),
        {"id": "q2", "type": "single_choice", "question": "坏题",
         "choices": ["A", "B"], "target_issue": "x", "expected_answer": "Z"},
        _q(3, "text"),
        _q(4, issue="loop-boundary"),
    ]
    provider, questionnaire = _gen_with_script([json.dumps(_payload(questions))])
    assert len(questionnaire.questions) == 3
    assert provider.calls == 1


def test_micro_repair_fixes_invalid_question_via_extra_call():
    # 3 道题里 1 道坏 → 剩 2 道不足 3 → 定点修复（第 2 次调用）补回，而非整卷重试
    questions = [
        _q(1),
        {"id": "q2", "type": "single_choice", "question": "坏题",
         "choices": ["A", "B"], "target_issue": "x", "expected_answer": "Z"},
        _q(3, "text"),
    ]
    good = json.dumps(_payload(questions), ensure_ascii=False)
    fixed = json.dumps({"questions": [
        {"id": "q2", "type": "single_choice", "question": "修好的题",
         "choices": ["A", "B"], "target_issue": "x", "expected_answer": "A"},
    ]}, ensure_ascii=False)
    provider, questionnaire = _gen_with_script([good, fixed])
    assert len(questionnaire.questions) == 3
    assert provider.calls == 2  # 生成 + 一次定点修复，没有整卷重试


def test_overproduction_trims_to_five_questions():
    questions = [
        _q(1), _q(2, issue="loop-boundary"), _q(3, "text", issue="complexity"),
        _q(4, "multiple_choice", "A,B", issue="off-by-one"), _q(5), _q(6), _q(7),
    ]
    provider, questionnaire = _gen_with_script([json.dumps(_payload(questions))])
    assert len(questionnaire.questions) == 5
    assert provider.calls == 1


def test_pick_questions_prefers_target_issue_diversity():
    valid = [_q(1), _q(2, issue="loop-boundary"), _q(3, issue="index-range"),
             _q(4, issue="complexity"), _q(5), _q(6)]
    picked = _pick_questions(valid, limit=3)
    issues = [q["target_issue"] for q in picked]
    assert issues == ["index-range", "loop-boundary", "complexity"]


def test_draft_schema_passed_to_provider():
    good = json.dumps(_payload([_q(1), _q(2, "text"), _q(3)]))
    provider, _ = _gen_with_script([good])
    schema = provider.last_response_schema["json_schema"]
    assert schema["title"] == "QuestionnaireDraft"
    # 服务器权威字段不得出现在发给模型的 schema 中
    assert set(schema["properties"].keys()) == {"diagnosis", "questions"}


# ---------------- OpenAICompatProvider 能力降级 ----------------

def test_structured_mode_ladder_downgrades_and_learns(monkeypatch):
    from app.llm import _UnsupportedFormatError

    provider = OpenAICompatProvider(api_key="k", base_url="http://x", model="m")
    calls: list[str] = []

    def fake_invoke(lc_messages, schema, mode):
        calls.append(mode)
        if mode in ("json_schema", "json_mode"):
            raise _UnsupportedFormatError("response_format is not supported")
        return '{"ok": true}'

    monkeypatch.setattr(provider, "_invoke", fake_invoke)
    hint = {"kind": "questionnaire", "json_schema": {"title": "T"}}
    assert provider.generate([{"role": "user", "content": "x"}], hint) == '{"ok": true}'
    assert calls == ["json_schema", "json_mode", "legacy"]
    assert provider.structured_mode == "legacy"  # 学习到端点能力
    calls.clear()
    provider.generate([{"role": "user", "content": "y"}], hint)
    assert calls == ["legacy"]  # 下次直接从可用模式开始


def test_pinned_legacy_mode_skips_structured_attempts(monkeypatch):
    monkeypatch.setenv("AA_STRUCTURED_MODE", "legacy")
    provider = OpenAICompatProvider(api_key="k", base_url="http://x", model="m")
    calls: list[str] = []

    def fake_invoke(lc_messages, schema, mode):
        calls.append(mode)
        return '{"ok": true}'

    setattr(provider, "_invoke", fake_invoke)
    provider.generate([{"role": "user", "content": "x"}], {"json_schema": {"title": "T"}})
    assert calls == ["legacy"]  # 固定 legacy 时不尝试任何结构化模式


# ---------------- Mock 混沌注入 + 端到端 ----------------

def _gen_one(monkeypatch, chaos: str):
    monkeypatch.setenv("AA_MOCK_CHAOS", chaos)
    provider = MockLLMProvider()
    assignment = storage.load_assignment()
    submission = next(s for s in storage.list_submissions() if s.student_id == "20260003")
    return generate_questionnaire(provider, assignment, submission), provider


def test_mock_chaos_bad_json_recovers_via_repair(monkeypatch):
    questionnaire, provider = _gen_one(monkeypatch, "bad_json")
    assert len(questionnaire.questions) >= 3
    assert provider._calls == 2


def test_mock_chaos_truncated_json_recovers_via_repair(monkeypatch):
    questionnaire, provider = _gen_one(monkeypatch, "truncate")
    assert len(questionnaire.questions) >= 3
    assert provider._calls == 2


def test_mock_chaos_empty_then_retry_then_success(monkeypatch):
    questionnaire, provider = _gen_one(monkeypatch, "empty")
    assert len(questionnaire.questions) >= 3
    assert provider._calls == 2  # 空内容重试不消耗结构次数，第 2 次正常输出


def test_mock_time_limit_from_case_config(monkeypatch):
    monkeypatch.setenv("AA_MOCK_CHAOS", "")
    questionnaire, _ = _gen_one(monkeypatch, "")
    # 限时来自匹配案例的服务端配置（20 分钟），草稿装配不丢失
    assert questionnaire.time_limit_minutes == 20
