"""Prepare risk-only prompts for already-generated papers without current risk notes."""
import json
import os
from pathlib import Path

from app import storage
from app.question_risk import read_status
from app.source import paper_source_fingerprint

root = Path(__file__).resolve().parent.parent / 'datasets/pa1_2026fa'
os.environ['AA_ROOT'] = str(root)
assignment = storage.load_assignment()
pending = []
for student in sorted(storage.list_submissions(), key=lambda item: item.student_id):
    if not student.code.strip() or read_status(student, assignment)['status'] == 'ready':
        continue
    questionnaire = storage.load_questionnaire(student.student_id)
    if not questionnaire or questionnaire.source_fingerprint != paper_source_fingerprint(student, assignment):
        continue
    pending.append({'student_id': student.student_id, 'source_fingerprint': questionnaire.source_fingerprint,
        'source_files': [{'filename': f.filename, 'line_start': f.line_start, 'code': f.code}
                         for f in student.source_files]})
base_prompt = (Path(__file__).resolve().parent.parent / 'prompts/zcode_batch_risk.txt').read_text(encoding='utf-8')
for index in range(0, len(pending), 8):
    group = pending[index:index + 8]
    prompt = base_prompt + '\n\n作业：' + assignment.prompt + '\n学生提交：\n' + json.dumps(group, ensure_ascii=False)
    storage.save_json_atomic(root / 'bulk/risk-only-inputs' / f'batch-{index // 8 + 1:02d}.json', {
        'student_ids': [row['student_id'] for row in group], 'prompt': prompt,
        'required_provider': 'ZCode Start Plan / GLM-5.3-Flash',
    })
print(f'Prepared {len(pending)} risk reviews in {(len(pending) + 7) // 8} ZCode batches.')
