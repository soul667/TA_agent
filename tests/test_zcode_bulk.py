import json

import pytest

from app import storage
from app.agent import build_messages, generate_questionnaire
from app.llm import MockLLMProvider
from app.models import Submission
from app.question_risk import read_status
from app.source import paper_source_fingerprint
from app.zcode_bulk import ingest_question_result, ingest_risk_only_batch


@pytest.fixture
def input_data():
    assignment=storage.load_assignment()
    submission=storage.load_submission('20260002')
    raw=MockLLMProvider().generate(build_messages(assignment,submission), {'kind':'questionnaire'})
    content=json.loads(raw)
    content['questioning_review']={'priority':'normal','ai_assistance_cue':False,'signals':[],
        'uncertainty':'源码不能证明是否使用 AI，需当面解释。'}
    return assignment,submission,content


def envelope(body, provider='account:bigmodel-start-plan',model='GLM-5.3-Flash'):
    return {'verified_runtime':{'provider_id':provider,'model_id':model},'response':json.dumps(body,ensure_ascii=False)}


def test_question_and_teacher_risk_are_saved_together_without_leaking_to_student(input_data):
    assignment, submission, content=input_data
    result=ingest_question_result(submission.student_id,envelope(content))
    q=storage.load_questionnaire(submission.student_id)
    assert result['question_count']==5 and q.version==1
    assert q.generated_by=='zcode-start-plan:GLM-5.3-Flash'
    assert q.source_fingerprint==paper_source_fingerprint(submission,assignment)
    assert read_status(submission,assignment)['status']=='ready'
    assert 'questioning_review' not in q.public()
    assert storage.load_response(submission.student_id) is None


def test_wrong_plan_and_fabricated_code_evidence_never_publish(input_data):
    assignment, submission, content=input_data
    with pytest.raises(ValueError,match='Start Plan'):
        ingest_question_result(submission.student_id,envelope(content,provider='cpa',model='gpt-5.6-luna'))
    content['questioning_review']={
        'priority':'verify','ai_assistance_cue':True,
        'signals':[{'filename':'arith.c','global_line':2,'code_excerpt':'not in source',
                    'observation':'无法核对','follow_up_question':'解释这行'}],
    }
    with pytest.raises(ValueError):
        ingest_question_result(submission.student_id,envelope(content))
    assert storage.load_questionnaire(submission.student_id) is None
    assert read_status(submission,assignment)['status']=='pending'


def test_existing_paper_is_preserved_and_risk_only_batch_checks_each_student(input_data):
    assignment,submission,content=input_data
    other=storage.load_submission('20260003')
    provider=MockLLMProvider()
    for student in (submission,other):
        q=generate_questionnaire(provider,assignment,student)
        q.source_fingerprint=paper_source_fingerprint(student,assignment)
        storage.save_questionnaire(q)
    before=storage.load_questionnaire(submission.student_id).questionnaire_id
    items=[{'student_id':s.student_id,'questioning_review':content['questioning_review']}
           for s in (submission,other)]
    result=ingest_risk_only_batch([submission.student_id,other.student_id],envelope({'items':items}))
    assert len(result)==2
    assert storage.load_questionnaire(submission.student_id).questionnaire_id==before
    assert read_status(submission,assignment)['status']=='ready'
    assert read_status(other,assignment)['status']=='ready'
    items[1]['student_id']='99999999'
    with pytest.raises(ValueError,match='遗漏或串用'):
        ingest_risk_only_batch([submission.student_id,other.student_id],envelope({'items':items}))
