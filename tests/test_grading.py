import csv
import io
import json
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app import storage
from app.grading import _grade_path
from app.import_blackboard import import_archive
from app.main import app
from conftest import TeacherClient, student_token

SID = '20260002'
URL = f'/api/ta/students/{SID}'


@pytest.fixture
def client():
    return TeacherClient(app)


def payload(client, score=88, **kwargs):
    detail = client.get(URL).json()
    return {'score': score, 'comment': '理解整数除法', 'grader_name': 'TA One',
            'revision': detail['revision'], 'submission_fingerprint': detail['submission_fingerprint'],
            'save_token': uuid.uuid4().hex, **kwargs}


def test_roster_and_source_available_before_any_paper_generation(client):
    data = client.get('/api/ta/students').json()
    assert data['max_score'] == 100 and len(data['rows']) == 6
    assert all(row['grading_status'] == 'ungraded' for row in data['rows'])
    assert all(row['can_generate'] for row in data['rows'])
    assert all(row['paper_status'] == 'not_generated' for row in data['rows'])
    detail = client.get(URL).json()
    assert detail['code'] == storage.load_submission(SID).code
    assert detail['current_grade'] is None
    assert detail['revision'] == 0
    assert storage.load_questionnaire(SID) is None


def test_grade_zero_revision_history_and_no_exam_side_effects(client):
    source_path = storage.submissions_dir()/f'{SID}.json'
    original = source_path.read_bytes()
    first = client.post(URL+'/grade', json=payload(client, 0)).json()
    assert first['current_grade']['score'] == 0
    assert first['grading_status'] == 'graded'
    assert first['revision'] == 1
    assert client.get(URL).json()['current_grade']['score'] == 0
    second = client.post(URL+'/grade', json=payload(client, 95.5, grader_name='TA Two')).json()
    assert second['revision'] == 2
    assert [r['score'] for r in second['grade_history']] == [95.5, 0]
    assert second['grade_history'][1]['grader_name'] == 'TA One'
    assert source_path.read_bytes() == original
    assert storage.load_response(SID) is None
    assert storage.load_exam_session(SID) is None
    assert not storage.load_evaluation_file(SID).records
    raw = storage.load_json(_grade_path(SID))
    assert raw['records'][0]['submission_snapshot']['code'] == storage.load_submission(SID).code
    assert 'submission_snapshot' not in json.dumps(second)


@pytest.mark.parametrize('score', [-0.1, 100.1, 'NaN', 'Infinity', True, None, '80'])
def test_invalid_scores_are_rejected_without_writing(client, score):
    assert client.post(URL+'/grade', json=payload(client, score)).status_code == 422
    assert not _grade_path(SID).exists()


def test_name_required_and_maximum_score_allowed(client):
    assert client.post(URL+'/grade', json=payload(client, 100, grader_name='  ')).status_code == 422
    response = client.post(URL+'/grade', json=payload(client, 100, grader_name=' TA One '))
    assert response.status_code == 200
    assert response.json()['current_grade']['grader_name'] == 'TA One'


def test_idempotent_retry_and_token_mismatch(client):
    body = payload(client)
    first = client.post(URL+'/grade', json=body).json()
    again = client.post(URL+'/grade', json=body).json()
    assert again['already_saved'] is True
    assert again['revision'] == 1
    assert again['current_grade']['record_id'] == first['current_grade']['record_id']
    assert client.post(URL+'/grade', json={**body, 'score': 1}).status_code == 409
    assert client.get(URL).json()['current_grade']['score'] == 88


def test_two_tas_cannot_silently_overwrite_each_other(client):
    first, second = payload(client, 81), payload(client, 92, grader_name='TA Two')
    def submit(body):
        return TeacherClient(app).post(URL+'/grade', json=body).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(submit, [first, second]))
    assert sorted(statuses) == [200, 409]
    assert client.get(URL).json()['revision'] == 1


def test_changed_submission_requires_reload_and_flags_previous_grade(client):
    assert client.post(URL+'/grade', json=payload(client)).status_code == 200
    stale = payload(client, 95)
    submission = storage.load_submission(SID)
    submission.code += '\n/* updated submission */'
    storage.save_json_atomic(storage.submissions_dir()/f'{SID}.json', submission.model_dump())
    response = client.post(URL+'/grade', json=stale)
    assert response.status_code == 409 and '提交已经更新' in response.json()['detail']
    assert client.get(URL).json()['grading_status'] == 'needs_review'
    row = next(r for r in client.get('/api/ta/students').json()['rows'] if r['student_id'] == SID)
    assert row['grading_status'] == 'needs_review' and row['grade']['score'] == 88
    new = client.post(URL+'/grade', json=payload(client, 95)).json()
    assert new['grading_status'] == 'graded' and new['revision'] == 2


def test_missing_code_stays_visible_and_ta_can_assign_score(client):
    submission = storage.load_submission(SID)
    submission.code = ''
    storage.save_json_atomic(storage.submissions_dir()/f'{SID}.json', submission.model_dump())
    detail = client.get(URL).json()
    assert detail['can_generate'] is False and detail['paper_status'] == 'missing_source'
    assert client.post(URL+'/grade', json=payload(client, 0, comment='未附代码')).status_code == 200
    assert client.get(URL).json()['current_grade']['score'] == 0


@pytest.mark.parametrize('path', ['/api/ta/students', URL, URL+'/download', '/api/ta/grades.csv'])
def test_ta_reads_require_teacher_permission(path):
    client = TestClient(app)
    assert client.get(path).status_code == 401
    client.headers['Authorization'] = f'Bearer {student_token(SID)}'
    assert client.get(path).status_code == 403


def test_grade_write_rejects_student_and_unknown_sid(client):
    body = payload(client)
    public = TestClient(app)
    assert public.post(URL+'/grade', json=body).status_code == 401
    public.headers['Authorization'] = f'Bearer {student_token(SID)}'
    assert public.post(URL+'/grade', json=body).status_code == 403
    assert client.post('/api/ta/students/99999999/grade', json=body).status_code == 404


def test_csv_includes_ungraded_rows_zero_and_escapes_formulas(client):
    client.post(URL+'/grade', json=payload(client, 0, grader_name='=TA()', comment='=HYPERLINK("x")'))
    response = client.get('/api/ta/grades.csv')
    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
    assert len(rows) == 6
    row = next(row for row in rows if row['学号'] == SID)
    assert float(row['得分']) == 0
    assert row['评分人'] == "'=TA()"
    assert row['评语'].startswith("'=HYPERLINK")
    assert all(row['得分'] == '' for row in rows if row['学号'] != SID)


def test_original_download_contains_only_selected_students_latest_attempt(client, tmp_path):
    archive = tmp_path/'original.zip'
    prefix = f'Assignment #1 Submission_{SID}_attempt_2026-09-14-10-00-00'
    raw = b'#include <stdio.h>\r\nint main(){return 0;}'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr(prefix+'_arith.c', raw)
        z.writestr(prefix+'_result.png', b'image bytes')
        z.writestr(prefix+'.txt', 'Submission Field:\n\nComments:\n')
        z.writestr(f'Assignment #1 Submission_{SID}_attempt_2026-09-13-10-00-00_old.c', 'old attempt')
        z.writestr('Assignment #1 Submission_20260003_attempt_2026-09-14-10-00-00_other.c', 'private other work')
    import_archive(archive, storage.root(), 'c-find-max')
    detail = client.get(URL).json()
    assert detail['download_available']
    assert {a['name'] for a in detail['attachments']} == {'arith.c', 'result.png', 'Blackboard 提交记录.txt'}
    response = client.get(URL+'/download')
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        assert set(z.namelist()) == {'arith.c', 'result.png', 'Blackboard 提交记录.txt'}
        assert z.read('arith.c') == raw
        assert b'private other work' not in b''.join(z.read(n) for n in z.namelist())


def test_corrupt_grade_file_cannot_be_overwritten(client):
    body = payload(client)
    path = _grade_path(SID)
    path.parent.mkdir(exist_ok=True)
    path.write_text('broken JSON')
    assert client.post(URL+'/grade', json=body).status_code == 500
    assert path.read_text() == 'broken JSON'


def test_ta_spa_routes_resolve(client):
    assert client.get('/ta').status_code == 200
    assert client.get('/ta/'+SID).status_code == 200


def test_source_inspection_is_visible_separately_from_manual_grade(client):
    submission = storage.load_submission(SID)
    path = storage.root()/'inspections'/f'{SID}.json'
    storage.save_json_atomic(path, {
        'student_id': SID, 'student_name': '测试学生', 'assignment_id': submission.assignment_id,
        'submission_fingerprint': client.get(URL).json()['submission_fingerprint'],
        'reviewer': '源码初审', 'reviewed_at': '2026-09-27T00:00:00+00:00',
        'summary': '未显式写 return', 'method_note': '源码检查',
        'files': [{'filename': 'arith.c', 'explicit_return': False, 'comments': True,
                   'c_syntax_ok': True, 'notes': ['当前文件未显式写 return']}],
    })
    detail = client.get(URL).json()
    assert detail['inspection']['is_current'] is True
    assert detail['inspection']['files'][0]['notes'] == ['当前文件未显式写 return']
    assert detail['current_grade'] is None and detail['grading_status'] == 'ungraded'
    row = next(row for row in client.get('/api/ta/students').json()['rows'] if row['student_id'] == SID)
    assert row['inspection_status'] == 'reviewed' and row['student_name'] == '测试学生'
    submission.code += '\n// updated'
    storage.save_json_atomic(storage.submissions_dir()/f'{SID}.json', submission.model_dump())
    assert client.get(URL).json()['inspection']['is_current'] is False
    row = next(row for row in client.get('/api/ta/students').json()['rows'] if row['student_id'] == SID)
    assert row['inspection_status'] == 'outdated'
