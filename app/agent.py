"""诊断 Agent：分析学生原始提交，生成个性化诊断问卷。

LangChain 优化·第一批：
- 提示词要求超产 6~7 道题，装配阶段逐题校验、择优保留 3~5 道（安全库存）；
- 个别题目校验失败时走定点修复（只把坏题发回给模型修），不再整卷重试。
"""
from __future__ import annotations

import json
import logging

from pydantic import ValidationError

from . import cases, prompts
from .llm import LLMError, LLMProvider, extract_json, run_structured
from .models import Assignment, Question, Questionnaire, Submission
from .schemas import QuestionnaireDraft

log = logging.getLogger("adaptive_assessment.agent")

# Prompt 唯一事实来源：prompts/questionnaire.txt（版本 = 内容哈希，见 app.prompts）
SYSTEM_PROMPT = prompts.get_prompt("questionnaire")

_REPAIR_SYSTEM = (
    "你是一名 C 语言程序设计课程的助教 AI，负责修复诊断题目的 JSON 数据。"
    "只输出要求格式的 JSON，不要 markdown 代码块，不要解释文字。"
)


def build_assignment_prefix(assignment: Assignment) -> str:
    """冻结的作业上下文：所有学生字节相同，作为稳定前缀吃 provider 的 KV/前缀缓存。

    前置条件提示词与序列化都不得包含学生身份——这是内容寻址复用（app/reuse.py）
    把问卷视为纯函数的前提。
    """
    frozen = {
        "assignment": assignment.prompt,
        "requirements": assignment.requirements,
        "reference_answer": assignment.reference_answer,
        "rubric": assignment.rubric,
        "assessment_notes": assignment.assessment_notes,
    }
    return json.dumps(frozen, ensure_ascii=False, indent=2)


def build_student_suffix(submission: Submission) -> str:
    """每生不同的部分。不含任何学生身份（身份只在消息头，绝不进入可克隆载荷）。"""
    return json.dumps(
        {
            "submission_language": submission.language,
            "submission_code": submission.code,
            "source_files": [
                {"filename": source.filename, "line_start": source.line_start,
                 "line_count": len(source.code.splitlines())}
                for source in submission.source_files
            ],
            "submission_notes": submission.import_notes,
        },
        ensure_ascii=False,
        indent=2,
    )


def build_messages(assignment: Assignment, submission: Submission) -> list[dict]:
    import os

    output_lang = os.getenv("AA_OUTPUT_LANG", "zh").strip().lower() or "zh"
    user = (
        f"STUDENT_ID: {submission.student_id}\n"
        f"ASSIGNMENT_ID: {assignment.id}\n"
        f"OUTPUT_LANGUAGE: {output_lang}\n"
        "请分析以下学生提交并生成诊断问卷。作业上下文（对所有学生相同）如下：\n"
        + build_assignment_prefix(assignment)
        + "\n该学生的提交如下：\n"
        + build_student_suffix(submission)
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _pre_clean(data: dict) -> dict:
    """LLM 输出的宽松修复：问题 id 缺失/重复时统一重排为 q1..qn；severity 非法值兜底。"""
    questions = data.get("questions")
    if isinstance(questions, list):
        for i, q in enumerate(questions, start=1):
            if isinstance(q, dict):
                q.setdefault("id", f"q{i}")
                if not str(q.get("id", "")).strip():
                    q["id"] = f"q{i}"
        seen: set[str] = set()
        for i, q in enumerate(questions, start=1):
            if isinstance(q, dict):
                qid = str(q.get("id", "")).strip()
                if not qid or qid in seen:
                    q["id"] = f"q{i}"
                    qid = q["id"]
                seen.add(qid)
    diag = data.get("diagnosis")
    if isinstance(diag, dict):
        issues = diag.get("issues")
        if isinstance(issues, list):
            for issue in issues:
                if isinstance(issue, dict) and str(issue.get("severity", "")).lower() not in (
                    "high", "medium", "low",
                ):
                    issue["severity"] = "medium"
    return data


# ---------------------------------------------------------------- 逐题装配

def _partition_questions(questions: list) -> tuple[list[dict], list[tuple[dict, str]]]:
    """逐题用权威 Question 模型校验，返回 (合格题, [(坏题, 错误信息)])。"""
    valid: list[dict] = []
    invalid: list[tuple[dict, str]] = []
    for q in questions:
        if not isinstance(q, dict):
            invalid.append(({}, "题目不是 JSON 对象"))
            continue
        try:
            Question.model_validate(q)
            valid.append(q)
        except ValidationError as e:
            invalid.append((q, str(e)[:300]))
    return valid, invalid


def _pick_questions(valid: list[dict], limit: int = 5) -> list[dict]:
    """择优保留：优先覆盖不同 target_issue（个性化多样性），再按原顺序补齐，最多 limit 道。"""
    picked: list[dict] = []
    seen_issues: set[str] = set()
    for q in valid:
        issue = str(q.get("target_issue", ""))
        if issue and issue not in seen_issues:
            seen_issues.add(issue)
            picked.append(q)
    for q in valid:
        if len(picked) >= limit:
            break
        if q not in picked:
            picked.append(q)
    picked = picked[:limit]
    # 理解核验必须留下一道解释题，避免择优截断时只剩选择题。
    explanation = next((q for q in valid if q.get("type") == "text"), None)
    if picked and explanation and not any(q.get("type") == "text" for q in picked):
        picked[-1] = explanation
    return picked


def _repair_invalid_questions(
    provider: LLMProvider, questions: list, invalid: list[tuple[dict, str]]
) -> list[dict]:
    """定点修复：只把坏题及其校验错误发回给模型，独立小调用，不重生成整卷。"""
    if not invalid:
        return []
    detail = [
        {"question": q, "error": err}
        for q, err in invalid
        if q
    ]
    repair_messages = [
        {"role": "system", "content": _REPAIR_SYSTEM},
        {
            "role": "user",
            "content": (
                "以下题目未通过数据校验，请逐个修复（保持字段结构不变，"
                "expected_answer 必须是合法选项字母或要点文本）。"
                "只输出修复后的题目 JSON 数组，不要解释：\n"
                + json.dumps(detail, ensure_ascii=False, indent=2)
            ),
        },
    ]
    raw = provider.generate(repair_messages)
    fixed = extract_json(raw)
    items: list = []
    if isinstance(fixed, dict):
        items = fixed.get("questions") if isinstance(fixed.get("questions"), list) else []
    elif isinstance(fixed, list):
        items = [x for x in fixed if isinstance(x, dict)]
    fixed_ok, _ = _partition_questions(items)
    log.info("定点修复返回 %d 道合格题（坏题 %d 道）", len(fixed_ok), len(invalid))
    return fixed_ok


# 定点修复所需上下文见 generate_questionnaire 内的 _salvage 闭包。


def generate_questionnaire(
    provider: LLMProvider, assignment: Assignment, submission: Submission
) -> Questionnaire:
    messages = build_messages(assignment, submission)

    def _salvage(data: dict, exc: Exception) -> dict | None:
        """run_structured 的定点修复钩子：剔除/修复坏题后返回可装配的 dict。"""
        questions = data.get("questions")
        if not isinstance(questions, list):
            return None
        valid, invalid = _partition_questions([q for q in questions if isinstance(q, dict)])
        if len(valid) >= 3:
            return {**data, "questions": valid}
        fixed_ok = _repair_invalid_questions(provider, questions, invalid)
        if not fixed_ok:
            return None
        # 按原位置拼回：坏题依次用修复题替换，修不好的丢弃
        result: list[dict] = []
        it = iter(fixed_ok)
        for q in questions:
            if isinstance(q, dict):
                try:
                    Question.model_validate(q)
                    result.append(q)
                    continue
                except ValidationError:
                    pass
                cand = next(it, None)
                if isinstance(cand, dict):
                    try:
                        Question.model_validate(cand)
                        result.append(cand)
                    except ValidationError:
                        pass
        if len(result) >= 3:
            return {**data, "questions": result}
        return None

    def _clean(data: dict) -> dict:
        data = _pre_clean(data)
        # 权威字段以后端数据为准：真实模型不回显 student_id/assignment_id 也能过校验
        data.setdefault("student_id", submission.student_id)
        data.setdefault("assignment_id", assignment.id)
        return data

    def _assemble(draft: QuestionnaireDraft) -> Questionnaire:
        dumps = [q.model_dump(exclude_none=True) for q in draft.questions]
        valid, _ = _partition_questions(dumps)
        if len(valid) < 3:
            raise ValueError(f"有效题目不足 3 道（仅 {len(valid)} 道合格），需要重新生成")
        payload = draft.model_dump()
        payload["questions"] = _pick_questions(valid)
        payload["student_id"] = submission.student_id
        payload["assignment_id"] = assignment.id
        return Questionnaire.model_validate(payload)

    questionnaire = run_structured(
        provider,
        messages,
        Questionnaire,
        kind="questionnaire",
        pre_clean=_clean,
        draft_cls=QuestionnaireDraft,
        salvage=_salvage,
        assemble=_assemble,
        prompt_version=prompts.version("questionnaire"),
    )
    # 权威字段以后端数据为准，不信任模型回显；案例绑定由服务端按代码计算（docs §9）
    questionnaire.student_id = submission.student_id
    questionnaire.assignment_id = assignment.id
    questionnaire.generated_by = provider.name
    questionnaire.prompt_version = prompts.version("questionnaire")
    questionnaire.code_fingerprint = cases.code_fingerprint(submission.code)
    matched = cases.match_case(submission.code)
    if matched is not None:
        questionnaire.case_id = matched.case_id
        questionnaire.case_match = "exact"
        # 限时来自案例配置（服务端权威），不再信任模型回显的 payload 字段
        questionnaire.time_limit_minutes = matched.time_limit_minutes
    else:
        questionnaire.case_id = ""
        questionnaire.case_match = "none"
    return questionnaire
