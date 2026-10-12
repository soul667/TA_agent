"""Evidence-bound TA follow-up notes. A code sample cannot establish AI authorship."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from . import storage
from .source import paper_source_fingerprint


class RiskSignal(BaseModel):
    filename: str = Field(min_length=1, max_length=300)
    global_line: int = Field(ge=1)
    code_excerpt: str = Field(min_length=1, max_length=500)
    observation: str = Field(min_length=1, max_length=1000)
    follow_up_question: str = Field(min_length=1, max_length=1000)


class QuestionRisk(BaseModel):
    student_id: str
    assignment_id: str
    source_fingerprint: str
    provider_id: str
    model_id: str
    reviewed_at: str
    priority: Literal["normal", "follow_up", "verify"] = "normal"
    ai_assistance_cue: bool = False
    signals: list[RiskSignal] = Field(default_factory=list, max_length=5)
    uncertainty: str = "仅凭代码无法判定作者或是否使用了 AI，需由学生解释本人代码。"

    @model_validator(mode="after")
    def require_evidence_for_flag(self):
        if (self.ai_assistance_cue or self.priority == "verify") and not self.signals:
            raise ValueError("提高追问优先级必须提供可核对的具体代码证据")
        return self


def _source_line(source, global_line):
    offset = global_line - source.line_start
    lines = source.code.splitlines()
    return lines[offset] if 0 <= offset < len(lines) else None


def validate_against_source(review: QuestionRisk, submission, assignment) -> None:
    if review.student_id != submission.student_id or review.assignment_id != assignment.id:
        raise ValueError("风险记录与学生或作业不匹配")
    if review.source_fingerprint != paper_source_fingerprint(submission, assignment):
        raise ValueError("风险记录所依据的代码已变化")
    for signal in review.signals:
        candidates = [f for f in submission.source_files if f.filename == signal.filename]
        if not candidates:
            raise ValueError(f"证据文件不存在: {signal.filename}")
        line = next((_source_line(f, signal.global_line) for f in candidates
                     if _source_line(f, signal.global_line) is not None), None)
        if line is None or "".join(signal.code_excerpt.split()) not in "".join(line.split()):
            raise ValueError(f"第 {signal.global_line} 行代码与所引证据不符")


def path_for(student_id):
    return storage.root() / "question_risks" / f"{student_id}.json"


def store_review(review: QuestionRisk, submission, assignment):
    validate_against_source(review, submission, assignment)
    storage.save_json_atomic(path_for(submission.student_id), review.model_dump(mode="json"))


def read_status(submission, assignment):
    if not submission.code.strip():
        return {"status": "unavailable"}
    path = path_for(submission.student_id)
    data = storage.load_json(path)
    if data is None:
        return {"status": "invalid" if path.exists() else "pending"}
    try:
        risk = QuestionRisk.model_validate(data)
        validate_against_source(risk, submission, assignment)
    except Exception:
        return {"status": "outdated"}
    return {"status": "ready", **risk.model_dump(mode="json")}
