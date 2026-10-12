import io
import json
import zipfile
from pathlib import Path

from app.import_blackboard import import_archive, _same_pasted_code
from app.models import Submission


def member(sid, file='', stamp='2026-09-14-10-00-00'):
    prefix = f'Assignment #1 Submission_{sid}_attempt_{stamp}'
    return prefix + (f'_{file}' if file else '.txt')


def archive(path, entries):
    with zipfile.ZipFile(path, 'w') as z:
        for name, value in entries.items():
            z.writestr(name, value)
    return path


def load(root, sid):
    return Submission.model_validate_json((root/'data/submissions'/f'{sid}.json').read_text())


def test_real_source_files_nested_zip_and_missing_submission(tmp_path):
    nested = io.BytesIO()
    archive(nested, {'src/part3.c': 'int main(){return 0;}\n', 'part3.exe': b'not executable'})
    src = archive(tmp_path/'input.zip', {
        member('12610001'): 'Submission Field:\nThere is no student submission text data for this assignment.\nComments:\n',
        member('12610001','part2.c'): '#include <stdio.h>\r\nint main(){puts("Hello");}\r\n',
        member('12610001','project.zip'): nested.getvalue(),
        member('12610002'): 'Submission Field:\n<br><p> </p>\nComments:\nNo files.',
    })
    root = tmp_path/'dataset'
    result = import_archive(src, root, 'cs111-pa1')
    assert (result['student_count'],result['ready_count'],result['missing_source_count']) == (2,1,1)
    submission = load(root, '12610001')
    assert len(submission.source_files) == 2
    assert 'not executable' not in submission.code
    assert submission.submitted_at == '2026-09-14T10:00:00+08:00'
    for source in submission.source_files:
        assert submission.code.splitlines()[source.line_start-1] == source.code.splitlines()[0]
    assert load(root,'12610002').code == ''
    assert (root/result['original_archive']).read_bytes() == src.read_bytes()
    assert len(result['students'][0]['ignored_files']) == 1
    again = import_archive(src, root, 'cs111-pa1')
    assert again['archive_sha256'] == result['archive_sha256']
    assert len(list((root/'data/submissions').glob('*.json'))) == 2


def test_latest_attempt_and_inline_submission_are_bound_to_correct_student(tmp_path):
    src = archive(tmp_path/'input.zip', {
        member('12610001','old.c',stamp='2026-09-12-09-00-00'): 'int main(){return 8;}',
        member('12610001'): 'Submission Field:\n<pre>#include &lt;stdio.h&gt;\nint main(){return 0;}</pre>\nComments:\n',
        member('12610002','other.c'): 'int main(){return 2;}',
    })
    root=tmp_path/'dataset'
    result=import_archive(src,root,'cs111-pa1')
    first=load(root,'12610001')
    assert first.code.startswith('#include <stdio.h>')
    assert 'return 8' not in first.code
    assert 'return 2' not in first.code
    assert result['students'][0]['older_attempts'] == ['2026-09-12-09-00-00']
    assert 'return 2' in load(root,'12610002').code


def test_non_utf8_and_unsafe_nested_paths_do_not_extract_files(tmp_path):
    nested=io.BytesIO()
    archive(nested, {'../escape.c':'int main(){return 99;}', 'good.c':'// 中文\nint main(){return 0;}'.encode('gb18030')})
    src=archive(tmp_path/'input.zip', {member('12610001','project.zip'):nested.getvalue()})
    root=tmp_path/'dataset'
    result=import_archive(src,root,'cs111-pa1')
    submission=load(root,'12610001')
    assert len(submission.source_files) == 1
    assert '中文' in submission.code
    assert submission.source_files[0].encoding == 'gb18030'
    assert not (tmp_path/'escape.c').exists()
    assert any('不安全' in note for note in result['students'][0]['notes'])


def test_duplicate_inline_code_is_preserved_without_duplicating_printed_sources(tmp_path):
    code = '#include <stdio.h>\nint main(){puts("Hello world");}'
    pasted = '\n\n    #include <stdio.h>\n\n  int main(){puts("Hello world");}\n'
    src = archive(tmp_path/'input.zip', {
        member('12610001','hello.c'):code,
        member('12610001'):'Submission Field:\n' + pasted + '\nComments:\n',
    })
    root=tmp_path/'dataset'
    result=import_archive(src,root,'cs111-pa1')
    s=load(root,'12610001')
    assert result['source_file_count']==1
    assert s.code==code
    assert 'Hello world' in s.submission_text
    assert not _same_pasted_code(code, code.replace('Hello world','Hello  world'))
