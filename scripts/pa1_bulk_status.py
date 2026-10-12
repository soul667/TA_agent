"""Summarize pre-generation and teacher-only questioning risk without model calls."""
import json
import os
from pathlib import Path
from collections import Counter

from app import storage
from app.question_risk import read_status
from app.source import paper_source_fingerprint

root=Path(__file__).resolve().parent.parent/'datasets/pa1_2026fa'
os.environ['AA_ROOT']=str(root)
a=storage.load_assignment()
questions=Counter();risks=Counter()
for student in storage.list_submissions():
    if not student.code.strip():
        questions['missing_source']+=1
    else:
        q=storage.load_questionnaire(student.student_id)
        if q and q.source_fingerprint==paper_source_fingerprint(student,a):
            questions['ready']+=1
            questions['zcode_session' if q.generated_by.startswith('zcode') else 'earlier_provider']+=1
        else:questions['pending']+=1
    risks[read_status(student,a)['status']]+=1
print(json.dumps({'questionnaires':dict(questions),'question_risks':dict(risks)},ensure_ascii=False,indent=2))
