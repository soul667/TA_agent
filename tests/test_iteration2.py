"""本期迭代回归：中英双语反馈、代码变异题、重新作答预填数据、限时字段。

对应 2026-09-12 第二批需求的可测部分：
- 需求1：判分反馈双语（feedback_en 与 feedback 计数一致、remaining_issues_en 对应）；
- 需求6：code_variant 题按 exact-choice 判分，public 视图含 variant 而不含答案；
- 需求7：result 接口的 attempts 带本人 answers（供前端预填）；
- 需求8：提交可携带 time_expired / duration_seconds 并落盘。
"""
from __future__ import annotations

import json

import pytest

from app import cases
from app.agent import generate_questionnaire
from app.evaluator import evaluate
from app.llm import MockLLMProvider
from app.main import app
from app.models import ResponseFile, Submission, now_utc

from conftest import TeacherClient
from test_api import generated  # noqa: F401 复用生成问卷的 fixture


@pytest.fixture()
def client():
    return TeacherClient(app)


def _submission(code: str, sid: str):
    return Submission(
        student_id=sid, assignment_id="c-find-max", code=code, language="c",
        submitted_at="2026-09-12T00:00:00",
    )


def _evaluate_profile(case, sid, profile):
    provider = MockLLMProvider()
    assignment = storage_load_assignment()
    questionnaire = generate_questionnaire(provider, assignment, _submission(case.code, sid))
    answers = questionnaire.normalize_answers(profile.answers)
    response = ResponseFile(student_id=sid, submitted_at=now_utc(), answers=answers,
                            attempt_count=1)
    return evaluate(provider, assignment, _submission(case.code, sid), questionnaire, response)


def storage_load_assignment():
    from app import storage
    return storage.load_assignment()


# ---------------- 需求1：双语反馈 ----------------

def test_feedback_is_bilingual_with_matching_counts():
    case = cases.load_case("C01")
    profile = next(p for p in case.profiles if p.profile_id == "mastered")
    ev = _evaluate_profile(case, "94000001", profile)

    assert ev.feedback_en, "mastered 档必须有英文反馈"
    assert "5 questions: 5 correct" in ev.feedback_en
    assert "答对 5 题" in ev.feedback
    assert ev.remaining_issues == [] and ev.remaining_issues_en == []
    # 知识点双语名称
    assert all(kp.point_zh for kp in ev.knowledge_points)


def test_feedback_en_flags_uncovered_answers():
    case = cases.load_case("C03")
    profile = next(p for p in case.profiles if p.profile_id == "contradictory")
    ev = _evaluate_profile(case, "94000002", profile)

    assert ev.needs_ta_review is True
    assert ev.remaining_issues_en, "待核验知识点必须有英文列表"
    assert "manual review" in ev.feedback_en or "revisit" in ev.feedback_en


def test_all_samples_have_english_notes():
    """样例注释经真实 LLM 批量翻译落库后，每个样例都应有 note_en。"""
    for case in cases.list_cases():
        for q in case.questions:
            for sample in q.samples:
                assert sample.note_en, f"{case.case_id}/{q.id} 样例缺少英文注释"


# ---------------- 需求6：代码变异题 ----------------

def test_code_variant_question_grading_and_public_view():
    case = cases.load_case("C01")
    q5 = next(q for q in case.questions if q.id == "q5")
    assert q5.type == "code_variant"
    assert q5.variant is not None and q5.variant["line_no"] == 3

    # 判分走 exact-choice
    provider = MockLLMProvider()
    assignment = storage_load_assignment()
    questionnaire = generate_questionnaire(provider, assignment, _submission(case.code, "93000001"))
    answers = questionnaire.normalize_answers({"q5": case.questions[-1].expected_answer})
    response = ResponseFile(student_id="93000001", submitted_at=now_utc(),
                            answers=answers, attempt_count=1)
    ev = evaluate(provider, assignment, _submission(case.code, "93000001"),
                  questionnaire, response)
    kp = {k.point: k for k in ev.knowledge_points}["equivalent-implementation"]
    assert kp.status == "mastered"
    assert "exact-choice" in kp.basis

    # public 视图：含 variant（学生可见的假设修改），不含答案
    public = questionnaire.questions[-1].public()
    assert public["variant"] == {"line_no": 3, "replacement": q5.variant["replacement"]}
    dumped = json.dumps(public)
    assert "expected_answer" not in dumped and q5.expected_answer not in dumped


def test_every_case_has_a_code_variant_question():
    for case in cases.list_cases():
        q5s = [q for q in case.questions if q.type == "code_variant"]
        assert len(q5s) == 1, f"{case.case_id} 应恰有 1 道变异题"
        q = q5s[0]
        assert q.variant and 1 <= q.variant["line_no"] <= len(case.code.split("\n"))
        assert q.question_en and q.choices_en and q.target_en


# ---------------- 需求7/8：预填数据与限时字段（API 层） ----------------

def test_result_attempts_carry_answers_for_prefill(client, generated):  # noqa: F811
    answers = {"q1": "B", "q2": "B", "q3": "B",
               "q4": "最后一次合法访问是 arr[n-1]；当 i == n 时循环必须停止，因为 arr[n] 不属于数组。",
               "q5": "C"}
    client.post("/api/exam/20260003/submit", json={"answers": answers})
    res = client.get("/api/exam/20260003/result")
    assert res.status_code == 200
    attempts = res.json()["attempts"]
    assert attempts and attempts[-1]["answers"] == answers


def test_submit_records_timer_fields(client, generated):  # noqa: F811
    answers = {"q1": "B", "q2": "B", "q3": "B", "q4": "", "q5": "B"}
    res = client.post("/api/exam/20260003/submit", json={
        "answers": answers, "time_expired": True, "duration_seconds": 1205,
    })
    assert res.status_code == 200
    result = client.get("/api/exam/20260003/result").json()
    latest = result["latest_attempt"]
    assert latest["time_expired"] is True
    detail = client.get("/api/teacher/students/20260003").json()
    stored = detail["response"]["attempts"][-1]
    assert stored["time_expired"] is True
    assert stored["duration_seconds"] == 1205


# ---------------- 访问码功能开关（AA_ACCESS_CODE_ENABLED） ----------------

def test_access_code_disabled_allows_student_endpoints(client, generated, monkeypatch):  # noqa: F811
    """开关显式关闭（AA_ACCESS_CODE_ENABLED=0，演示模式）：学生端仅凭学号访问。
    安全修复 R1 后默认开启，此处必须显式设 0。"""
    from app.main import app
    from conftest import TestClient

    monkeypatch.setenv("AA_ACCESS_CODE_ENABLED", "0")
    plain = TestClient(app)
    assert plain.get("/api/exam/20260003").status_code == 200
    answers = {"q1": "B", "q2": "B", "q3": "B", "q4": "", "q5": "B"}
    res = plain.post("/api/exam/20260003/submit", json={"answers": answers})
    assert res.status_code == 200
    assert plain.get("/api/exam/20260003/result").status_code == 200
    # 教师端不受开关影响：仍需教师口令
    assert plain.get("/api/teacher/overview").status_code == 401
    # meta 暴露开关状态
    assert plain.get("/api/meta").json()["access_code_enabled"] is False


def test_access_code_enabled_by_default(client, generated, monkeypatch):  # noqa: F811
    """安全修复 R1：不设置环境变量时访问码默认开启（安全默认）。"""
    from app.main import app
    from conftest import TestClient

    monkeypatch.delenv("AA_ACCESS_CODE_ENABLED", raising=False)
    plain = TestClient(app)
    assert plain.get("/api/exam/20260003").status_code == 401
    assert plain.get("/api/meta").json()["access_code_enabled"] is True


def test_access_code_enabled_keeps_old_behavior(client, generated, monkeypatch):  # noqa: F811
    """开关开启：无凭据 401、错误访问码 403（原 docs §6 行为）。"""
    from app.main import app
    from conftest import TestClient

    monkeypatch.setenv("AA_ACCESS_CODE_ENABLED", "1")
    plain = TestClient(app)
    assert plain.get("/api/exam/20260003").status_code == 401
    wrong = TestClient(app)
    wrong.headers.update({"Authorization": "Bearer wrong-code"})
    assert wrong.get("/api/exam/20260003").status_code == 403
    assert plain.get("/api/meta").json()["access_code_enabled"] is True
