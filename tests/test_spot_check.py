"""按学号抽查：首次生成、作答绑定、幂等、失败恢复及教师权限。"""
import json
import threading

import pytest
from fastapi.testclient import TestClient

from app import main, storage
from conftest import TeacherClient, student_token

SID = '20260002'
URL = f'/api/teacher/students/{SID}/paper'


@pytest.fixture
def client():
    return TeacherClient(main.app)


def ready(client, await_generation_job, sid=SID):
    url = f'/api/teacher/students/{sid}/paper'
    response = client.post(url)
    assert response.status_code in (200, 202), response.text
    if response.status_code == 202:
        assert await_generation_job(client, sid)['status'] == 'ok'
    response = client.get(url)
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'ready'
    return response.json()


def test_first_lookup_generates_personalized_paper_without_exam_side_effects(client, await_generation_job):
    payload = ready(client, await_generation_job)
    paper = payload['paper']
    assert paper['student_id'] == SID
    assert paper['code'] == storage.load_submission(SID).code
    assert len(paper['questions']) == 5
    assert storage.load_questionnaire(SID).case_id == 'C02'
    assert 'initialization' in {q.target_issue for q in storage.load_questionnaire(SID).questions}
    assert 'expected_answer' not in json.dumps(paper)
    assert 'diagnosis' not in paper
    assert len(payload['teacher_reference']['answers']) == 5
    assert 'student_access_code' not in payload
    assert storage.load_response(SID) is None
    assert storage.load_exam_session(SID) is None
    assert not storage.load_evaluation_file(SID).records
    # 重复抽查只打开同一份答卷，不增加版本或再次调用模型。
    again = client.post(URL)
    assert again.status_code == 200
    assert again.json()['paper']['questionnaire_id'] == paper['questionnaire_id']
    assert again.json()['paper']['version'] == 1


def test_changed_student_code_regenerates_and_preserves_old_paper(client, await_generation_job):
    first = ready(client, await_generation_job)
    submission = storage.load_submission(SID)
    submission.code = storage.load_submission('20260003').code
    storage.save_json_atomic(storage.submissions_dir() / f'{SID}.json', submission.model_dump())
    assert client.get(URL).status_code == 409
    second = ready(client, await_generation_job)
    assert second['paper']['version'] == 2
    assert second['paper']['code'] == submission.code
    assert storage.load_questionnaire(SID).case_id == 'C03'
    assert first['paper']['questions'] != second['paper']['questions']
    assert storage.load_questionnaire(SID, 1).case_id == 'C02'


def test_changed_assignment_invalidates_existing_paper(client, await_generation_job):
    ready(client, await_generation_job)
    path = storage.data_dir() / 'assignment' / 'c-find-max.json'
    data = storage.load_json(path)
    data['requirements'].append('说明时间复杂度')
    storage.save_json_atomic(path, data)
    assert client.get(URL).status_code == 409
    assert ready(client, await_generation_job)['paper']['version'] == 2


def test_repeated_clicks_share_one_pending_generation(client, await_generation_job, monkeypatch):
    started, release = threading.Event(), threading.Event()
    original = main.generate_questionnaire

    def slow(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(main, 'generate_questionnaire', slow)
    first = client.post(URL)
    try:
        assert started.wait(3)
        second = client.post(URL)
        assert first.status_code == second.status_code == 202
        assert first.json()['job_id'] == second.json()['job_id']
        assert client.get(URL).json()['status'] == 'pending'
    finally:
        release.set()
        assert await_generation_job(client, SID)['status'] == 'ok'
    assert storage.load_questionnaire(SID).version == 1


def test_generation_failure_is_visible_and_retryable(client, await_generation_job, monkeypatch):
    original = main.generate_questionnaire

    def fail(*args, **kwargs):
        raise RuntimeError('temporary provider failure')

    monkeypatch.setattr(main, 'generate_questionnaire', fail)
    assert client.post(URL).status_code == 202
    assert await_generation_job(client, SID)['status'] == 'failed'
    assert client.get(URL).json()['status'] == 'failed'
    assert storage.load_questionnaire(SID) is None
    monkeypatch.setattr(main, 'generate_questionnaire', original)
    assert ready(client, await_generation_job)['paper']['version'] == 1


def test_source_changed_during_generation_cannot_publish_stale_paper(client, await_generation_job, monkeypatch):
    original = main.generate_questionnaire

    def change_source(*args, **kwargs):
        paper = original(*args, **kwargs)
        submission = storage.load_submission(SID)
        submission.code = storage.load_submission('20260003').code
        storage.save_json_atomic(storage.submissions_dir() / f'{SID}.json', submission.model_dump())
        return paper

    monkeypatch.setattr(main, 'generate_questionnaire', change_source)
    assert client.post(URL).status_code == 202
    assert await_generation_job(client, SID)['status'] == 'failed'
    assert storage.load_questionnaire(SID) is None


def test_failed_optional_regeneration_does_not_hide_current_valid_paper(client, await_generation_job, monkeypatch):
    first = ready(client, await_generation_job)

    def fail(*args, **kwargs):
        raise RuntimeError('temporary provider failure')

    monkeypatch.setattr(main, 'generate_questionnaire', fail)
    assert client.post(f'/api/teacher/students/{SID}/regenerate').status_code == 202
    assert await_generation_job(client, SID)['status'] == 'failed'
    # 管理页仍能查看该次失败，抽查页 GET/POST 都能继续打开有效原卷。
    for method in ('get', 'post'):
        response = getattr(client, method)(URL)
        assert response.status_code == 200
        assert response.json()['paper']['questionnaire_id'] == first['paper']['questionnaire_id']


@pytest.mark.parametrize('method', ['get', 'post'])
def test_teacher_permission_required(method):
    with TestClient(main.app) as public:
        assert getattr(public, method)(URL).status_code == 401
        public.headers['Authorization'] = f'Bearer {student_token(SID)}'
        assert getattr(public, method)(URL).status_code == 403


@pytest.mark.parametrize('sid, status', [('123', 400), ('99999999', 404)])
def test_invalid_or_unknown_student_does_not_create_job(client, sid, status):
    assert client.post(f'/api/teacher/students/{sid}/paper').status_code == status
    assert storage.load_generation_job(sid) is None


@pytest.mark.parametrize('change, status', [({'code': '  '}, 422), ({'assignment_id': 'other'}, 409)])
def test_missing_or_mismatched_work_is_rejected(client, change, status):
    submission = storage.load_submission(SID).model_dump()
    submission.update(change)
    storage.save_json_atomic(storage.submissions_dir() / f'{SID}.json', submission)
    assert client.post(URL).status_code == status
    assert storage.load_generation_job(SID) is None
