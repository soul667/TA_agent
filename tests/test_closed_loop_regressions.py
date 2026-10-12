"""提交 / 评价 / 反馈闭环回归（审批 P0-2、P0-3 与幂等、恢复要求）。

覆盖：
- 评价失败：答案保留、教师总览不显示旧结论、学生状态 eval_failed、重试不新增作答；
- 重新生成问卷：新版本 + 历史保留 + 旧作答仍绑定旧版本 + 旧版本提交 409；
- 幂等提交：同一 submission_token 返回同一回执，不新增作答；
- 启动恢复：pending 评价记录标记 failed(interrupted)；
- 学生结果状态机与助教复核发布反馈。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import storage
from app.agent import generate_questionnaire
from app.llm import MockLLMProvider
from app.main import app
from app.models import EvaluationRecord, now_utc
from conftest import TeacherClient, student_token

GOOD_ANSWERS_20260003 = {
    "q1": "B",
    "q2": "B",
    "q3": "B",
    "q4": "最后一次合法访问是 arr[n-1]；当 i == n 时循环必须停止，因为 arr[n] 不属于数组。",
    "q5": "C",
}
CONTRADICTORY_ANSWERS_20260003 = {
    "q1": "B",
    "q2": "B",
    "q3": "A",
    "q4": "arr[n] 也可以访问，i 到 n 才算处理完全部元素。",
    "q5": "B",
}


@pytest.fixture()
def client():
    return TeacherClient(app)


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


# ---------------- P0-2：评价失败不丢答案、不显示旧结论 ----------------

def test_evaluation_failure_preserves_answers_and_hides_stale_result(
    client, generated, await_evaluation, monkeypatch
):
    # 本测试的教学场景是"评价 ok 后重练再交"：显式恢复开放重交（安全修复 R2 后默认锁死）
    monkeypatch.setenv("AA_RESUBMIT_AFTER_FEEDBACK", "1")
    # 第一次提交评价成功（mastered / 10 分）：提交回执立即返回 pending，
    # 等后台评价落定后再继续，保证后续 monkeypatch 只影响第二次提交
    res = client.post("/api/exam/20260003/submit", json={"answers": GOOD_ANSWERS_20260003})
    assert res.json()["evaluation_status"] == "pending"
    assert await_evaluation("20260003") == "ok"

    # 第二次提交时评价失败（模拟）
    def _boom(*args, **kwargs):
        raise RuntimeError("模拟评价失败")

    monkeypatch.setattr(main_module, "evaluate", _boom)
    res = client.post("/api/exam/20260003/submit", json={"answers": CONTRADICTORY_ANSWERS_20260003})
    assert res.status_code == 200
    body = res.json()
    assert body["evaluation_status"] == "pending"
    assert body["attempt"] == 2
    assert await_evaluation("20260003") == "failed"

    # 答案仍完整落盘（两次作答都在）
    saved = json.loads(
        (storage.responses_dir() / "20260003.json").read_text(encoding="utf-8")
    )
    assert saved["attempt_count"] == 2
    assert len(saved["attempts"]) == 2
    assert saved["answers"]["q4"] == CONTRADICTORY_ANSWERS_20260003["q4"]

    # 评价记录：第 1 次 ok、第 2 次 failed，分开保存
    eval_file = storage.load_evaluation_file("20260003")
    statuses = [r.status for r in eval_file.records]
    assert statuses == ["ok", "failed"]

    # 教师总览：不把旧评价当作当前结果
    row = {r["student_id"]: r for r in client.get("/api/teacher/overview").json()["rows"]}[
        "20260003"
    ]
    assert row["exam_status"] == "done"
    assert row["evaluation_status"] == "failed"
    assert row["understanding"] is None
    assert row["score"] is None

    # 学生结果：eval_failed，且提示可重试
    headers = {"Authorization": f"Bearer {student_token('20260003')}"}
    result = TestClient(app).get("/api/exam/20260003/result", headers=headers).json()
    assert result["state"] == "eval_failed"
    assert any("重试" in s for s in result["next_steps"])


def test_retry_evaluation_does_not_add_attempt(
    client, generated, await_evaluation, monkeypatch
):
    # 开放重交模式（教学重练场景）：安全修复 R2 后默认评价出分即锁死
    monkeypatch.setenv("AA_RESUBMIT_AFTER_FEEDBACK", "1")
    client.post("/api/exam/20260003/submit", json={"answers": CONTRADICTORY_ANSWERS_20260003})
    await_evaluation("20260003")

    original_evaluate = main_module.evaluate

    def _boom(*args, **kwargs):
        raise RuntimeError("模拟评价失败")

    monkeypatch.setattr(main_module, "evaluate", _boom)
    client.post("/api/exam/20260003/submit", json={"answers": GOOD_ANSWERS_20260003})
    assert await_evaluation("20260003") == "failed"
    saved = json.loads(
        (storage.responses_dir() / "20260003.json").read_text(encoding="utf-8")
    )
    assert saved["attempt_count"] == 2

    # 恢复真实评价逻辑后重试（不用 monkeypatch.undo：那会连环境隔离一起撤销）
    monkeypatch.setattr(main_module, "evaluate", original_evaluate)
    res = client.post("/api/teacher/students/20260003/evaluate")
    assert res.status_code == 200
    assert res.json()["student_id"] == "20260003"

    saved2 = json.loads(
        (storage.responses_dir() / "20260003.json").read_text(encoding="utf-8")
    )
    assert saved2["attempt_count"] == 2  # 重试评价没有新增作答
    eval_file = storage.load_evaluation_file("20260003")
    assert eval_file.records[-1].status == "ok"


# ---------------- P0-3：重新生成 = 新版本，旧作答不迁移 ----------------

def test_regeneration_creates_new_version_and_rejects_stale_submission(
    client, generated, await_generation_job
):
    res = client.post("/api/exam/20260003/submit", json={"answers": GOOD_ANSWERS_20260003})
    assert res.json()["questionnaire_version"] == 1

    regen = client.post("/api/teacher/students/20260003/regenerate")
    assert regen.status_code == 202  # 异步 202（第三批）
    job = await_generation_job(client, "20260003")
    assert job["status"] == "ok"
    assert job["version"] == 2

    # 当前问卷是 v2；v1 完整保留在 history
    assert storage.load_questionnaire("20260003").version == 2
    v1 = storage.load_questionnaire("20260003", 1)
    assert v1 is not None and v1.version == 1

    # 旧作答仍绑定 v1（不会挂到新问卷的题目下）
    response = storage.load_response("20260003")
    assert response.attempts[0].questionnaire_version == 1
    assert response.attempts[0].questionnaire_id == v1.questionnaire_id

    # 旧版本提交 → 409
    res = client.post(
        "/api/exam/20260003/submit",
        json={"answers": GOOD_ANSWERS_20260003, "questionnaire_version": 1},
    )
    assert res.status_code == 409
    assert "问卷已更新" in res.json()["detail"]

    # 新版本提交 → 正常，绑定 v2
    res = client.post(
        "/api/exam/20260003/submit",
        json={"answers": GOOD_ANSWERS_20260003, "questionnaire_version": 2},
    )
    assert res.status_code == 200
    assert res.json()["questionnaire_version"] == 2
    response = storage.load_response("20260003")
    assert response.attempts[1].questionnaire_version == 2

    # 教师详情：可按版本取回旧问卷，attempts 各自标注版本
    detail = client.get("/api/teacher/students/20260003").json()
    assert detail["questionnaire"]["version"] == 2
    assert detail["questionnaire_versions"] == [1, 2]
    assert detail["response"]["attempts"][0]["questionnaire_version"] == 1
    detail_v1 = client.get("/api/teacher/students/20260003?version=1").json()
    assert detail_v1["questionnaire"]["version"] == 1


# ---------------- 幂等提交 ----------------

def test_idempotent_submission_same_token_same_receipt(client, generated, monkeypatch):
    # 开放重交模式（教学重练场景）：安全修复 R2 后默认评价出分即锁死
    monkeypatch.setenv("AA_RESUBMIT_AFTER_FEEDBACK", "1")
    token = "net-retry-token-001"
    first = client.post(
        "/api/exam/20260003/submit",
        json={"answers": GOOD_ANSWERS_20260003, "submission_token": token},
    )
    assert first.status_code == 200
    # 网络重试：同一 token 再次提交 → 同一回执
    second = client.post(
        "/api/exam/20260003/submit",
        json={"answers": GOOD_ANSWERS_20260003, "submission_token": token},
    )
    assert second.status_code == 200
    assert second.json()["attempt_id"] == first.json()["attempt_id"]
    assert second.json()["attempt"] == first.json()["attempt"] == 1

    saved = json.loads(
        (storage.responses_dir() / "20260003.json").read_text(encoding="utf-8")
    )
    assert saved["attempt_count"] == 1  # 没有新增作答

    # 新 token → 新作答
    third = client.post(
        "/api/exam/20260003/submit",
        json={"answers": GOOD_ANSWERS_20260003, "submission_token": "another-token"},
    )
    assert third.json()["attempt"] == 2


def test_concurrent_duplicate_tokens_create_single_attempt(client, generated):
    """同一 token 的并发重试（线程池模拟）只落一条作答。"""
    import concurrent.futures

    token = "concurrent-token-001"

    def submit():
        return client.post(
            "/api/exam/20260003/submit",
            json={"answers": GOOD_ANSWERS_20260003, "submission_token": token},
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = [f.result() for f in [pool.submit(submit) for _ in range(4)]]
    assert all(r.status_code == 200 for r in results)
    attempt_ids = {r.json()["attempt_id"] for r in results}
    assert len(attempt_ids) == 1
    saved = json.loads(
        (storage.responses_dir() / "20260003.json").read_text(encoding="utf-8")
    )
    assert saved["attempt_count"] == 1


# ---------------- 启动恢复 ----------------

def test_recover_interrupted_evaluations(client, generated, await_evaluation):
    client.post("/api/exam/20260003/submit", json={"answers": GOOD_ANSWERS_20260003})
    await_evaluation("20260003")  # 在飞评价先落定，避免真实 pending 记录被一并恢复
    eval_file = storage.load_evaluation_file("20260003")
    pending = EvaluationRecord(
        student_id="20260003",
        attempt_id="fake-attempt",
        status="pending",
        started_at=now_utc(),
    )
    eval_file.records.append(pending)
    storage.save_evaluation_file(eval_file)

    recovered = storage.recover_interrupted_evaluations()
    assert recovered == 1
    refreshed = storage.load_evaluation_file("20260003")
    rec = [r for r in refreshed.records if r.evaluation_id == pending.evaluation_id][0]
    assert rec.status == "failed"
    assert rec.error == "interrupted"


# ---------------- 学生状态机与复核闭环 ----------------

def test_student_result_lifecycle_and_review_publication(
    client, plain, generated, await_evaluation, monkeypatch
):
    # 开放重交模式：本测试覆盖"出分 → 换档位重交 → 复核发布"完整教学闭环
    monkeypatch.setenv("AA_RESUBMIT_AFTER_FEEDBACK", "1")
    headers = {"Authorization": f"Bearer {student_token('20260003')}"}

    # mastered 档：评价完成且无需复核 → 反馈立即可见
    client.post("/api/exam/20260003/submit", json={"answers": GOOD_ANSWERS_20260003})
    assert await_evaluation("20260003") == "ok"
    result = plain.get("/api/exam/20260003/result", headers=headers).json()
    assert result["state"] == "feedback_ready"
    assert result["feedback"]["text"]

    # 矛盾档：needs_ta_review → 等待助教复核
    client.post("/api/exam/20260003/submit", json={"answers": CONTRADICTORY_ANSWERS_20260003})
    assert await_evaluation("20260003") == "ok"
    result = plain.get("/api/exam/20260003/result", headers=headers).json()
    assert result["state"] == "awaiting_review"
    assert result["feedback"] is None

    # 助教复核并发布
    review = client.post(
        "/api/teacher/students/20260003/review",
        json={
            "decision": "revised",
            "revised_understanding": "partial",
            "comment": "text 题与选择题矛盾，下调为部分理解",
            "feedback_to_student": "你对下标范围的记忆正确，但对循环边界的解释与事实相反，建议重做边界跟踪练习。",
            "published": True,
        },
    )
    assert review.status_code == 200
    assert review.json()["decision"] == "revised"

    # 学生看到已发布反馈；总览标记已复核
    result = plain.get("/api/exam/20260003/result", headers=headers).json()
    assert result["state"] == "feedback_ready"
    assert result["feedback"]["source"] == "review"
    assert "循环边界" in result["feedback"]["text"]
    row = {r["student_id"]: r for r in client.get("/api/teacher/overview").json()["rows"]}[
        "20260003"
    ]
    assert row["review_status"] == "reviewed"

    # 复核记录持久化
    reviews = storage.load_review_file("20260003")
    assert reviews.reviews[-1].comment
    assert reviews.reviews[-1].published


def test_review_validation(client, generated):
    client.post("/api/exam/20260003/submit", json={"answers": CONTRADICTORY_ANSWERS_20260003})
    # revised 必须给理由与档位
    assert client.post(
        "/api/teacher/students/20260003/review",
        json={"decision": "revised", "feedback_to_student": "x"},
    ).status_code == 422
    # published 必须带反馈
    assert client.post(
        "/api/teacher/students/20260003/review",
        json={"decision": "confirmed", "published": True},
    ).status_code == 422


def test_result_endpoint_recover_after_reload(plain, client, generated, await_evaluation):
    """刷新 / 重新进入后能找回已提交记录（不依赖浏览器草稿）。"""
    headers = {"Authorization": f"Bearer {student_token('20260003')}"}
    client.post("/api/exam/20260003/submit", json={"answers": GOOD_ANSWERS_20260003})
    assert await_evaluation("20260003") == "ok"
    # 模拟“关闭页面后再次进入”：新的无状态客户端重新查询
    again = TestClient(app).get("/api/exam/20260003/result", headers=headers).json()
    assert again["latest_attempt"]["evaluation_status"] == "ok"
    assert len(again["attempts"]) == 1
