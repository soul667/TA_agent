"""评价 Agent：综合原始题目、原始提交、诊断、问卷与学生的最新回答给出评价。

与旧版的两点差异（审批 P1）：
1. 提示词不再预设“学生交过有问题的作业”——代码可能正确也可能有错，
   code_quality 与 understanding 是两个正交维度；
2. 输出必须包含逐知识点结论 knowledge_points（docs/evaluation-rules.md §2），
   允许“证据不足 / 待核验（uncovered）”，每个结论关联具体回答与代码依据。

Mock 判分上下文（GRADING_DATA_JSON / CASE_META_JSON）由本模块构造：
- 样例与案例元数据只来自服务端匹配的案例，学生请求无法注入（docs §9）。
"""
from __future__ import annotations

import json
import os

from . import cases, prompts
from .llm import LLMProvider, run_structured
from .models import Assignment, Evaluation, Questionnaire, ResponseFile, Submission
from .schemas import EvaluationDraft

# Prompt 唯一事实来源：prompts/evaluation.txt（版本 = 内容哈希，见 app.prompts）
SYSTEM_PROMPT = prompts.get_prompt("evaluation")

_UNDERSTANDING_ALIASES = {
    "mastered": "mastered", "master": "mastered", "good": "mastered",
    "full": "mastered", "掌握": "mastered",
    "partial": "partial", "partially": "partial", "部分理解": "partial",
    "limited": "limited", "weak": "limited", "理解有限": "limited",
    "insufficient": "insufficient", "none": "insufficient", "poor": "insufficient",
    "基本未理解": "insufficient",
}


def _pre_clean(data: dict) -> dict:
    u = str(data.get("understanding", "")).strip().lower()
    data["understanding"] = _UNDERSTANDING_ALIASES.get(u, "partial")
    if str(data.get("code_quality", "")).strip().lower() not in ("correct", "incorrect"):
        data["code_quality"] = "unknown"
    if not isinstance(data.get("knowledge_points"), list):
        data["knowledge_points"] = []
    # 双语字段兜底：模型漏掉英文版时回退到中文原值
    if not str(data.get("feedback_en", "") or "").strip():
        data["feedback_en"] = str(data.get("feedback", "") or "")
    if not isinstance(data.get("remaining_issues_en"), list):
        data["remaining_issues_en"] = []
    return data


def _code_evidence(submission: Submission, code_lines: list[int]) -> str:
    """按问卷标注的行号从学生代码中摘出所问位置（学生端与教师端展示同源）。"""
    if not code_lines:
        return ""
    lines = submission.code.splitlines()
    picked = [lines[n - 1].strip() for n in code_lines if 1 <= n <= len(lines)]
    return " | ".join(picked)


def build_messages(
    assignment: Assignment,
    submission: Submission,
    questionnaire: Questionnaire,
    response: ResponseFile,
) -> list[dict]:
    case = cases.load_case(questionnaire.case_id) if questionnaire.case_id else None
    case_questions = {q.id: q for q in case.questions} if case else {}

    items = []
    for q in questionnaire.questions:
        cq = case_questions.get(q.id)
        variant = q.variant.model_dump() if q.variant else None
        items.append({
            "qid": q.id,
            "type": q.type.value,
            "target": q.target_issue,
            "target_zh": cq.target_zh if cq else q.target_issue,
            "target_en": (cq.target_en if cq else "") or q.target_issue,
            "expected": q.expected_answer,
            "answer": response.answers.get(q.id, ""),
            "samples": [s.model_dump() for s in cq.samples] if cq else [],
            "code_evidence": _code_evidence(submission, q.code_lines),
            "variant": variant,
        })

    context = {
        "assignment": {
            "id": assignment.id,
            "prompt": assignment.prompt,
            "rubric": assignment.rubric,
            "reference_answer": assignment.reference_answer,
        },
        "student_original_submission": submission.code,
        "previous_diagnosis": questionnaire.diagnosis.model_dump(mode="json"),
        "questions_and_answers": [
            {
                "qid": q.id,
                "type": q.type.value,
                "question": q.question,
                "choices": q.choices,
                "target_issue": q.target_issue,
                "expected_answer": q.expected_answer,
                "student_answer": response.answers.get(q.id, ""),
            }
            for q in questionnaire.questions
        ],
    }
    # Mock 模式通过这两行机器可读数据做确定性判分；真实 LLM 也会参考它
    grading_line = "GRADING_DATA_JSON: " + json.dumps(items, ensure_ascii=False)
    case_meta = {
        "case_id": case.case_id if case else "",
        "code_quality": case.code_quality if case else "unknown",
        "case_title": case.title if case else "",
    }
    case_line = "CASE_META_JSON: " + json.dumps(case_meta, ensure_ascii=False)
    output_lang = os.getenv("AA_OUTPUT_LANG", "zh").strip().lower() or "zh"
    user = (
        f"STUDENT_ID: {submission.student_id}\n"
        f"OUTPUT_LANGUAGE: {output_lang}\n"
        "请评价该学生的问卷表现。全部证据如下：\n"
        + json.dumps(context, ensure_ascii=False, indent=2)
        + "\n" + grading_line
        + "\n" + case_line
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def evaluate(
    provider: LLMProvider,
    assignment: Assignment,
    submission: Submission,
    questionnaire: Questionnaire,
    response: ResponseFile,
) -> Evaluation:
    messages = build_messages(assignment, submission, questionnaire, response)

    def _clean(data: dict) -> dict:
        data = _pre_clean(data)
        # 真实模型不回显 student_id 时由后端补齐（权威字段）
        data.setdefault("student_id", submission.student_id)
        return data

    def _assemble(draft: EvaluationDraft) -> Evaluation:
        payload = draft.model_dump()
        payload["student_id"] = submission.student_id
        return Evaluation.model_validate(payload)

    evaluation = run_structured(
        provider,
        messages,
        Evaluation,
        kind="evaluation",
        pre_clean=_clean,
        draft_cls=EvaluationDraft,
        assemble=_assemble,
        prompt_version=prompts.version("evaluation"),
    )
    evaluation.student_id = submission.student_id
    evaluation.evaluated_by = provider.name
    evaluation.prompt_version = prompts.version("evaluation")
    evaluation.based_on_attempt = response.attempt_count
    evaluation.questionnaire_id = questionnaire.questionnaire_id
    evaluation.questionnaire_version = questionnaire.version
    # 安全修复 R3（纵深防御）：学生文本作答中出现疑似提示词注入特征时，无论 LLM
    # 如何判定，结论一律转人工复核——被操纵的评价绝不会自动发布给学生
    if _suspect_injection(response.answers):
        evaluation.needs_ta_review = True
        marker = "（检测到答卷中含疑似提示注入内容，已转人工复核）"
        if marker not in evaluation.basis:
            evaluation.basis = (evaluation.basis + marker).strip("+")
    return evaluation


# 注入特征：中英文常见提示词操纵用语（命中任一即转人工，宁误报不漏报）
_INJECTION_PATTERNS = (
    "ignore previous", "ignore above", "ignore all", "disregard",
    "system prompt", "you are now", "act as", "pretend you",
    "give me full", "full marks", "maximum score", "set score",
    "needs_ta_review to false", "mark me as mastered",
    "忽略以上", "忽略之前", "忽略所有", "忽略上面的", "无视上述", " disregard ",
    "系统提示", "你现在", "扮演", "假装你是", "给我满分", "满分通过",
    "改成分数", "把分数", "标记为掌握", "无需人工",
)


def _suspect_injection(answers: dict) -> bool:
    """扫描学生文本作答是否含提示词注入特征（仅 text 题，选择题为字母无从注入）。"""
    for value in answers.values():
        if not isinstance(value, str):
            continue
        low = value.lower()
        for pat in _INJECTION_PATTERNS:
            if pat in low:
                return True
    return False
