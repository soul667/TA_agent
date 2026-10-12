import json

from app import main, storage
from app.agent import build_student_suffix, _pick_questions
from app.models import SourceFile, Submission
from conftest import TeacherClient


def test_multifile_generation_context_uses_global_line_numbers_and_no_archive_paths():
    s = Submission(student_id='12610001', assignment_id='pa1',
        code='// FILE: a.c\nint main(){}\n// FILE: b.c\nint main(){}',
        source_files=[SourceFile(filename='a.c',code='int main(){}',line_start=2,archive_member='private.zip/a.c'),
                      SourceFile(filename='b.c',code='int main(){}',line_start=4,archive_member='private.zip/b.c')])
    data=json.loads(build_student_suffix(s))
    assert data['source_files']==[
        {'filename':'a.c','line_start':2,'line_count':1},
        {'filename':'b.c','line_start':4,'line_count':1},
    ]
    assert 'private.zip' not in build_student_suffix(s)
    assert '12610001' not in build_student_suffix(s)


def test_paper_selection_keeps_explanation_question():
    questions=[{'id':f'q{i}','type':'single_choice','target_issue':str(i)} for i in range(5)]
    questions.append({'id':'explain','type':'text','target_issue':'explanation'})
    picked=_pick_questions(questions)
    assert len(picked)==5
    assert any(q['type']=='text' for q in picked)


def test_real_class_does_not_silently_use_find_max_mock_questions(await_generation_job):
    path=storage.data_dir()/'assignment/c-find-max.json'
    data=storage.load_json(path)
    data['requires_real_model']=True
    storage.save_json_atomic(path,data)
    client=TeacherClient(main.app)
    r=client.post('/api/teacher/students/20260002/paper')
    assert r.status_code==202
    job=await_generation_job(client,'20260002')
    assert job['status']=='failed'
    assert '真实模型' in job['error']
    assert storage.load_questionnaire('20260002') is None


def test_imported_empty_submission_has_specific_error_without_starting_job():
    sid='20260002'
    submission=storage.load_submission(sid)
    submission.code=''
    submission.source_archive_sha256='source-hash'
    storage.save_json_atomic(storage.submissions_dir()/f'{sid}.json',submission.model_dump())
    r=TeacherClient(main.app).post(f'/api/teacher/students/{sid}/paper')
    assert r.status_code==422
    assert '提交记录存在' in r.json()['detail']
    assert storage.load_generation_job(sid) is None
