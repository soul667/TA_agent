"""FastAPI 端到端测试：生成 -> 学生作答 -> 评价 -> 教师查看 的完整闭环。

适配最小认证（docs/evaluation-rules.md §6）：本文件的客户端统一以教师口令访问
（教师可访问全部学生数据）；未认证 / 归属不符的拒绝行为见 test_auth_regressions.py。
"""
from __future__ import annotations

import json

import pytest

from app import storage
from app.agent import generate_questionnaire
from app.llm import MockLLMProvider
from app.main import app
from conftest import TeacherClient


@pytest.fixture()
def client():
    return TeacherClient(app)


@pytest.fixture()
def generated():
    provider = MockLLMProvider()
    assignment = storage.load_assignment()
    for submission in storage.list_submissions():
        storage.save_questionnaire(
            generate_questionnaire(provider, assignment, submission)
        )
    return assignment


GOOD_ANSWERS_20260003 = {
    "q1": "B",
    "q2": "B",
    "q3": "B",
    "q4": "最后一次合法访问是 arr[n-1]；当 i == n 时循环必须停止，因为 arr[n] 不属于数组。",
}


def test_pages_served(client):
    for path, marker in [
        ("/", "个性化诊断测试"),
        ("/exam", "问卷"),
        ("/teacher", "教师"),
    ]:
        res = client.get(path)
        assert res.status_code == 200
        assert marker in res.text
    assert client.get("/static/style.css").status_code == 200


def test_get_exam_sanitized_and_complete(client, generated):
    res = client.get("/api/exam/20260003")
    assert res.status_code == 200
    data = res.json()
    assert data["student_id"] == "20260003"
    assert len(data["questions"]) == 5
    dumped = json.dumps(data, ensure_ascii=False)
    assert "expected_answer" not in dumped
    assert "diagnosis" not in dumped
    assert "target_issue" not in dumped
    assert data["course"] == "C语言程序设计"
    # 双语 + 变异题 + 限时（本期新增字段）
    assert data["questions"][0]["question_en"]
    assert data["questions"][4]["type"] == "code_variant"
    assert data["questions"][4]["variant"]["line_no"] == 4
    assert data["time_limit_minutes"] == 20


def test_get_exam_unknown_student_404(client, generated):
    res = client.get("/api/exam/99999999")
    assert res.status_code == 404
    assert "不存在" in res.json()["detail"]


def test_get_exam_not_generated_404_not_500(client):
    # generated/ 为空：提交存在但问卷未生成
    res = client.get("/api/exam/20260003")
    assert res.status_code == 404
    assert "尚未生成" in res.json()["detail"]


def test_get_exam_bad_sid_format_400(client):
    res = client.get("/api/exam/abc")
    assert res.status_code == 400
    res = client.get("/api/exam/../etc")
    assert res.status_code in (400, 404)


def test_submit_creates_response_and_evaluation(client, generated, await_evaluation):
    res = client.post(
        "/api/exam/20260003/submit", json={"answers": GOOD_ANSWERS_20260003}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "submitted"
    assert body["attempt"] == 1
    # 评价异步化：提交立即返回 pending 回执，评价在后台完成后落盘
    assert body["evaluation_status"] == "pending"
    assert await_evaluation("20260003") == "ok"

    # Test 5: responses/20260003.json 产生
    resp_file = storage.responses_dir() / "20260003.json"
    assert resp_file.exists()
    saved = json.loads(resp_file.read_text(encoding="utf-8"))
    assert saved["student_id"] == "20260003"
    assert saved["answers"]["q1"] == "B"
    assert len(saved["attempts"]) == 1

    # Test 6: evaluations/20260003.json 产生
    ev_file = storage.evaluations_dir() / "20260003.json"
    assert ev_file.exists()
    ev = json.loads(ev_file.read_text(encoding="utf-8"))
    assert ev["student_id"] == "20260003"
    assert 0 <= ev["score"] <= ev["max_score"]
    assert ev["understanding"] in ("mastered", "partial", "limited", "insufficient")


def test_submit_unknown_student_404(client, generated):
    res = client.post("/api/exam/99999999/submit", json={"answers": {"q1": "A"}})
    assert res.status_code == 404


def test_submit_not_generated_404(client):
    res = client.post("/api/exam/20260003/submit", json={"answers": {"q1": "A"}})
    assert res.status_code == 404
    assert "尚未生成" in res.json()["detail"]


def test_submit_invalid_answer_422(client, generated):
    res = client.post("/api/exam/20260003/submit", json={"answers": {"q1": "Z"}})
    assert res.status_code == 422
    res = client.post("/api/exam/20260003/submit", json={"answers": {"q99": "A"}})
    assert res.status_code == 422


def test_duplicate_submit_keeps_attempts(client, generated, monkeypatch, await_evaluation):
    # 本测试验证重练时保留历次作答；显式打开重练，避免依赖后台评价是否已完成的竞态。
    monkeypatch.setenv("AA_RESUBMIT_AFTER_FEEDBACK", "1")
    for i in range(2):
        res = client.post(
            "/api/exam/20260003/submit", json={"answers": GOOD_ANSWERS_20260003}
        )
        assert res.status_code == 200
        await_evaluation("20260003")
        assert res.json()["attempt"] == i + 1
    saved = json.loads(
        (storage.responses_dir() / "20260003.json").read_text(encoding="utf-8")
    )
    assert saved["attempt_count"] == 2
    assert len(saved["attempts"]) == 2


def test_teacher_overview_and_detail(client, generated, await_evaluation):
    # 一个学生提交，一个学生未提交
    client.post("/api/exam/20260003/submit", json={"answers": GOOD_ANSWERS_20260003})
    await_evaluation("20260003")

    res = client.get("/api/teacher/overview")
    assert res.status_code == 200
    rows = res.json()["rows"]
    assert len(rows) == 6
    by_sid = {r["student_id"]: r for r in rows}
    assert by_sid["20260003"]["exam_status"] == "done"
    assert by_sid["20260003"]["understanding"] is not None
    assert by_sid["20260003"]["original_issues"] == ["out-of-bounds"]
    assert by_sid["20260001"]["exam_status"] == "waiting"
    assert by_sid["20260001"]["score"] is None

    # Test 7: 教师详情包含完整链路信息
    res = client.get("/api/teacher/students/20260003")
    assert res.status_code == 200
    detail = res.json()
    assert "int max = arr[0];" in detail["submission"]["code"]
    assert detail["questionnaire"]["diagnosis"]["issues"][0]["type"] == "out-of-bounds"
    assert detail["questionnaire"]["questions"][0]["expected_answer"] == "B"  # 教师可见
    assert detail["response"]["answers"]["q1"] == "B"
    assert detail["evaluation"]["score"] >= 0
    # 未提交学生：response 为空
    res = client.get("/api/teacher/students/20260001")
    assert res.json()["response"] is None
    assert res.json()["evaluation"] is None


def test_teacher_detail_unknown_404(client, generated):
    assert client.get("/api/teacher/students/99999999").status_code == 404


def test_teacher_reevaluate_endpoint(client, generated, await_evaluation):
    client.post("/api/exam/20260003/submit", json={"answers": GOOD_ANSWERS_20260003})
    await_evaluation("20260003")
    res = client.post("/api/teacher/students/20260003/evaluate")
    assert res.status_code == 200
    assert res.json()["student_id"] == "20260003"
    # 未提交的学生不能评价
    res = client.post("/api/teacher/students/20260001/evaluate")
    assert res.status_code == 404


def test_teacher_regenerate_endpoint(client, generated, await_generation_job):
    # 异步 202：立即返回 pending 任务，教师端轮询状态端点（第三批）
    res = client.post("/api/teacher/students/20260003/regenerate")
    assert res.status_code == 202
    assert res.json()["status"] == "pending"
    job = await_generation_job(client, "20260003")
    assert job["status"] == "ok"
    assert job["version"] == 2
    detail = client.get("/api/teacher/students/20260003")
    assert detail.status_code == 200
    assert len(detail.json()["questionnaire"]["questions"]) in (3, 4, 5)
    # 状态端点：无任务的学生 404
    res = client.get("/api/teacher/students/20260001/regenerate/status")
    assert res.status_code == 404


def test_full_closed_loop_all_students(client, generated, await_evaluation):
    """六个学生全部提交（不同作答质量），教师总览全部 done。"""
    answers_per_student = {
        "20260001": {  # 全对
            "q1": "A", "q2": "C", "q3": "B",
            "q4": "n=0 时 arr[0] 越界；n>=1 保证 arr[0] 存在，是函数的前置条件。",
        },
        "20260002": {  # 全错
            "q1": "A", "q2": "A", "q3": ["A", "C"], "q4": "随便写的",
        },
        "20260003": GOOD_ANSWERS_20260003,
        "20260004": {"q1": "C", "q2": "B", "q3": "B", "q4": "不变量改为最小值，条件反过来。"},
        "20260005": {"q1": "C", "q2": "B", "q3": "A", "q4": "只比较了首尾两个元素。"},
        "20260006": {"q1": "C", "q2": "A", "q3": "A", "q4": "计算靠赋值，返回靠 return。"},
    }
    for sid, answers in answers_per_student.items():
        res = client.post(f"/api/exam/{sid}/submit", json={"answers": answers})
        assert res.status_code == 200, f"{sid}: {res.text}"
        assert res.json()["evaluation_status"] == "pending"
        assert await_evaluation(sid) == "ok"

    rows = client.get("/api/teacher/overview").json()["rows"]
    assert all(r["exam_status"] == "done" for r in rows)
    assert all(r["understanding"] for r in rows)

    by_sid = {r["student_id"]: r for r in rows}
    # 学得好与学得差的应有分数差异（20260002 全错 vs 20260001 全对）
    assert by_sid["20260001"]["score"] > by_sid["20260002"]["score"]
