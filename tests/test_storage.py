"""存储层与答案规范化的单元测试。"""
from __future__ import annotations

import json

import pytest

from app import storage
from app.models import Questionnaire


def _sample_questionnaire(sid: str = "20260003") -> Questionnaire:
    data = json.loads(
        (storage.DEFAULT_ROOT / "tests" / "fixtures_questionnaire.json").read_text(encoding="utf-8")
    )
    data["student_id"] = sid
    return Questionnaire.model_validate(data)


def test_load_assignment_and_submissions():
    assignment = storage.load_assignment()
    assert assignment.id == "c-find-max"
    assert "n >= 1" in assignment.requirements

    submissions = storage.list_submissions()
    assert [s.student_id for s in submissions] == [f"2026000{i}" for i in range(1, 7)]


def test_load_submission_missing_returns_none():
    assert storage.load_submission("99999999") is None


def test_atomic_write_does_not_leave_tmp_files(tmp_path):
    path = tmp_path / "sub" / "x.json"
    storage.save_json_atomic(path, {"a": 1})
    assert json.loads(path.read_text()) == {"a": 1}
    assert list(path.parent.glob("*.tmp")) == []


def test_load_json_corrupt_file_returns_none(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    assert storage.load_json(path) is None


def test_normalize_answers_multiple_choice_forms():
    q = _sample_questionnaire()
    raw = {"q1": "b", "q2": ["c", "a", "a"], "q3": "文本回答"}
    answers = q.normalize_answers(raw)
    assert answers["q1"] == "B"
    assert answers["q2"] == ["A", "C"]
    assert answers["q3"] == "文本回答"


def test_normalize_answers_rejects_unknown_qid():
    q = _sample_questionnaire()
    with pytest.raises(ValueError, match="未知题目编号"):
        q.normalize_answers({"q99": "A"})


def test_normalize_answers_rejects_invalid_letter():
    q = _sample_questionnaire()
    with pytest.raises(ValueError):
        q.normalize_answers({"q1": "Z"})


def test_questionnaire_rejects_wrong_question_count():
    data = json.loads(
        (storage.DEFAULT_ROOT / "tests" / "fixtures_questionnaire.json").read_text(encoding="utf-8")
    )
    data["questions"] = data["questions"][:2]
    with pytest.raises(Exception):
        Questionnaire.model_validate(data)
