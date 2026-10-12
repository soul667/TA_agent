"""盒带回归：真实 LLM 的原始响应离线重放完整解析→装配链路（无网络、零 API 成本）。

盒带由 `python -m app.eval_harness` 录制到 tests/cassettes/。目录为空时整层跳过，
因此默认 CI（Mock 模式）不受影响；录制后本层即为 75 个 Mock 测试之外唯一
走「真实模型输出 → 宽松解析 → 草稿校验 → 装配」的测试。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import storage
from app.agent import generate_questionnaire
from app.evaluator import evaluate
from app.llm import LLMProvider
from app.models import ResponseFile, Submission, now_utc

CASSETTE_DIR = Path(__file__).resolve().parent / "cassettes"
CASSETTE_FILES = sorted(CASSETTE_DIR.glob("*.json")) if CASSETTE_DIR.exists() else []

if CASSETTE_FILES:
    _pytestmark = [pytest.mark.cassette]
else:
    _pytestmark = [
        pytest.mark.cassette,
        pytest.mark.skip(reason="无盒带：先运行 python -m app.eval_harness 录制"),
    ]
pytestmark = _pytestmark


class CassetteProvider(LLMProvider):
    """按录制顺序回放原始输出；调用次数与录制时不一致即报错（流水线漂移信号）。"""

    def __init__(self, name: str, raws: list[str]):
        self.name = name
        self.raws = list(raws)

    def generate(self, messages, response_schema=None):
        if not self.raws:
            raise AssertionError("盒带耗尽：当前流水线的 LLM 调用次数多于录制时")
        return self.raws.pop(0)


@pytest.mark.parametrize("path", CASSETTE_FILES, ids=[p.stem for p in CASSETTE_FILES])
def test_cassette_generation_pipeline(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    case = _load_case(data["case_id"])
    assert case is not None, f"案例 {data['case_id']} 不在 data/mock_cases/"
    provider = CassetteProvider(data["model"], data["generation_raws"])
    assignment = storage.load_assignment()
    submission = Submission(
        student_id="20260003", assignment_id=assignment.id, code=case.code,
    )
    q = generate_questionnaire(provider, assignment, submission)
    assert 3 <= len(q.questions) <= 5
    assert q.case_id == case.case_id and q.case_match == "exact"
    assert q.prompt_version.startswith("pv-")
    # 学生可见视图不泄露答案与内部关联
    public = json.dumps(q.public(), ensure_ascii=False)
    assert "expected_answer" not in public
    assert "target_issue" not in public
    assert not provider.raws, "盒带未耗尽：流水线调用次数少于录制时"


@pytest.mark.parametrize("path", CASSETTE_FILES, ids=[p.stem for p in CASSETTE_FILES])
def test_cassette_evaluation_pipeline(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not data.get("evaluation"):
        pytest.skip("盒带无判分腿")
    case = _load_case(data["case_id"])
    assert case is not None
    assignment = storage.load_assignment()
    submission = Submission(
        student_id="20260003", assignment_id=assignment.id, code=case.code,
    )
    # 问卷侧也用盒带重放，保证评价腿基于录制时的问卷
    gen_provider = CassetteProvider(data["model"], data["generation_raws"])
    questionnaire = generate_questionnaire(gen_provider, assignment, submission)
    ev_provider = CassetteProvider(data["model"], data["evaluation"]["raws"])
    response = ResponseFile(
        student_id=submission.student_id,
        submitted_at=now_utc(),
        answers=dict(data["evaluation"]["answers"]),
        attempt_count=1,
    )
    result = evaluate(ev_provider, assignment, submission, questionnaire, response)
    assert 0 <= result.score <= 10
    assert result.prompt_version.startswith("pv-")
    assert not ev_provider.raws


def _load_case(case_id: str):
    from app import cases

    return cases.load_case(case_id)
