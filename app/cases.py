"""Mock 案例库：data/mock_cases/ 的加载与“按代码内容匹配”（docs §9）。

- 诊断匹配依据是**规范化后的代码内容**，与学生学号解耦：同一案例换学号诊断不变；
  相同学号换代码按新代码重新匹配。
- 每份案例含：代码、预设诊断、3~5 道题（text 题带作答样例与变体）、4 组预置作答
  档案（解释充分 / 部分理解 / 解释矛盾 / 证据不足）。
- 预置作答复仅供演示与测试基线，绝不自动写入学生提交记录。
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional

from pydantic import BaseModel, Field

from . import storage


def normalize_code(code: str) -> str:
    """去掉全部空白字符后比较，使缩进 / 换行 / 空格差异不影响案例匹配。"""
    return re.sub(r"\s+", "", code or "")


def code_fingerprint(code: str) -> str:
    return hashlib.sha1(normalize_code(code).encode("utf-8")).hexdigest()[:12]


class CaseSample(BaseModel):
    """text 题的一条预置作答样例：verdict + 显式变体列表（docs §2 basis=sample-match）。"""
    verdict: str            # correct / partial / wrong / insufficient
    variants: list[str]
    note: str = ""          # 命中该样例时的反馈句（中文）
    note_en: str = ""       # 英文反馈句（双语）


class CaseQuestion(BaseModel):
    id: str
    type: str
    question: str
    question_en: str = ""
    choices: Optional[list[str]] = None
    choices_en: Optional[list[str]] = None
    target_issue: str = ""
    target_zh: str = ""
    target_en: str = ""
    expected_answer: str
    code_lines: list[int] = []
    stage: str = "initial"
    variant: Optional[dict] = None   # {line_no, replacement}（code_variant 题）
    samples: list[CaseSample] = []


class CaseProfile(BaseModel):
    profile_id: str         # mastered / partial / contradictory / insufficient
    label: str
    answers: dict
    expect: dict = Field(default_factory=dict)
    # expect: {"understanding": "...", "needs_ta_review": bool}


class MockCase(BaseModel):
    case_id: str
    title: str = ""
    title_en: str = ""
    assignment_id: str = "c-find-max"
    case_version: int = 1
    review_status: str = "draft"   # draft / reviewed
    code: str
    code_quality: str = "unknown"  # correct / incorrect / unknown
    behavior_notes: str = ""
    behavior_notes_en: str = ""
    time_limit_minutes: int = 20   # 问卷限时（分钟），0 = 不限时
    verification_points: list[str] = []
    input_coverage: list[str] = []
    diagnosis: dict
    questions: list[CaseQuestion]
    profiles: list[CaseProfile] = []


def cases_dir():
    return storage.data_dir() / "mock_cases"


def list_cases() -> list[MockCase]:
    result: list[MockCase] = []
    folder = cases_dir()
    if not folder.exists():
        return result
    import json

    for path in sorted(folder.glob("*.json")):
        data = storage.load_json(path)
        if data and isinstance(data, dict) and data.get("case_id"):
            result.append(MockCase.model_validate(data))
    return result


def load_case(case_id: str) -> Optional[MockCase]:
    for case in list_cases():
        if case.case_id == case_id:
            return case
    return None


def match_case(code: str) -> Optional[MockCase]:
    """按规范化代码内容精确匹配案例；未匹配返回 None（上层转通用问卷 + 待人工核验）。"""
    normalized = normalize_code(code)
    if not normalized:
        return None
    for case in list_cases():
        if normalize_code(case.code) == normalized:
            return case
    return None


# 旧学号兜底：仅当提示词里完全没有代码时使用（真实链路提示词始终含
# submission_code，此映射只服务历史直连调用，见 llm.MockLLMProvider._diagnose）。
LEGACY_SID_CASES = {
    "20260001": "C01",
    "20260002": "C02",
    "20260003": "C03",
    "20260004": "C04",
    "20260005": "C05",
    "20260006": "C06",
}


# 兜底：代码未匹配到任何案例时使用的通用问卷（明确标注待人工核验，docs §9）
GENERIC_QUESTIONNAIRE = {
    "diagnosis": {
        "summary": (
            "演示规则未覆盖该代码（Mock 案例库中无匹配），使用通用诊断问卷，"
            "结果待人工核验。"
        ),
        "summary_en": (
            "This code is not covered by the demo rules (no match in the mock case "
            "library). A generic questionnaire is used; results await manual review."
        ),
        "issues": [
            {
                "type": "uncovered",
                "description": "代码未匹配到预置案例，诊断与评价均需人工核验",
                "description_en": "Code did not match any preset case; diagnosis and evaluation need manual review.",
                "evidence": "",
                "severity": "low",
            }
        ],
    },
    "questions": [
        {
            "id": "q1",
            "type": "single_choice",
            "question": "长度为 n 的 C 数组 arr，合法的下标范围是？",
            "question_en": "For a C array arr of length n, what is the range of valid indices?",
            "choices": ["1 到 n", "0 到 n-1", "0 到 n", "1 到 n-1"],
            "choices_en": ["1 to n", "0 to n-1", "0 to n", "1 to n-1"],
            "target_issue": "index-range",
            "target_zh": "下标范围",
            "target_en": "index range",
            "expected_answer": "B",
        },
        {
            "id": "q2",
            "type": "single_choice",
            "question": "以下哪个更适合作为“求最大值”循环中 max 的初始值？",
            "question_en": (
                "Which of these is the better initial value for max in a \"find the maximum\" loop?"
            ),
            "choices": [
                "常数 0",
                "arr[0]（数组中的第一个元素）",
                "一个足够大的常数，比如 1000000",
                "不初始化，直接开始比较",
            ],
            "choices_en": [
                "The constant 0",
                "arr[0] (the first element of the array)",
                "A sufficiently large constant, e.g. 1000000",
                "Leave it uninitialized and start comparing right away",
            ],
            "target_issue": "initialization",
            "target_zh": "初始化",
            "target_en": "initialization",
            "expected_answer": "B",
        },
        {
            "id": "q3",
            "type": "single_choice",
            "question": (
                "若希望调用者能写 int m = find_max(arr, n); 并得到正确结果，函数必须做什么？"
            ),
            "question_en": (
                "For the caller to write int m = find_max(arr, n); and get the correct "
                "result, what must the function do?"
            ),
            "choices": [
                "在结束前用 return 语句把结果返回给调用者",
                "把 max 声明为全局变量，否则永远不行",
                "把结果写入文件再让调用者读取",
                "什么都不需要做，返回值会自动可用",
            ],
            "choices_en": [
                "Return the result to the caller with a return statement before exiting",
                "Declare max as a global variable, otherwise it never works",
                "Write the result to a file for the caller to read",
                "Nothing — the return value is automatically available",
            ],
            "target_issue": "return-mechanism",
            "target_zh": "返回机制",
            "target_en": "return mechanism",
            "expected_answer": "A",
        },
    ],
    "case_id": "",
    "case_match": "none",
}


def case_to_questionnaire_payload(case: MockCase, student_id: str, assignment_id: str) -> dict:
    """把案例转成 Mock Provider 的问卷输出（与 Questionnaire 模型字段对齐）。"""
    return {
        "student_id": student_id,
        "assignment_id": assignment_id,
        "diagnosis": case.diagnosis,
        "questions": [
            {
                "id": q.id,
                "type": q.type,
                "question": q.question,
                "question_en": q.question_en,
                "choices": q.choices,
                "choices_en": q.choices_en,
                "target_issue": q.target_issue,
                "expected_answer": q.expected_answer,
                "code_lines": q.code_lines,
                "stage": q.stage,
                "variant": q.variant,
            }
            for q in case.questions
        ],
        "case_id": case.case_id,
        "case_match": "exact",
        "code_fingerprint": code_fingerprint(case.code),
        "time_limit_minutes": case.time_limit_minutes,
    }
