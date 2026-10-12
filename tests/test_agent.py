"""诊断 Agent 与 Mock Provider 的测试（对应验收 Test 1/2/3）。"""
from __future__ import annotations

import json

from app import storage
from app.agent import generate_questionnaire
from app.llm import MockLLMProvider


def generate_all() -> dict:
    provider = MockLLMProvider()
    assignment = storage.load_assignment()
    out = {}
    for submission in storage.list_submissions():
        q = generate_questionnaire(provider, assignment, submission)
        storage.save_questionnaire(q)
        out[submission.student_id] = q
    return out


def test_all_six_students_generate_successfully():
    questionnaires = generate_all()
    assert len(questionnaires) == 6
    assert all(len(q.questions) >= 3 for q in questionnaires.values())


def test_every_questionnaire_has_3_to_5_questions():
    for q in generate_all().values():
        assert 3 <= len(q.questions) <= 5, q.student_id


def test_questionnaires_are_persisted_as_expected_files():
    generate_all()
    for i in range(1, 7):
        path = storage.generated_dir() / f"2026000{i}.json"
        assert path.exists(), path
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["student_id"] == f"2026000{i}"
        assert 3 <= len(data["questions"]) <= 5


def _texts(q):
    return {item.question for item in q.questions}


def test_questionnaires_differ_between_students():
    qs = generate_all()
    pairs = [
        ("20260002", "20260003"),
        ("20260003", "20260004"),
        ("20260002", "20260004"),
        ("20260001", "20260002"),
        ("20260005", "20260006"),
    ]
    for a, b in pairs:
        assert _texts(qs[a]).isdisjoint(_texts(qs[b])), f"{a} 与 {b} 的问卷重复了"


def test_questionnaire_focus_matches_each_students_real_error():
    qs = generate_all()
    all_text = {sid: " ".join(t.question for t in q.questions) for sid, q in qs.items()}
    # 20260002: 负数 / 初始化
    assert "负" in all_text["20260002"] or "初始" in all_text["20260002"]
    # 20260003: 下标 / 越界
    assert "下标" in all_text["20260003"] or "越界" in all_text["20260003"]
    # 20260004: 比较方向 / 不变量 / 最小值
    assert any(k in all_text["20260004"] for k in ("不变量", "最小值", "比较"))
    # 20260001: 确认理解——复杂度 / i=1，而不是基础边界题
    assert any(k in all_text["20260001"] for k in ("复杂度", "i = 1", "i=1", "n >= 1"))
    # 20260005: 遍历完整性
    assert any(k in all_text["20260005"] for k in ("遍历", "逐一", "循环", "比较过"))
    # 20260006: 返回机制
    assert "return" in all_text["20260006"] or "返回" in all_text["20260006"]


def test_20260006_diagnosis_does_not_misclassify():
    qs = generate_all()
    diag = qs["20260006"].diagnosis
    summary = diag.summary
    # 必须区分“计算逻辑正确”与“没有返回”，不能说成不会找最大值
    assert "正确" in summary and ("return" in summary or "返回" in summary)
    assert diag.issues[0].type == "missing-return"


def test_public_view_does_not_leak_internal_fields():
    qs = generate_all()
    for q in qs.values():
        dumped = json.dumps(q.public(), ensure_ascii=False)
        assert "expected_answer" not in dumped
        assert "diagnosis" not in dumped
        assert "target_issue" not in dumped


def test_unknown_student_gets_generic_questionnaire():
    provider = MockLLMProvider()
    assignment = storage.load_assignment()
    submission = storage.load_submission("20260001").model_copy(update={"student_id": "20999999"})
    q = generate_questionnaire(provider, assignment, submission)
    assert q.student_id == "20999999"
    assert 3 <= len(q.questions) <= 5
