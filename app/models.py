"""Pydantic 数据模型：作业、提交、诊断问卷、学生回答、评价结果、复核记录。

版本关联约定见 docs/evaluation-rules.md §4：
- 问卷携带 questionnaire_id + version + code_fingerprint + case_id；
- 每次作答是 attempts[] 中一条，绑定 questionnaire_id/version 与幂等 submission_token；
- 评价记录 records[] 逐条绑定 attempt_id；复核记录 reviews[] 绑定 attempt_id + evaluation_id。
新增字段全部带默认值，旧 JSON 文件可直接加载（version 视为 1）。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class QuestionType(str, Enum):
    single_choice = "single_choice"
    multiple_choice = "multiple_choice"
    text = "text"
    # 代码变异题：假设把某一行改成 replacement 后行为如何（点击题目左侧代码联动展示）
    code_variant = "code_variant"


class Severity(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class VariantSpec(BaseModel):
    """代码变异题的“假设修改”：把第 line_no 行整行替换为 replacement（学生可见）。"""
    line_no: int
    replacement: str


class Understanding(str, Enum):
    mastered = "mastered"
    partial = "partial"
    limited = "limited"
    insufficient = "insufficient"


UNDERSTANDING_ZH = {
    "mastered": "掌握",
    "partial": "部分理解",
    "limited": "理解有限",
    "insufficient": "基本未理解",
}

# 知识点结论状态：uncovered = 证据不足 / 待人工核验（docs §2）
KP_STATUS_ZH = {
    "mastered": "已表现出的理解",
    "partial": "部分理解",
    "limited": "理解有限",
    "insufficient": "未表现出理解",
    "uncovered": "证据不足 / 待核验",
}


class Assignment(BaseModel):
    id: str
    course: str = ""
    course_en: str = ""
    title: str = ""
    title_en: str = ""
    prompt: str
    requirements: list[str] = []
    reference_answer: str
    rubric: list[str] = []
    assessment_notes: list[str] = []
    requires_real_model: bool = False


class SourceFile(BaseModel):
    filename: str
    code: str
    archive_member: str = ""
    sha256: str = ""
    encoding: str = "utf-8"
    line_start: int = 1


class Submission(BaseModel):
    student_id: str
    assignment_id: str
    code: str
    language: str = "c"
    submitted_at: str = ""
    source_files: list[SourceFile] = []
    import_notes: list[str] = []
    submission_text: str = ""
    source_archive_sha256: str = ""


class Issue(BaseModel):
    type: str
    description: str
    description_en: str = ""
    evidence: str = ""
    severity: Severity = Severity.medium


class Diagnosis(BaseModel):
    summary: str
    summary_en: str = ""
    issues: list[Issue] = []


def _letters_ok(value: str, count: int, multi: bool) -> Optional[str]:
    """校验选择题 expected_answer 形如 "B" / "A,C"，返回规范化的值；非法返回 None。"""
    letters = [p.strip().upper() for p in str(value).split(",") if p.strip()]
    valid = [chr(ord("A") + i) for i in range(count)]
    if not letters or any(l not in valid for l in letters):
        return None
    if not multi and len(letters) != 1:
        return None
    uniq = sorted(set(letters))
    return ",".join(uniq)


class Question(BaseModel):
    id: str
    type: QuestionType
    question: str
    question_en: str = ""          # 英文版（双语切换；空则回退中文）
    choices: Optional[list[str]] = None
    choices_en: Optional[list[str]] = None
    target_issue: str = ""
    expected_answer: str
    # 学生端展示“所问位置”用的代码行号（1 基），不含任何内部信息
    code_lines: list[int] = []
    # 代码变异题：把某行改成 replacement 后行为如何（replacement 学生可见，非答案）
    variant: Optional[VariantSpec] = None
    # 阶段标记：initial = 首次作答题；followup = 提示后的核验题（docs §3）
    stage: str = "initial"

    @model_validator(mode="after")
    def _check_choices_and_answer(self) -> "Question":
        if self.type in (
            QuestionType.single_choice, QuestionType.multiple_choice, QuestionType.code_variant,
        ):
            if not self.choices or len(self.choices) < 2:
                raise ValueError(f"选择题 {self.id} 必须提供至少 2 个 choices")
            normalized = _letters_ok(
                self.expected_answer, len(self.choices),
                multi=self.type == QuestionType.multiple_choice,
            )
            if normalized is None:
                raise ValueError(
                    f"选择题 {self.id} 的 expected_answer 必须是合法选项字母"
                    f"（如 'B' 或 'A,C'），当前为 {self.expected_answer!r}"
                )
            self.expected_answer = normalized
        return self

    def public(self) -> dict:
        """发给学生端的视图：不含 expected_answer / target_issue / diagnosis。"""
        return {
            "id": self.id,
            "type": self.type.value,
            "question": self.question,
            "question_en": self.question_en,
            "choices": self.choices,
            "choices_en": self.choices_en,
            "code_lines": self.code_lines,
            "variant": self.variant.model_dump() if self.variant else None,
            "stage": self.stage,
        }


class Questionnaire(BaseModel):
    student_id: str
    assignment_id: str
    diagnosis: Diagnosis
    questions: list[Question]
    generated_at: datetime = Field(default_factory=now_utc)
    generated_by: str = "mock"
    # ---- 版本关联（docs §4）----
    questionnaire_id: str = Field(default_factory=new_id)
    version: int = 1
    code_fingerprint: str = ""   # 生成时所依据代码的规范化指纹
    source_fingerprint: str = "" # 抽查答卷绑定完整作业、原始代码及语言，防止沿用旧题
    case_id: str = ""            # 匹配到的 Mock 案例；未匹配为 ""
    case_match: str = "none"     # exact=按代码匹配 / legacy_sid=旧学号兜底 / none
    # 限时（分钟）；0 = 不限时。前端倒计时 + 到时自动提交，服务端记录超时标记
    time_limit_minutes: int = 0
    # 生成所用 prompt 版本（app.prompts.version，provenance 归因；旧文件视为空）
    prompt_version: str = ""
    # 内容寻址复用来源（第三批）：克隆自哪位学生的问卷；首次生成为空
    reused_from: str = ""

    @field_validator("questions")
    @classmethod
    def _question_count(cls, v: list[Question]) -> list[Question]:
        if not (3 <= len(v) <= 5):
            raise ValueError(f"问卷必须包含 3~5 道题，当前 {len(v)} 道")
        return v

    def public(self) -> dict:
        return {
            "student_id": self.student_id,
            "assignment_id": self.assignment_id,
            "questionnaire_id": self.questionnaire_id,
            "version": self.version,
            "generated_by": self.generated_by,
            "case_matched": bool(self.case_id),
            "questions": [q.public() for q in self.questions],
        }

    def normalize_answers(self, raw: dict[str, Any]) -> dict[str, Any]:
        """把学生提交的答案规范化。未知题目编号 / 非法选项字母抛 ValueError。"""
        known = {q.id: q for q in self.questions}
        for qid in raw:
            if qid not in known:
                raise ValueError(f"未知题目编号: {qid}")
        answers: dict[str, Any] = {}
        for q in self.questions:
            value = raw.get(q.id, "")
            if q.type in (QuestionType.single_choice, QuestionType.code_variant):
                s = str(value).strip().upper()
                if s == "":
                    answers[q.id] = ""
                    continue
                if _letters_ok(s, len(q.choices), multi=False) is None:
                    raise ValueError(f"题目 {q.id} 的答案必须是单个合法选项字母")
                answers[q.id] = s
            elif q.type == QuestionType.multiple_choice:
                if isinstance(value, str):
                    parts = [p for p in value.split(",")]
                elif isinstance(value, list):
                    parts = [str(p) for p in value]
                else:
                    raise ValueError(f"题目 {q.id} 的答案应为字母列表")
                letters = sorted({
                    p.strip().upper() for p in parts if p.strip()
                })
                if letters and _letters_ok(",".join(letters), len(q.choices), multi=True) is None:
                    raise ValueError(f"题目 {q.id} 的答案包含非法选项字母")
                answers[q.id] = letters
            else:
                answers[q.id] = str(value).strip()
        return answers


class AttemptData(BaseModel):
    """一次学生作答：绑定问卷版本，含幂等标识与评价状态。"""
    submitted_at: datetime
    answers: dict[str, Any]
    attempt_id: str = Field(default_factory=new_id)
    attempt_no: int = 1
    questionnaire_id: str = ""
    questionnaire_version: int = 1
    submission_token: str = ""   # 客户端提交请求唯一标识（幂等键）
    stage: str = "initial"       # initial / followup（docs §3）
    evaluation_status: str = "pending"  # pending / ok / failed / awaiting_review
    submission_source: str = "student"  # student / classroom
    source_code_snapshot: str = ""       # 现场抽查所依据的代码，随作答留档
    # 限时相关（客户端上报，服务端记录；不做硬性拒绝，仅标记）
    time_expired: bool = False
    duration_seconds: Optional[int] = None
    # 防作弊（安全修复 R7）：文本作答与其他学生的历史作答完全一致时标记疑似协同作弊，
    # 评价完成后强制 needs_ta_review，由助教人工判定（不自动扣分，避免误伤）
    collusion_suspect: bool = False
    collusion_with: list[str] = []


class ResponseFile(BaseModel):
    """responses/{student_id}.json：顶层字段始终反映最新一次提交，attempts 保留历史。"""
    student_id: str
    submitted_at: datetime
    answers: dict[str, Any]
    attempt_count: int = 1
    attempts: list[AttemptData] = []
    questionnaire_id: str = ""          # 顶层镜像最新一次作答
    questionnaire_version: int = 1
    attempt_id: str = ""


# ---------------- 评价：逐知识点证据（docs §2） ----------------

class KnowledgePointResult(BaseModel):
    point: str
    point_zh: str = ""
    point_en: str = ""
    question_ids: list[str] = []
    code_evidence: str = ""
    student_evidence: str = ""
    status: str  # mastered/partial/limited/insufficient/uncovered
    basis: str = "manual"  # exact-choice/sample-match/heuristic/uncovered/manual
    note: str = ""


class Evaluation(BaseModel):
    student_id: str
    understanding: Understanding
    score: int
    max_score: int = 10
    feedback: str
    feedback_en: str = ""          # 英文版反馈（双语切换；空则前端回退中文）
    remaining_issues: list[str] = []
    remaining_issues_en: list[str] = []
    needs_ta_review: bool = False
    confidence: float = 0.8
    evaluated_at: datetime = Field(default_factory=now_utc)
    evaluated_by: str = "mock"
    # 评价所用 prompt 版本（app.prompts.version，provenance 归因；旧文件视为空）
    prompt_version: str = ""
    based_on_attempt: int = 1
    # ---- 结构化扩展 ----
    code_quality: str = "unknown"  # correct/incorrect/unknown（与 understanding 正交）
    knowledge_points: list[KnowledgePointResult] = []
    basis: str = ""                # 整体判分来源说明
    attempt_id: str = ""
    questionnaire_id: str = ""
    questionnaire_version: int = 1

    @model_validator(mode="after")
    def _clamp(self) -> "Evaluation":
        if not (0 <= self.score <= self.max_score):
            raise ValueError(f"score 必须在 0~{self.max_score} 之间")
        self.confidence = max(0.0, min(1.0, self.confidence))
        return self


class EvaluationRecord(BaseModel):
    """一次评价运行：只针对某个 attempt；status 与结果分离保存（P0）。"""
    evaluation_id: str = Field(default_factory=new_id)
    student_id: str
    attempt_id: str
    attempt_no: int = 1
    questionnaire_id: str = ""
    questionnaire_version: int = 1
    status: str = "pending"  # pending / ok / failed
    error: str = ""
    started_at: datetime = Field(default_factory=now_utc)
    finished_at: Optional[datetime] = None
    result: Optional[Evaluation] = None


class EvaluationFile(BaseModel):
    """evaluations/{student_id}.json：records 只追加；当前评价 = 最新 attempt 的最新记录。"""
    student_id: str
    records: list[EvaluationRecord] = []

    def records_for(self, attempt_id: str) -> list[EvaluationRecord]:
        return [r for r in self.records if r.attempt_id == attempt_id]

    def current(self, latest_attempt_id: Optional[str]) -> Optional[EvaluationRecord]:
        """当前 attempt 的最新评价记录；无则 None（旧结果不冒充当前结果）。"""
        if latest_attempt_id is None:
            return None
        recs = self.records_for(latest_attempt_id)
        return recs[-1] if recs else None

    def dump_payload(self) -> dict:
        """落盘格式：顶层 = 当前最新成功评价的字段（兼容旧读取方）+ bookkeeping + records。"""
        data = self.model_dump(mode="json")
        latest_ok: Optional[EvaluationRecord] = None
        for rec in reversed(self.records):
            if rec.status == "ok" and rec.result is not None:
                latest_ok = rec
                break
        if latest_ok is not None:
            result = latest_ok.result.model_dump(mode="json")
            result["attempt_id"] = latest_ok.attempt_id
            result["evaluation_status"] = latest_ok.status
            data = {**result, "student_id": self.student_id,
                    "current_attempt_evaluated": latest_ok.attempt_id, "records": data["records"]}
        return data


# ---------------- 助教复核（docs §8） ----------------

class GenerationJob(BaseModel):
    """一次问卷再生成任务：异步 202 模式（第三批），复用评价的 pending/ok/failed 约定。"""
    job_id: str = Field(default_factory=new_id)
    student_id: str
    status: str = "pending"   # pending / ok / failed
    error: str = ""
    started_at: datetime = Field(default_factory=now_utc)
    finished_at: Optional[datetime] = None
    version: int = 0          # ok 时的新问卷版本号
    reused_from: str = ""     # ok 且来自内容复用时为来源学生


class ReviewRecord(BaseModel):
    review_id: str = Field(default_factory=new_id)
    student_id: str
    attempt_id: str
    evaluation_id: str = ""
    reviewer: str = "ta"
    decision: str = "confirmed"   # confirmed / revised
    revised_understanding: Optional[Understanding] = None
    comment: str = ""             # 复核理由（revised 时必填）
    feedback_to_student: str = ""
    published: bool = False
    created_at: datetime = Field(default_factory=now_utc)


class ReviewFile(BaseModel):
    student_id: str
    reviews: list[ReviewRecord] = []

    def latest_for(self, attempt_id: str) -> Optional[ReviewRecord]:
        recs = [r for r in self.reviews if r.attempt_id == attempt_id]
        return recs[-1] if recs else None
