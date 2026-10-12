"""基于 JSON 文件的存储层。

可靠性约定（docs/evaluation-rules.md §4）：
- 所有写入均为原子写：**唯一**临时文件名 + os.replace（避免并发请求共用同一 .tmp 互相覆盖）；
- 同一学生的提交 / 评价 / 重生成 / 复核通过每学生文件锁（flock）串行化；
- 问卷重新生成只递增版本，历史版本写入 generated/history/ 完整保留；
- 评价保存为 records 追加式 EvaluationFile，旧版单评价文件在加载时惰性迁移为历史记录。

目录根可通过环境变量 AA_ROOT 覆盖（测试用），默认为项目根目录。
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import Iterator, Optional

from .models import (
    Assignment,
    AttemptData,
    Evaluation,
    EvaluationFile,
    EvaluationRecord,
    GenerationJob,
    Questionnaire,
    ResponseFile,
    ReviewFile,
    Submission,
    now_utc,
)

DEFAULT_ROOT = Path(__file__).resolve().parent.parent


def root() -> Path:
    return Path(os.environ.get("AA_ROOT", str(DEFAULT_ROOT)))


def data_dir() -> Path:
    return root() / "data"


def submissions_dir() -> Path:
    return data_dir() / "submissions"


def generated_dir() -> Path:
    return root() / "generated"


def generated_history_dir() -> Path:
    return generated_dir() / "history"


def responses_dir() -> Path:
    return root() / "responses"


def evaluations_dir() -> Path:
    return root() / "evaluations"


def reviews_dir() -> Path:
    return root() / "reviews"


def locks_dir() -> Path:
    return root() / "locks"


def sessions_dir() -> Path:
    """服务端计时（安全修复 R2）：学生首次打开问卷的时间戳。"""
    return root() / "sessions"


def load_exam_session(student_id: str) -> Optional[dict]:
    return load_json(sessions_dir() / f"{student_id}.json")


def save_exam_session(student_id: str, payload: dict) -> None:
    save_json_atomic(sessions_dir() / f"{student_id}.json", payload)


def save_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 唯一临时文件名：同一目标的并发原子写不会互相踩踏
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp_name, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)


def load_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None  # 文件损坏按“不存在”处理，避免 500


@contextlib.contextmanager
def student_lock(student_id: str) -> Iterator[None]:
    """同一学生的跨请求互斥：进程内（线程池）与多进程（uvicorn workers）均成立。"""
    locks_dir().mkdir(parents=True, exist_ok=True)
    lock_path = locks_dir() / f"{student_id}.lock"
    with open(lock_path, "a+") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


# ---------- 作业与提交 ----------

def load_assignment() -> Assignment:
    folder = data_dir() / "assignment"
    if not folder.exists():
        raise FileNotFoundError("data/assignment/ 目录不存在")
    for path in sorted(folder.glob("*.json")):
        data = load_json(path)
        if data:
            return Assignment.model_validate(data)
    raise FileNotFoundError("data/assignment/ 下没有可用的作业 JSON 文件")


def list_submissions() -> list[Submission]:
    folder = submissions_dir()
    if not folder.exists():
        return []
    result = []
    for path in sorted(folder.glob("*.json")):
        data = load_json(path)
        if data:
            result.append(Submission.model_validate(data))
    return result


def load_submission(student_id: str) -> Optional[Submission]:
    data = load_json(submissions_dir() / f"{student_id}.json")
    return Submission.model_validate(data) if data else None


# ---------- 生成问卷（版本化） ----------

def save_questionnaire(q: Questionnaire) -> Questionnaire:
    """保存最新问卷，同时把该版本完整快照写入 history（只增不改）。"""
    generated_history_dir().mkdir(parents=True, exist_ok=True)
    save_json_atomic(
        generated_history_dir() / f"{q.student_id}_v{q.version}.json",
        q.model_dump(mode="json"),
    )
    save_json_atomic(generated_dir() / f"{q.student_id}.json", q.model_dump(mode="json"))
    return q


def load_questionnaire(student_id: str, version: Optional[int] = None) -> Optional[Questionnaire]:
    if version is None:
        data = load_json(generated_dir() / f"{student_id}.json")
    else:
        data = load_json(generated_history_dir() / f"{student_id}_v{version}.json")
        if data is None and version == 1:
            data = load_json(generated_dir() / f"{student_id}.json")
    return Questionnaire.model_validate(data) if data else None


def next_questionnaire_version(student_id: str) -> int:
    latest = load_questionnaire(student_id)
    return (latest.version + 1) if latest else 1


# ---------- 学生回答 ----------

def save_response(r: ResponseFile) -> None:
    save_json_atomic(
        responses_dir() / f"{r.student_id}.json", r.model_dump(mode="json")
    )


def load_response(student_id: str) -> Optional[ResponseFile]:
    data = load_json(responses_dir() / f"{student_id}.json")
    return ResponseFile.model_validate(data) if data else None


def find_attempt(response: ResponseFile, attempt_id: str) -> Optional[AttemptData]:
    for a in response.attempts:
        if a.attempt_id == attempt_id:
            return a
    return None


def find_attempt_by_token(response: ResponseFile, token: str) -> Optional[AttemptData]:
    for a in response.attempts:
        if token and a.submission_token == token:
            return a
    return None


def latest_attempt(response: ResponseFile) -> Optional[AttemptData]:
    return response.attempts[-1] if response.attempts else None


# ---------- 评价结果（records 追加式） ----------

def _migrate_legacy_evaluation(data: dict, student_id: str) -> EvaluationFile:
    """旧版单评价文件 → 一条历史记录（attempt_id 为空，永远不算“当前评价”）。"""
    try:
        evaluation = Evaluation.model_validate(data)
    except Exception:
        evaluation = None
    record = EvaluationRecord(
        student_id=student_id,
        attempt_id="",
        attempt_no=data.get("based_on_attempt", 1),
        status="ok",
        finished_at=now_utc(),
        result=evaluation,
    )
    return EvaluationFile(student_id=student_id, records=[record])


def load_evaluation_file(student_id: str) -> EvaluationFile:
    data = load_json(evaluations_dir() / f"{student_id}.json")
    if data is None:
        return EvaluationFile(student_id=student_id)
    if "records" in data and isinstance(data.get("records"), list):
        return EvaluationFile.model_validate(data)
    return _migrate_legacy_evaluation(data, student_id)


def save_evaluation_file(f: EvaluationFile) -> None:
    save_json_atomic(
        evaluations_dir() / f"{f.student_id}.json", f.dump_payload()
    )


def append_evaluation_record(record: EvaluationRecord) -> EvaluationFile:
    f = load_evaluation_file(record.student_id)
    f.records.append(record)
    save_evaluation_file(f)
    return f


def update_evaluation_record(record: EvaluationRecord) -> None:
    f = load_evaluation_file(record.student_id)
    for i, r in enumerate(f.records):
        if r.evaluation_id == record.evaluation_id:
            f.records[i] = record
            break
    else:
        f.records.append(record)
    save_evaluation_file(f)


def recover_interrupted_evaluations() -> int:
    """启动恢复：pending 记录视为进程中断，标记 failed(interrupted) 供重试（docs §4.7）。"""
    folder = evaluations_dir()
    if not folder.exists():
        return 0
    recovered = 0
    for path in sorted(folder.glob("*.json")):
        sid = path.stem
        f = load_evaluation_file(sid)
        changed = False
        for rec in f.records:
            if rec.status == "pending":
                rec.status = "failed"
                rec.error = "interrupted"
                rec.finished_at = now_utc()
                changed = True
                recovered += 1
        if changed:
            save_evaluation_file(f)
    return recovered


# ---------- 问卷再生成任务（异步 202，第三批） ----------

def generation_jobs_dir() -> Path:
    return generated_dir() / "jobs"


def load_generation_job(student_id: str) -> Optional[GenerationJob]:
    data = load_json(generation_jobs_dir() / f"{student_id}.json")
    return GenerationJob.model_validate(data) if data else None


def save_generation_job(job: GenerationJob) -> None:
    save_json_atomic(
        generation_jobs_dir() / f"{job.student_id}.json", job.model_dump(mode="json")
    )


def recover_interrupted_generation_jobs() -> int:
    """启动恢复：中断的 pending 生成任务标记 failed，避免教师端永远轮询。"""
    folder = generation_jobs_dir()
    if not folder.exists():
        return 0
    recovered = 0
    for path in sorted(folder.glob("*.json")):
        data = load_json(path)
        if not data or data.get("status") != "pending":
            continue
        job = GenerationJob.model_validate(data)
        job.status = "failed"
        job.error = "interrupted: 服务重启时生成任务中断，请重试"
        job.finished_at = now_utc()
        save_generation_job(job)
        recovered += 1
    return recovered


# ---------- 助教复核 ----------

def load_review_file(student_id: str) -> ReviewFile:
    data = load_json(reviews_dir() / f"{student_id}.json")
    return ReviewFile.model_validate(data) if data else ReviewFile(student_id=student_id)


def save_review_file(f: ReviewFile) -> None:
    save_json_atomic(reviews_dir() / f"{f.student_id}.json", f.model_dump(mode="json"))


# ---------- 访问凭据（最小身份认证，docs §6） ----------

TEACHER_TOKEN_ENV = "AA_TEACHER_TOKEN"


def access_dir() -> Path:
    return data_dir() / "access"


def load_tokens(create_if_missing: bool = True) -> dict:
    """返回 {"teacher_token": str, "students": {sid: token}}。

    - 教师口令优先取环境变量 AA_TEACHER_TOKEN；
    - 学生访问码首次访问时为全部已知学号自动生成并持久化（稳定不变）；
    - 教师口令未配置时**随机生成**一份写入文件（安全修复 R1：代码内不再有
      固定默认口令；请从本文件或启动日志查看后妥善保管）。
    """
    path = access_dir() / "tokens.json"
    data = load_json(path) or {}
    changed = False
    env_token = os.environ.get(TEACHER_TOKEN_ENV, "").strip()
    if env_token:
        if data.get("teacher_token") != env_token:
            data["teacher_token"] = env_token
            changed = True
    elif not data.get("teacher_token") or data.get("teacher_token") == "ta-demo-token":
        # 安全修复 R1：历史默认口令是公开知识（README/源码），检测到即自动轮换
        data["teacher_token"] = "t-" + secrets.token_hex(16)
        changed = True
        print(
            "[安全提示] 教师口令未配置或仍为历史默认值，已随机生成新口令："
            f"{data['teacher_token']}（保存在 {path}，请妥善保管；正式使用请配置环境变量 AA_TEACHER_TOKEN）"
        )
    students: dict = data.get("students") or {}
    if create_if_missing:
        for submission in list_submissions():
            if submission.student_id not in students:
                students[submission.student_id] = "s-" + secrets.token_hex(6)
                changed = True
    data["students"] = students
    if changed:
        save_json_atomic(path, data)
    return data
