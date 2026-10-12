"""Version-bound answer receipts and the TA's question/answer review view."""
from . import storage


def answer_receipt(student_id, attempt):
    return {
        "student_id": student_id, "attempt_id": attempt.attempt_id,
        "attempt": attempt.attempt_no, "submitted_at": attempt.submitted_at.isoformat(),
        "questionnaire_id": attempt.questionnaire_id,
        "questionnaire_version": attempt.questionnaire_version,
        "answers": attempt.answers, "submission_token": attempt.submission_token,
        "evaluation_status": attempt.evaluation_status,
    }


def current_paper_answer(student_id, questionnaire):
    response = storage.load_response(student_id)
    if response:
        for attempt in reversed(response.attempts):
            if (attempt.questionnaire_id == questionnaire.questionnaire_id
                    and attempt.questionnaire_version == questionnaire.version):
                return answer_receipt(student_id, attempt)
    return None


def latest_answer_id(student_id):
    response = storage.load_response(student_id)
    return response.attempts[-1].attempt_id if response and response.attempts else ""


def answer_history(student_id):
    response = storage.load_response(student_id)
    if not response:
        return []
    current = storage.load_questionnaire(student_id)
    result = []
    for attempt in reversed(response.attempts):
        q = storage.load_questionnaire(student_id, attempt.questionnaire_version)
        if q and q.questionnaire_id != attempt.questionnaire_id:
            q = None  # 历史快照不可用时，不把同名 q1 等旧答案配到新版问题。
        result.append({
            **answer_receipt(student_id, attempt),
            "is_current_paper": bool(current and attempt.questionnaire_id == current.questionnaire_id),
            "source_code": attempt.source_code_snapshot,
            "questions": [{**question.public(), "answer": attempt.answers.get(question.id, ""),
                           "expected_answer": question.expected_answer} for question in q.questions] if q else [],
        })
    return result
