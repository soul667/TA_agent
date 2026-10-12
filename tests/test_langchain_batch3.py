"""第三批测试：前缀拆分 / 内容寻址复用 / 单飞 / 并发批跑 / 异步再生成。"""
from __future__ import annotations

import json
import threading
import time

import pytest

from app import reuse, storage
from app.agent import (
    build_assignment_prefix,
    build_messages,
    build_student_suffix,
    generate_questionnaire,
)
from app.generate_all import main as generate_all_main
from app.llm import LLMProvider, MockLLMProvider
from app.models import GenerationJob


@pytest.fixture()
def client():
    from conftest import TeacherClient
    from app.main import app

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


def _submission(sid: str, code: str):
    from app.models import Submission

    return Submission(student_id=sid, assignment_id="c-find-max", code=code)


def _any_code() -> str:
    return next(iter(storage.list_submissions())).code


class _SlowProvider(LLMProvider):
    """记录调用次数并放慢生成，验证单飞合并并发请求。"""

    name = "slow-mock"

    def __init__(self, delay: float = 0.2):
        self.delay = delay
        self.calls = 0
        self._lock = threading.Lock()

    def generate(self, messages, response_schema=None):
        with self._lock:
            self.calls += 1
        time.sleep(self.delay)
        return MockLLMProvider().generate(messages, response_schema)


# ---------------- 前缀 / 后缀拆分 ----------------

def test_assignment_prefix_is_byte_stable_and_identity_free():
    assignment = storage.load_assignment()
    s1 = _submission("20260001", _any_code())
    s2 = _submission("20260002", _any_code())
    p1, p2 = build_assignment_prefix(assignment), build_assignment_prefix(assignment)
    assert p1 == p2  # 字节稳定：可作 provider 前缀缓存与克隆纯函数的前提
    suffix1, suffix2 = build_student_suffix(s1), build_student_suffix(s2)
    assert suffix1 == suffix2  # 同代码 → 同后缀
    assert "20260001" not in p1 and "20260001" not in suffix1  # 身份不进可克隆载荷
    msgs = build_messages(assignment, s1)
    assert msgs[0]["role"] == "system"
    assert "STUDENT_ID: 20260001" in msgs[1]["content"]  # 身份只在消息头
    json.loads(suffix1)  # 后缀本身是合法 JSON


# ---------------- 内容寻址复用 ----------------

def test_reuse_clones_for_identical_code_with_single_llm_call(monkeypatch):
    monkeypatch.delenv("AA_REUSE", raising=False)
    assignment = storage.load_assignment()
    code = _any_code()
    provider = MockLLMProvider()

    q1, reused1 = reuse.get_or_generate(
        assignment, _submission("20260001", code),
        lambda: generate_questionnaire(provider, assignment, _submission("20260001", code)),
    )
    assert reused1 is False
    # 另一位学生交了一模一样的代码（抄袭场景）：零 LLM 调用，直接克隆
    calls_before = provider._calls
    q2, reused2 = reuse.get_or_generate(
        assignment, _submission("20260002", code),
        lambda: (_ for _ in ()).throw(AssertionError("不应再次调用 LLM")),
    )
    assert reused2 is True
    assert q2.reused_from == "20260001"
    assert q2.student_id == "20260002"
    assert q2.questionnaire_id != q1.questionnaire_id
    assert [x.question for x in q2.questions] == [x.question for x in q1.questions]
    assert provider._calls == calls_before  # 零新增 LLM 调用
    # 学生端视图不含来源信息
    assert "reused_from" not in json.dumps(q2.public(), ensure_ascii=False)


def test_reuse_singleflight_merges_concurrent_same_key_generation(monkeypatch):
    monkeypatch.delenv("AA_REUSE", raising=False)
    assignment = storage.load_assignment()
    code = _any_code()
    provider = _SlowProvider(delay=0.3)

    results: list = []
    barrier = threading.Barrier(3)

    def worker(sid: str):
        barrier.wait()  # 三个线程同时起跑
        q, reused = reuse.get_or_generate(
            assignment, _submission(sid, code),
            lambda: generate_questionnaire(provider, assignment, _submission(sid, code)),
        )
        results.append((sid, reused))

    threads = [threading.Thread(target=worker, args=(f"2026000{i}",)) for i in (1, 2, 3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert len(results) == 3
    reused_flags = [r for _, r in results]
    assert reused_flags.count(True) == 2  # 一次真实生成 + 两次克隆
    assert provider.calls == 1


def test_reuse_disabled_writes_index_but_always_generates(monkeypatch):
    monkeypatch.setenv("AA_REUSE", "0")
    assignment = storage.load_assignment()
    code = _any_code()
    provider = MockLLMProvider()

    def _gen(sid):
        return generate_questionnaire(provider, assignment, _submission(sid, code))

    q1, reused1 = reuse.get_or_generate(
        assignment, _submission("20260001", code), lambda: _gen("20260001"))
    q2, reused2 = reuse.get_or_generate(
        assignment, _submission("20260002", code), lambda: _gen("20260002"))
    assert reused1 is False and reused2 is False  # 关闭复用：每次都真实生成
    assert not q2.reused_from


# ---------------- 并发批跑 ----------------

def test_generate_all_concurrent_generates_all_students(monkeypatch):
    monkeypatch.delenv("AA_MOCK_CHAOS", raising=False)
    rc = generate_all_main(force=True)
    assert rc == 0
    count = len(list(storage.list_submissions()))
    generated = [p for p in storage.generated_dir().glob("*.json") if p.name != "reuse-index.json"]
    assert len(generated) == count


# ---------------- 异步再生成任务 ----------------

def test_pending_generation_job_recovered_on_startup():
    job = GenerationJob(student_id="20260003", status="pending")
    storage.save_generation_job(job)
    recovered = storage.recover_interrupted_generation_jobs()
    assert recovered == 1
    after = storage.load_generation_job("20260003")
    assert after.status == "failed" and "interrupted" in after.error


def test_regenerate_idempotent_while_pending(client, generated):
    # 已有 pending 任务时再次 POST 返回同一任务（幂等），不重复触发
    job = GenerationJob(student_id="20260003", status="pending")
    storage.save_generation_job(job)
    res = client.post("/api/teacher/students/20260003/regenerate")
    assert res.status_code == 202
    assert res.json()["job_id"] == job.job_id
