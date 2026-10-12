"""测试夹具：每个测试使用独立的 AA_ROOT 临时目录，强制 Mock LLM。

最小认证适配：夹具在隔离根目录写入**已知**访问凭据（教师口令 + 每学号学生访问码），
legacy 测试通过教师口令访问学生端与教师端接口；认证本身的拒绝行为在
test_auth_regressions.py 中用无凭据客户端验证。
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# 必须在 app.main 首次导入前设置：TestClient 默认 Host 是 "testserver"，
# Host 白名单（安全修复 R6）在 import 时固化
os.environ.setdefault("AA_ALLOWED_HOSTS", "testserver,localhost,127.0.0.1")

from app import storage

REPO_ROOT = Path(__file__).resolve().parent.parent

TEACHER_TOKEN = "test-teacher-token"


def student_token(sid: str) -> str:
    return f"test-token-{sid}"


class TeacherClient(TestClient):
    """自动携带教师口令的客户端（教师口令可访问学生端与教师端全部接口）。"""

    def __init__(self, app):
        super().__init__(app)
        self.headers.update({"Authorization": f"Bearer {TEACHER_TOKEN}"})

    def request(self, method, url, **kwargs):  # type: ignore[override]
        headers = dict(kwargs.pop("headers", None) or {})
        headers.setdefault("Authorization", f"Bearer {TEACHER_TOKEN}")
        kwargs["headers"] = headers
        return super().request(method, url, **kwargs)


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("AA_ACCESS_CODE_ENABLED", "1")  # 测试默认开启访问码（docs §6）
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AA_TEACHER_TOKEN", raising=False)
    root = tmp_path / "aa_root"
    shutil.copytree(REPO_ROOT / "data", root / "data")
    (root / "generated").mkdir()
    (root / "responses").mkdir()
    (root / "evaluations").mkdir()
    # 已知访问凭据（docs/evaluation-rules.md §6）
    access = root / "data" / "access"
    access.mkdir(parents=True, exist_ok=True)
    students = {
        p.stem: student_token(p.stem)
        for p in sorted((root / "data" / "submissions").glob("*.json"))
    }
    (access / "tokens.json").write_text(
        json.dumps({"teacher_token": TEACHER_TOKEN, "students": students},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    monkeypatch.setenv("AA_ROOT", str(root))
    # 进程内限速器（auth 防爆破 / 提交频率）是全局状态：每个测试重置，
    # 否则前面测试烧掉的失败预算会 429/403 到后面所有测试（TestClient IP 恒定）
    from app import auth as auth_module

    auth_module._failures.clear()
    yield root
    # 提交后的评价是异步线程：在还原 AA_ROOT 前 join 收尾，
    # 否则迟到的写穿透到还原后的真实数据目录
    from app import main as main_module

    for t in list(main_module._eval_workers):
        t.join(timeout=10)
    main_module._submit_times.clear()


@pytest.fixture
def await_evaluation():
    """等待该生最新作答的异步评价落定（mock 下瞬时；真实 LLM 由 timeout 兜底）。"""
    import time

    from app import main as main_module

    def _await(sid: str, timeout: float = 10.0) -> str:
        deadline = time.monotonic() + timeout
        for t in list(main_module._eval_workers):
            t.join(max(0.0, deadline - time.monotonic()))
        while time.monotonic() < deadline:
            resp = storage.load_response(sid)
            if resp is not None and resp.attempts:
                status = resp.attempts[-1].evaluation_status
                if status != "pending":
                    return status
            time.sleep(0.02)
        raise AssertionError(f"评价状态轮询超时: {sid}")

    return _await


@pytest.fixture
def await_generation_job():
    """轮询异步再生成任务直到结束（Mock 生成瞬时完成；真实 LLM 由 timeout 兜底）。"""
    import time

    def _await(client, sid: str, timeout: float = 10.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            res = client.get(f"/api/teacher/students/{sid}/regenerate/status")
            assert res.status_code == 200, res.text
            job = res.json()
            if job["status"] != "pending":
                return job
            time.sleep(0.05)
        raise AssertionError(f"生成任务轮询超时: {sid}")

    return _await
