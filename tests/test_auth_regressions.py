"""最小身份认证回归（审批 P0-1：未认证不得读取教师详情与参考答案）。

用**无凭据**与**他人凭据**的客户端验证：
- 未认证 → 401（教师端与学生端全部接口）；
- 教师口令错误 → 403；
- 学生 A 的访问码访问学生 B → 403（归属校验）；
- 学生访问码不能进入教师端；
- 本人访问码可正常答题（正常路径不受影响）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import storage
from app.agent import generate_questionnaire
from app.llm import MockLLMProvider
from app.main import app
from conftest import student_token

GOOD_ANSWERS_20260003 = {
    "q1": "B",
    "q2": "B",
    "q3": "B",
    "q4": "最后一次合法访问是 arr[n-1]；当 i == n 时循环必须停止，因为 arr[n] 不属于数组。",
}


@pytest.fixture()
def plain():
    return TestClient(app)


@pytest.fixture()
def generated():
    provider = MockLLMProvider()
    assignment = storage.load_assignment()
    for submission in storage.list_submissions():
        storage.save_questionnaire(
            generate_questionnaire(provider, assignment, submission)
        )
    return assignment


def test_teacher_endpoints_reject_unauthenticated(plain, generated):
    # 审批 P0-1 复现场景：未认证读教师详情曾经返回 200 + expected_answer
    get_paths = (
        "/api/teacher/overview",
        "/api/teacher/students/20260003",
    )
    post_paths = (
        "/api/teacher/students/20260003/evaluate",
        "/api/teacher/students/20260003/regenerate",
        "/api/teacher/students/20260003/review",
    )
    for path in get_paths:
        res = plain.get(path)
        assert res.status_code == 401, f"{path}: {res.status_code}"
        assert "expected_answer" not in res.text
    for path in post_paths:
        # review 携带空 body 以便通过请求体校验，验证认证先于业务执行
        res = plain.post(path, json={}) if path.endswith("/review") else plain.post(path)
        assert res.status_code == 401, f"{path}: {res.status_code}"
        assert "expected_answer" not in res.text


def test_teacher_wrong_token_403(plain, generated):
    res = plain.get(
        "/api/teacher/students/20260003",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert res.status_code == 403


def test_student_endpoints_reject_unauthenticated(plain, generated):
    assert plain.get("/api/exam/20260003").status_code == 401
    assert plain.post(
        "/api/exam/20260003/submit", json={"answers": GOOD_ANSWERS_20260003}
    ).status_code == 401
    assert plain.get("/api/exam/20260003/result").status_code == 401
    assert plain.post("/api/exam/20260003/evaluate").status_code == 401


def test_student_token_cannot_access_other_student(plain, generated):
    """归属校验：20260002 的访问码不能查看/提交/查询 20260003。"""
    headers = {"Authorization": f"Bearer {student_token('20260002')}"}
    assert plain.get("/api/exam/20260003", headers=headers).status_code == 403
    assert plain.post(
        "/api/exam/20260003/submit", json={"answers": GOOD_ANSWERS_20260003},
        headers=headers,
    ).status_code == 403
    assert plain.get("/api/exam/20260003/result", headers=headers).status_code == 403
    assert plain.post("/api/exam/20260003/evaluate", headers=headers).status_code == 403


def test_student_token_cannot_access_teacher_apis(plain, generated):
    headers = {"Authorization": f"Bearer {student_token('20260003')}"}
    assert plain.get("/api/teacher/overview", headers=headers).status_code == 403
    assert plain.get(
        "/api/teacher/students/20260003", headers=headers
    ).status_code == 403  # 学生的码不是教师口令


def test_own_token_full_student_flow(plain, generated, await_evaluation):
    headers = {"Authorization": f"Bearer {student_token('20260003')}"}
    res = plain.get("/api/exam/20260003", headers=headers)
    assert res.status_code == 200
    assert "expected_answer" not in res.text

    res = plain.post(
        "/api/exam/20260003/submit", json={"answers": GOOD_ANSWERS_20260003},
        headers=headers,
    )
    assert res.status_code == 200
    assert res.json()["evaluation_status"] == "pending"
    assert await_evaluation("20260003") == "ok"

    res = plain.get("/api/exam/20260003/result", headers=headers)
    assert res.status_code == 200
    assert res.json()["state"] in ("feedback_ready", "awaiting_review")
