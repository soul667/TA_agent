from app import storage
from app.grading import submission_fingerprint
from app.models import SourceFile, Submission
from app.question_risk import QuestionRisk, read_status, store_review
from app.source import paper_source_fingerprint


def _sample():
    assignment = storage.load_assignment()
    submission = Submission(student_id='20260002', assignment_id=assignment.id,
        code='// FILE: "arith.c"\nint main(){return 0;}\n',
        source_files=[SourceFile(filename='arith.c', code='int main(){return 0;}\n', line_start=2)])
    return assignment, submission


def test_teacher_risk_requires_verifiable_file_and_line_evidence():
    assignment, submission = _sample()
    assert read_status(submission, assignment)['status'] == 'pending'
    payload = dict(student_id=submission.student_id, assignment_id=assignment.id,
        source_fingerprint=paper_source_fingerprint(submission, assignment),
        provider_id='builtin:bigmodel-start-plan', model_id='GLM-5.3-Flash',
        reviewed_at='2026-09-27T12:00:00+00:00', priority='verify', ai_assistance_cue=True,
        signals=[dict(filename='arith.c',global_line=2,code_excerpt='return 0;',
                      observation='需确认该生能解释退出状态',follow_up_question='这里的 return 0 有什么作用？')])
    risk = QuestionRisk.model_validate(payload)
    store_review(risk, submission, assignment)
    assert read_status(submission, assignment)['ai_assistance_cue'] is True
    changed = submission.model_copy(update={'code': submission.code + '\n// changed'})
    assert read_status(changed, assignment)['status'] == 'outdated'


def test_unsubstantiated_ai_flag_or_wrong_line_is_rejected():
    assignment, submission = _sample()
    payload = dict(student_id=submission.student_id, assignment_id=assignment.id,
        source_fingerprint=paper_source_fingerprint(submission, assignment),
        provider_id='builtin:bigmodel-start-plan', model_id='GLM-5.3-Flash',
        reviewed_at='2026-09-27T12:00:00+00:00', priority='verify', ai_assistance_cue=True,
        signals=[])
    try:
        QuestionRisk.model_validate(payload)
    except ValueError:
        pass
    else:
        raise AssertionError('AI assistance cue without evidence must fail')
    payload['signals'] = [dict(filename='arith.c',global_line=2,code_excerpt='not in source',
        observation='unsupported',follow_up_question='why?')]
    try:
        store_review(QuestionRisk.model_validate(payload), submission, assignment)
    except ValueError:
        pass
    else:
        raise AssertionError('A made-up code excerpt must not be stored')


def test_unavailable_code_has_no_ai_claim():
    assignment, submission = _sample()
    submission.code = ''
    assert read_status(submission, assignment) == {'status': 'unavailable'}
