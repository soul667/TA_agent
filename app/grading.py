"""Authenticated TA roster, submission viewing and versioned manual grading."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from datetime import datetime
from pathlib import PurePosixPath

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

from . import auth, storage
from .import_blackboard import ENTRY
from .models import Submission, new_id, now_utc
from .source import paper_source_fingerprint
from .answers import answer_history, latest_answer_id
from .question_risk import read_status as read_question_risk

router = APIRouter(prefix="/api/ta", tags=["TA grading"])


class GradeInput(BaseModel):
    score: float = Field(ge=0, le=100, strict=True, allow_inf_nan=False)
    comment: str = Field(default="", max_length=8000)
    grader_name: str = Field(min_length=1, max_length=80)
    revision: int = Field(ge=0, strict=True)
    submission_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    save_token: str = Field(min_length=16, max_length=100)
    answer_attempt_id: str = Field(default="", max_length=100)

    @field_validator("grader_name")
    @classmethod
    def nonblank_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("请填写评分人")
        return value


class GradeRecord(BaseModel):
    record_id: str = Field(default_factory=new_id)
    score: float = Field(ge=0, le=100, allow_inf_nan=False)
    max_score: int = 100
    comment: str = ""
    grader_name: str
    saved_at: datetime = Field(default_factory=now_utc)
    submission_fingerprint: str
    save_token: str
    submission_snapshot: dict
    answer_attempt_id: str = ""


class GradeFile(BaseModel):
    student_id: str
    assignment_id: str
    records: list[GradeRecord] = []


def _submission(sid: str) -> Submission:
    if not re.fullmatch(r"[0-9]{4,20}", sid):
        raise HTTPException(400, "学号格式不正确")
    submission = storage.load_submission(sid)
    if submission is None:
        raise HTTPException(404, "学号不存在")
    if submission.student_id != sid or submission.assignment_id != storage.load_assignment().id:
        raise HTTPException(409, "学生提交与当前作业不匹配")
    return submission


def submission_fingerprint(submission: Submission) -> str:
    raw = json.dumps(submission.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _grade_path(sid):
    return storage.root() / "grades" / f"{sid}.json"


def _grades(submission: Submission) -> GradeFile:
    data = storage.load_json(_grade_path(submission.student_id))
    if data is None:
        if _grade_path(submission.student_id).exists():
            raise HTTPException(500, "评分记录无法读取，请检查保存文件，避免覆盖原记录")
        return GradeFile(student_id=submission.student_id, assignment_id=submission.assignment_id)
    grade_file = GradeFile.model_validate(data)
    if grade_file.student_id != submission.student_id or grade_file.assignment_id != submission.assignment_id:
        raise HTTPException(409, "评分记录与当前学生或作业不匹配")
    return grade_file


def _inspection(submission: Submission):
    path = storage.root() / "inspections" / f"{submission.student_id}.json"
    data = storage.load_json(path)
    if data is None:
        return None
    if data.get("student_id") != submission.student_id or data.get("assignment_id") != submission.assignment_id:
        raise HTTPException(409, "源码检查记录与当前学生或作业不匹配")
    return {**data, "is_current": data.get("submission_fingerprint") == submission_fingerprint(submission)}


def _record_payload(record: GradeRecord | None):
    return record.model_dump(mode="json", exclude={"save_token", "submission_snapshot"}) if record else None


def _state(submission, grades):
    record = grades.records[-1] if grades.records else None
    if record is None:
        return "ungraded"
    unchanged = (record.submission_fingerprint == submission_fingerprint(submission)
                 and record.answer_attempt_id == latest_answer_id(submission.student_id))
    return "graded" if unchanged else "needs_review"


def _paper_state(submission, assignment):
    if not submission.code.strip():
        return "missing_source"
    q = storage.load_questionnaire(submission.student_id)
    if q is None:
        return "not_generated"
    if q.assignment_id == assignment.id and q.source_fingerprint == paper_source_fingerprint(submission, assignment):
        return "ready"
    return "outdated"


def _archive_members(submission):
    digest = submission.source_archive_sha256
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        return None, []
    manifest = storage.load_json(storage.data_dir() / "imports" / f"{digest}.json")
    if not manifest:
        return None, []
    source_dir = (storage.root() / "source").resolve()
    archive_path = (storage.root() / manifest["original_archive"]).resolve()
    if not archive_path.is_relative_to(source_dir) or not archive_path.is_file():
        return None, []
    members = []
    with zipfile.ZipFile(archive_path) as z:
        for info in z.infolist():
            match = ENTRY.fullmatch(info.filename)
            if match and match['sid'] == submission.student_id:
                tail = match['tail']
                if tail == '.txt' or tail.startswith('_'):
                    members.append({"member": info.filename, "stamp": match['stamp'],
                        "name": tail[1:] if tail.startswith('_') else 'Blackboard 提交记录.txt',
                        "size": info.file_size})
    if members:
        latest = max(m['stamp'] for m in members)
        members = [m for m in members if m['stamp'] == latest]
    return archive_path, members


def _detail(submission, grades):
    assignment = storage.load_assignment()
    _, members = _archive_members(submission)
    inspection = _inspection(submission)
    return {
        "student_id": submission.student_id,
        "assignment_id": assignment.id, "assignment_title": assignment.title,
        "max_score": 100, "can_generate": bool(submission.code.strip()),
        "paper_status": _paper_state(submission, assignment),
        "submitted_at": submission.submitted_at,
        "source_files": [s.model_dump(exclude={"archive_member", "sha256"}) for s in submission.source_files],
        "code": submission.code, "language": submission.language,
        "submission_text": submission.submission_text,
        "import_notes": submission.import_notes,
        "attachments": [{"name": m['name'], "size": m['size']} for m in members],
        "download_available": bool(members),
        "submission_fingerprint": submission_fingerprint(submission),
        "revision": len(grades.records),
        "grading_status": _state(submission, grades),
        "current_grade": _record_payload(grades.records[-1] if grades.records else None),
        "grade_history": [_record_payload(record) for record in reversed(grades.records)],
        "inspection": inspection,
        "question_risk": read_question_risk(submission, assignment),
        "student_name": (inspection or {}).get("student_name", ""),
        "answer_attempts": answer_history(submission.student_id),
        "latest_answer_attempt_id": latest_answer_id(submission.student_id),
    }


def _roster():
    assignment = storage.load_assignment()
    rows = []
    for submission in storage.list_submissions():
        if submission.assignment_id != assignment.id:
            continue
        grades = _grades(submission)
        inspection = _inspection(submission)
        question_risk = read_question_risk(submission, assignment)
        rows.append({
            "student_id": submission.student_id, "submitted_at": submission.submitted_at,
            "files": [s.filename for s in submission.source_files],
            "source_count": len(submission.source_files) or int(bool(submission.code.strip())),
            "can_generate": bool(submission.code.strip()),
            "paper_status": _paper_state(submission, assignment),
            "grading_status": _state(submission, grades),
            "grade": _record_payload(grades.records[-1] if grades.records else None),
            "inspection_status": "reviewed" if inspection and inspection["is_current"] else "outdated" if inspection else "none",
            "question_risk": {"status": question_risk["status"], "priority": question_risk.get("priority"),
                              "ai_assistance_cue": question_risk.get("ai_assistance_cue", False)},
            "student_name": inspection.get("student_name", "") if inspection else "",
            "has_answers": bool(latest_answer_id(submission.student_id)),
        })
    rows.sort(key=lambda row: row['student_id'])
    return {"assignment_id": assignment.id, "assignment_title": assignment.title,
            "max_score": 100, "rows": rows}


@router.get("/students")
def students(request: Request):
    auth.require_teacher(request)
    return _roster()


@router.get("/students/{student_id}")
def student(student_id: str, request: Request):
    auth.require_teacher(request)
    _submission(student_id)
    with storage.student_lock(student_id):
        submission = _submission(student_id)
        return _detail(submission, _grades(submission))


@router.post("/students/{student_id}/grade")
def save_grade(student_id: str, body: GradeInput, request: Request):
    auth.require_teacher(request)
    _submission(student_id)  # 在构造锁文件路径前验证学号。
    with storage.student_lock(student_id):
        submission = _submission(student_id)
        grades = _grades(submission)
        for record in grades.records:
            if record.save_token == body.save_token:
                if (record.score, record.comment, record.grader_name, record.submission_fingerprint, record.answer_attempt_id) != (
                        body.score, body.comment, body.grader_name, body.submission_fingerprint, body.answer_attempt_id):
                    raise HTTPException(409, "保存编号已用于不同的评分，请重新载入后保存")
                return {**_detail(submission, grades), "already_saved": True}
        if body.submission_fingerprint != submission_fingerprint(submission):
            raise HTTPException(409, "学生提交已经更新，请重新载入提交后评分；本次评分尚未保存")
        if body.revision != len(grades.records):
            raise HTTPException(409, "另一位 TA 已更新评分，请重新载入最新记录后再保存；本次评分尚未保存")
        if body.answer_attempt_id != latest_answer_id(student_id):
            raise HTTPException(409, "学生刚提交了新的抽查答案，请重新载入并查看后评分；本次评分尚未保存")
        grades.records.append(GradeRecord(score=body.score, comment=body.comment,
            grader_name=body.grader_name, submission_fingerprint=body.submission_fingerprint,
            save_token=body.save_token, submission_snapshot=submission.model_dump(mode="json"),
            answer_attempt_id=body.answer_attempt_id))
        storage.save_json_atomic(_grade_path(student_id), grades.model_dump(mode="json"))
        return {**_detail(submission, grades), "already_saved": False}


@router.get("/students/{student_id}/download")
def download_submission(student_id: str, request: Request):
    auth.require_teacher(request)
    submission = _submission(student_id)
    archive_path, members = _archive_members(submission)
    if not archive_path or not members:
        raise HTTPException(404, "没有可下载的原始提交包")
    output = io.BytesIO()
    with zipfile.ZipFile(archive_path) as source, zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as target:
        for index, member in enumerate(members):
            # 不沿用上传者提供的目录路径；只输出该学生的文件，绝不发送全班 ZIP。
            filename = PurePosixPath(member['name'].replace('\\', '/')).name
            if filename in ('', '.', '..'):
                filename = f'attachment-{index}'
            target.writestr(filename, source.read(member['member']))
    return Response(output.getvalue(), media_type='application/zip', headers={
        'Content-Disposition': f'attachment; filename="submission-{student_id}.zip"'})


def _csv_safe(value):
    text = str(value) if value is not None else ''
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r')) else text


@router.get("/grades.csv")
def export_grades(request: Request):
    auth.require_teacher(request)
    roster = _roster()
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(['学号', '作业', '提交时间', '可生成答卷', '评分状态', '得分', '满分', '评分人', '评语', '评分时间'])
    for row in roster['rows']:
        grade = row['grade'] or {}
        writer.writerow([_csv_safe(v) for v in [
            row['student_id'], roster['assignment_title'], row['submitted_at'], '是' if row['can_generate'] else '否',
            {'ungraded':'未评分', 'graded':'已评分', 'needs_review':'提交已变更，需重评'}[row['grading_status']],
            grade.get('score'), 100, grade.get('grader_name'), grade.get('comment'), grade.get('saved_at')]])
    return Response(('\ufeff' + output.getvalue()).encode('utf-8'), media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition':'attachment; filename="pa1-grades.csv"'})
