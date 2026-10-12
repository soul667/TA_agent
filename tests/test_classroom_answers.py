"""现场作答 -> 持久化 -> TA 按对应题目查看 -> 人工评分。"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app import main, storage
from conftest import TeacherClient, student_token

SID = '20260002'
PAPER = f'/api/teacher/students/{SID}/paper'
SUBMIT = PAPER + '/answers'
TA = f'/api/ta/students/{SID}'


@pytest.fixture
def client():
    return TeacherClient(main.app)


@pytest.fixture
def paper(client, await_generation_job):
    assert client.post(PAPER).status_code == 202
    assert await_generation_job(client, SID)['status'] == 'ok'
    return client.get(PAPER).json()['paper']


def body(paper, answers=None):
    if answers is None:
        answers = {q['id']: ['A', 'B'] if q['type'] == 'multiple_choice' else
                   '这是学生自己的解释' if q['type'] == 'text' else 'A' for q in paper['questions']}
    return {'answers': answers, 'questionnaire_id': paper['questionnaire_id'],
            'questionnaire_version': paper['version'], 'submission_token': uuid.uuid4().hex}


def grade_body(detail, score=80):
    return {'score': score, 'comment': '根据原始代码和现场回答评分', 'grader_name': 'TA',
            'revision': detail['revision'], 'submission_fingerprint': detail['submission_fingerprint'],
            'answer_attempt_id': detail['latest_answer_attempt_id'], 'save_token': uuid.uuid4().hex}


def test_save_answers_restores_same_paper_and_ta_sees_all_question_types(client, paper, monkeypatch):
    monkeypatch.setattr(main, '_spawn_evaluation', lambda *a: pytest.fail('现场作答不应启动 AI 评价'))
    request = body(paper)
    response = client.post(SUBMIT, json=request)
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt['evaluation_status'] == 'awaiting_review'
    assert receipt['answers'] == request['answers']
    assert 'expected_answer' not in receipt
    saved = storage.load_response(SID)
    assert saved.attempt_count == 1
    assert saved.attempts[0].submission_source == 'classroom'
    assert saved.attempts[0].source_code_snapshot == storage.load_submission(SID).code
    assert not storage.load_evaluation_file(SID).records
    assert storage.load_exam_session(SID) is None
    assert not (storage.root()/'grades'/f'{SID}.json').exists()
    assert client.get(PAPER).json()['latest_answer']['attempt_id'] == receipt['attempt_id']
    detail = client.get(TA).json()
    assert detail['latest_answer_attempt_id'] == receipt['attempt_id']
    attempt = detail['answer_attempts'][0]
    assert attempt['is_current_paper'] and len(attempt['questions']) == 5
    for q in attempt['questions']:
        assert q['answer'] == request['answers'][q['id']]
        assert q['expected_answer']
    status = client.get(f'/api/exam/{SID}/result').json()
    assert status['state'] == 'awaiting_review'
    assert '人工评分' in status['next_steps'][0]


def test_network_retry_is_idempotent_and_modified_retry_rejected(client, paper):
    request = body(paper)
    first = client.post(SUBMIT, json=request).json()
    again = client.post(SUBMIT, json=request).json()
    assert first['attempt_id'] == again['attempt_id']
    assert storage.load_response(SID).attempt_count == 1
    single = next(q for q in paper['questions'] if q['type'] == 'single_choice')
    request['answers'][single['id']] = 'B'
    assert client.post(SUBMIT, json=request).status_code == 409
    assert storage.load_response(SID).attempt_count == 1


@pytest.mark.parametrize('kind', ['unknown_question', 'invalid_choice', 'wrong_multi_type', 'wrong_text_type', 'too_long', 'empty'])
def test_bad_or_empty_answers_do_not_write(client, paper, kind):
    request = body(paper)
    single = next(q['id'] for q in paper['questions'] if q['type'] == 'single_choice')
    multi = next(q['id'] for q in paper['questions'] if q['type'] == 'multiple_choice')
    text = next(q['id'] for q in paper['questions'] if q['type'] == 'text')
    if kind == 'unknown_question': request['answers']['not-a-question'] = 'A'
    elif kind == 'invalid_choice': request['answers'][single] = 'Z'
    elif kind == 'wrong_multi_type': request['answers'][multi] = 'A,B'
    elif kind == 'wrong_text_type': request['answers'][text] = None
    elif kind == 'too_long': request['answers'][text] = 'x' * 8001
    elif kind == 'empty': request['answers'] = {}
    assert client.post(SUBMIT, json=request).status_code == 422
    assert storage.load_response(SID) is None


def test_changed_paper_rejects_old_answers_and_preserves_history(client, paper, await_generation_job):
    request = body(paper)
    receipt = client.post(SUBMIT, json=request).json()
    assert client.post(f'/api/teacher/students/{SID}/regenerate').status_code == 202
    assert await_generation_job(client, SID)['status'] == 'ok'
    assert client.post(SUBMIT, json={**request, 'submission_token': uuid.uuid4().hex}).status_code == 409
    assert client.get(PAPER).json()['latest_answer'] is None
    history = client.get(TA).json()['answer_attempts'][0]
    assert history['questionnaire_id'] == paper['questionnaire_id']
    assert history['attempt_id'] == receipt['attempt_id']
    assert not history['is_current_paper']
    assert history['questions'][0]['question'] == paper['questions'][0]['question']


def test_changed_source_rejects_answers_before_regeneration(client, paper):
    submission = storage.load_submission(SID)
    submission.code += '\n/* new source */'
    storage.save_json_atomic(storage.submissions_dir()/f'{SID}.json', submission.model_dump())
    assert client.post(SUBMIT, json=body(paper)).status_code == 409
    assert storage.load_response(SID) is None


def test_new_answers_mark_score_stale_and_require_ta_to_reload(client, paper):
    client.post(SUBMIT, json=body(paper))
    detail = client.get(TA).json()
    first = client.post(TA+'/grade', json=grade_body(detail)).json()
    assert first['grading_status'] == 'graded'
    stale_grade = grade_body(first, 90)
    second = client.post(SUBMIT, json=body(paper)).json()
    assert client.get(TA).json()['grading_status'] == 'needs_review'
    assert client.post(TA+'/grade', json=stale_grade).status_code == 409
    latest = client.get(TA).json()
    assert len(latest['answer_attempts']) == 2
    scored = client.post(TA+'/grade', json=grade_body(latest, 90)).json()
    assert scored['grading_status'] == 'graded'
    assert scored['current_grade']['answer_attempt_id'] == second['attempt_id']
    assert scored['grade_history'][1]['answer_attempt_id'] == detail['latest_answer_attempt_id']


def test_only_teacher_can_submit_supervised_classroom_answers(paper):
    public = TestClient(main.app)
    assert public.post(SUBMIT, json=body(paper)).status_code == 401
    public.headers['Authorization'] = f'Bearer {student_token(SID)}'
    assert public.post(SUBMIT, json=body(paper)).status_code == 403
