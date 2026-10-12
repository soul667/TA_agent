"""模型侧草稿 Schema：只描述「LLM 应产出」的字段，同时作为结构化输出的 JSON Schema。

与 app/models.py 的权威模型分离（LangChain 优化·第一批）：
- 草稿不含任何服务器权威字段（questionnaire_id / version / code_fingerprint /
  case_id / generated_by / student_id / assignment_id / evaluated_by 等）——
  这些由后端装配时注入，模型输出一律不信任；
- 草稿层校验刻意宽松（定位到"哪道题坏了"即可），严格校验统一由权威模型
  Question / Questionnaire / Evaluation 在逐题装配时执行；
- questions 条数放宽到 3~7（超产安全库存）：坏题剔除后仍能凑足 3~5 道，
  避免为 1 道坏题整卷重新生成。
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from .models import Diagnosis, KnowledgePointResult, QuestionType, VariantSpec


class QuestionDraft(BaseModel):
    """一道题的模型产出视图。type 缺失（None）时装配阶段直接判废。"""

    id: str = ""
    type: Optional[QuestionType] = None
    question: str = ""
    question_en: str = ""
    choices: Optional[list[str]] = None
    choices_en: Optional[list[str]] = None
    target_issue: str = ""
    expected_answer: str = ""
    # 学生端展示“所问位置”的代码行号（1 基）
    code_lines: list[int] = []
    # 代码变异题：把第 line_no 行整行替换为 replacement（replacement 学生可见，非答案）
    variant: Optional[VariantSpec] = None
    stage: str = "initial"


class QuestionnaireDraft(BaseModel):
    """问卷草稿：3~7 道题（超产），权威 3~5 由装配阶段择优裁剪。"""

    diagnosis: Diagnosis = Field(default_factory=lambda: Diagnosis(summary=""))
    questions: list[QuestionDraft] = Field(default_factory=list, min_length=3, max_length=7)


class EvaluationDraft(BaseModel):
    """评价草稿：与 Evaluation 对应，去掉 student_id / evaluated_by 等权威字段。

    understanding / code_quality 用 str 接收，别名归一在 evaluator._pre_clean 完成，
    枚举约束由权威模型校验。
    """

    understanding: str = "partial"
    score: int = 0
    max_score: int = 10
    feedback: str = ""
    feedback_en: str = ""
    remaining_issues: list[str] = []
    remaining_issues_en: list[str] = []
    needs_ta_review: bool = False
    confidence: float = 0.8
    code_quality: str = "unknown"
    knowledge_points: list[KnowledgePointResult] = []
    basis: str = ""
