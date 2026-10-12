"""结构化输出可靠性测试：JSON 提取 + 重试修复。"""
from __future__ import annotations

import json

import pytest

from app.llm import (
    LLMError,
    LLMProvider,
    MockLLMProvider,
    extract_json,
    run_structured,
)
from app.models import Questionnaire

VALID_QUESTIONNAIRE = {
    "student_id": "20260003",
    "assignment_id": "c-find-max",
    "diagnosis": {"summary": "测试", "issues": []},
    "questions": [
        {
            "id": "q1",
            "type": "single_choice",
            "question": "长度为 n 的数组合法下标范围是？",
            "choices": ["1 到 n", "0 到 n-1", "0 到 n", "1 到 n-1"],
            "target_issue": "index-range",
            "expected_answer": "B",
        },
        {
            "id": "q2",
            "type": "text",
            "question": "说明问题",
            "choices": None,
            "target_issue": "index-range",
            "expected_answer": "参考要点",
        },
        {
            "id": "q3",
            "type": "multiple_choice",
            "question": "哪些是越界？",
            "choices": ["arr[n]", "arr[0]", "arr[n-1]", "arr[n+1]"],
            "target_issue": "index-range",
            "expected_answer": "A,D",
        },
    ],
}


class FlakyProvider(LLMProvider):
    """第一次输出 markdown 包裹的坏 JSON，第二次输出正常 JSON。"""

    name = "flaky"

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0

    def generate(self, messages, response_schema=None):
        self.calls += 1
        return self.outputs.pop(0)


def test_extract_json_plain():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_markdown_fenced():
    text = '好的，以下是结果：\n```json\n{"a": {"b": 2}}\n```\n希望有帮助'
    assert extract_json(text) == {"a": {"b": 2}}


def test_extract_json_with_prefix_text_and_braces_in_strings():
    text = '分析：该函数中 "{ max }" 有问题。输出：{"code": "if (a > b) { x }", "ok": true}'
    data = extract_json(text)
    assert data == {"code": "if (a > b) { x }", "ok": True}


def test_extract_json_garbage_returns_none():
    assert extract_json("抱歉我无法完成") is None
    assert extract_json("") is None


def test_run_structured_recovers_from_bad_output():
    good = json.dumps(VALID_QUESTIONNAIRE, ensure_ascii=False)
    provider = FlakyProvider(["这不是 JSON：```json\n{broken\n```", "```json\n" + good + "\n```"])
    result = run_structured(
        provider, [{"role": "user", "content": "x"}], Questionnaire, kind="questionnaire"
    )
    assert isinstance(result, Questionnaire)
    assert provider.calls == 2


def test_run_structured_raises_after_max_attempts():
    provider = FlakyProvider(["垃圾输出"] * 5)
    with pytest.raises(LLMError):
        run_structured(
            provider, [{"role": "user", "content": "x"}], Questionnaire,
            kind="questionnaire", max_attempts=3,
        )
    assert provider.calls == 3


def test_run_structured_repairs_duplicate_question_ids():
    data = json.loads(json.dumps(VALID_QUESTIONNAIRE))
    data["questions"][1]["id"] = "q1"  # 重复 id
    provider = FlakyProvider([json.dumps(data, ensure_ascii=False)])
    from app.agent import _pre_clean
    result = run_structured(
        provider, [{"role": "user", "content": "x"}], Questionnaire,
        kind="questionnaire", pre_clean=_pre_clean,
    )
    ids = [q.id for q in result.questions]
    assert len(ids) == len(set(ids)) == 3


def test_mock_provider_diagnosis_and_evaluation_paths():
    provider = MockLLMProvider()
    # 诊断路径
    raw = provider.generate(
        [{"role": "user", "content": "STUDENT_ID: 20260003\n..."}],
        response_schema={"kind": "questionnaire"},
    )
    data = extract_json(raw)
    assert data["student_id"] == "20260003"
    assert data["diagnosis"]["issues"][0]["type"] == "out-of-bounds"
    # 评价路径
    grading = (
        "STUDENT_ID: 20260003\nGRADING_DATA_JSON: "
        + json.dumps([
            {"qid": "q1", "type": "single_choice", "target": "index-range",
             "expected": "B", "answer": "B"},
            {"qid": "q2", "type": "text", "target": "loop-boundary",
             "expected": "最后一次合法访问是 arr[n-1]，i==n 时停止", "answer": ""},
        ])
    )
    raw = provider.generate(
        [{"role": "user", "content": grading}], response_schema={"kind": "evaluation"}
    )
    ev = extract_json(raw)
    assert ev["student_id"] == "20260003"
    assert ev["score"] == 5  # 1 对 1 错
    assert ev["understanding"] == "limited"
    assert ev["needs_ta_review"] is True  # 含 text 题
