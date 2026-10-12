"""Validate/import ZCode Start Plan outputs after provider identity has been verified.

This module does not invoke a model or change the user's ZCode configuration. It
refuses outputs without an explicit Start Plan + GLM-5.3-Flash provenance envelope.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import cases, storage
from .agent import _pick_questions
from .llm import extract_json
from .models import Diagnosis, Question, Questionnaire, now_utc
from .question_risk import QuestionRisk, store_review, validate_against_source
from .source import paper_source_fingerprint

ALLOWED_PROVIDER_IDS = {
    "builtin:bigmodel-start-plan", "builtin:zai-start-plan",
    "account:bigmodel-start-plan", "account:zai-start-plan",
    # 用户 2026-09-28 确认改用本会话实际的 BigModel Individual Coding Plan 套餐。
    "account:bigmodel-individual-coding-plan",
}
ALLOWED_MODEL_IDS = {"glm-5.3-flash", "GLM-5.3-Flash"}
QUESTION_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "zcode_batch_question.txt"


def _verified_envelope(envelope):
    runtime = envelope.get("verified_runtime") or {}
    provider = runtime.get("provider_id")
    model = runtime.get("model_id")
    if provider not in ALLOWED_PROVIDER_IDS or model not in ALLOWED_MODEL_IDS:
        raise ValueError("模型调用来源未验证为 ZCode Start Plan / GLM-5.3-Flash；拒绝入库")
    raw = envelope.get("response", "")
    body = extract_json(raw) if isinstance(raw, str) else raw if isinstance(raw, dict) else None
    if not isinstance(body, dict):
        raise ValueError("ZCode 输出不是可解析的 JSON 对象")
    return provider, model, body


def _risk(submission, assignment, data, provider, model):
    if not isinstance(data, dict):
        raise ValueError("缺少全班必需的追问风险记录")
    review = QuestionRisk.model_validate({
        "student_id": submission.student_id, "assignment_id": assignment.id,
        "source_fingerprint": paper_source_fingerprint(submission, assignment),
        "provider_id": provider, "model_id": model,
        "reviewed_at": now_utc().isoformat(), **{k: data[k] for k in
            ("priority", "ai_assistance_cue", "signals", "uncertainty") if k in data},
    })
    validate_against_source(review, submission, assignment)
    return review


def ingest_question_result(student_id: str, envelope: dict) -> dict:
    provider, model, body = _verified_envelope(envelope)
    submission = storage.load_submission(student_id)
    if not submission or not submission.code.strip():
        raise ValueError("该学号没有可生成问卷的源码")
    assignment = storage.load_assignment()
    fingerprint = paper_source_fingerprint(submission, assignment)
    if body.get("student_id", student_id) != student_id:
        raise ValueError("模型回显了另一位学生的学号")
    if not isinstance(body.get("questions"), list):
        raise ValueError("缺少 questions 列表")
    questions = [Question.model_validate(item) for item in body["questions"]]
    if not 3 <= len(questions) <= 7:
        raise ValueError("模型应先产出 3–7 道可验证的题目")
    if any(not q.id.strip() for q in questions) or len({q.id for q in questions}) != len(questions):
        raise ValueError("题目编号为空或重复，不能保存答卷")
    questions = [Question.model_validate(item) for item in _pick_questions(
        [q.model_dump(mode="json") for q in questions])]
    if not any(q.type.value == "text" for q in questions):
        raise ValueError("必须至少包含一道可解释本人代码的简答题")
    line_count = len(submission.code.splitlines())
    for q in questions:
        if not q.question.strip() or not q.question_en.strip():
            raise ValueError(f"题目 {q.id} 缺少中文或英文题面")
        if q.type.value == "text" and not q.expected_answer.strip():
            raise ValueError(f"简答题 {q.id} 缺少 TA 核对要点")
        if q.choices and not 3 <= len(q.choices) <= 5:
            raise ValueError(f"题目 {q.id} 需要 3–5 个选项")
        if q.choices and (not q.choices_en or len(q.choices_en) != len(q.choices)):
            raise ValueError(f"题目 {q.id} 的选项没有对应的英文版本")
        if any(line < 1 or line > line_count for line in q.code_lines):
            raise ValueError(f"题目 {q.id} 引用的代码行不存在")
        if q.variant and not 1 <= q.variant.line_no <= line_count:
            raise ValueError(f"题目 {q.id} 的代码变式行不存在")
    risk = _risk(submission, assignment, body.get("questioning_review"), provider, model)
    q = Questionnaire(student_id=student_id, assignment_id=assignment.id,
        diagnosis=Diagnosis.model_validate(body.get("diagnosis") or {}), questions=questions,
        generated_by=f"zcode:{provider}:{model}", source_fingerprint=fingerprint,
        code_fingerprint=cases.code_fingerprint(submission.code),
        prompt_version="pv-" + hashlib.sha256(QUESTION_PROMPT.read_bytes()).hexdigest()[:12])
    with storage.student_lock(student_id):
        current = storage.load_submission(student_id)
        if not current or paper_source_fingerprint(current, assignment) != fingerprint:
            raise ValueError("入库前学生源码已变化，请重新生成")
        existing = storage.load_questionnaire(student_id)
        if existing and existing.assignment_id == assignment.id and existing.source_fingerprint == fingerprint:
            raise ValueError("该学生已有适用答卷；保留现有版本并单独分析追问风险")
        response = storage.load_response(student_id)
        if response and response.attempts:
            raise ValueError("已有正式作答，不能把新题目自动覆盖到旧回答")
        q.version = storage.next_questionnaire_version(student_id)
        storage.save_questionnaire(q)
        store_review(risk, current, assignment)
    return {"student_id": student_id, "version": q.version, "question_count": len(q.questions),
            "provider_id": provider, "model_id": model, "risk_priority": risk.priority}


def ingest_risk_only_batch(student_ids: list[str], envelope: dict) -> list[dict]:
    provider, model, body = _verified_envelope(envelope)
    expected = set(student_ids)
    items = body.get("items")
    if not isinstance(items, list) or len(items) != len(expected):
        raise ValueError("风险批次人数与输入不一致")
    ids = [item.get("student_id") for item in items if isinstance(item, dict)]
    if len(ids) != len(expected) or set(ids) != expected:
        raise ValueError("风险批次遗漏或串用了学号")
    assignment = storage.load_assignment()
    prepared = []
    for item in items:
        submission = storage.load_submission(item["student_id"])
        if not submission:
            raise ValueError(f"学号不存在: {item['student_id']}")
        questionnaire = storage.load_questionnaire(submission.student_id)
        if not questionnaire or questionnaire.source_fingerprint != paper_source_fingerprint(submission, assignment):
            raise ValueError(f"该学生没有与当前源码匹配的答卷: {submission.student_id}")
        prepared.append((submission, _risk(submission, assignment, item.get("questioning_review"), provider, model)))
    for submission, risk in prepared:
        with storage.student_lock(submission.student_id):
            current = storage.load_submission(submission.student_id)
            store_review(risk, current, assignment)
    return [{"student_id": submission.student_id, "priority": risk.priority}
            for submission, risk in prepared]


def ingest_json_file(path: Path, student_id: str | None = None, student_ids: list[str] | None = None):
    envelope = json.loads(path.read_text(encoding="utf-8"))
    if student_id:
        return ingest_question_result(student_id, envelope)
    if student_ids:
        return ingest_risk_only_batch(student_ids, envelope)
    raise ValueError("必须指定一名出题学生或风险批次学号")
