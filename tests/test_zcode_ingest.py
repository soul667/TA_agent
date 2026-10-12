import sqlite3
import time
from pathlib import Path

import pytest

from scripts.zcode_ingest_pa1 import current_zcode_route


def test_inferred_zcode_session_must_match_workspace_and_start_plan(tmp_path):
    database=tmp_path/'zcode.sqlite'
    current_workspace=tmp_path/'workspace'
    current_workspace.mkdir()
    output=tmp_path/'answer.json'
    output.write_text('{}')
    session_id='sess_'+'1'*8+'-'+'1'*4+'-'+'1'*4+'-'+'1'*4+'-'+'1'*12
    with sqlite3.connect(database) as con:
        con.execute('create table session (id text, directory text)')
        con.execute('create table model_usage (session_id text, query_source text, provider_id text, model_id text, status text, completed_at integer)')
        con.execute('insert into session values (?,?)',(session_id,str(current_workspace)))
        con.execute('insert into model_usage values (?,?,?,?,?,?)',
            (session_id,'main_turn','account:bigmodel-start-plan','GLM-5.3-Flash','completed',round(output.stat().st_mtime*1000)))
    verified=current_zcode_route('',output,current_workspace,database)
    assert verified['zcode_session_id']==session_id
    assert verified['provider_id']=='account:bigmodel-start-plan'
    with sqlite3.connect(database) as con:
        con.execute("update model_usage set provider_id='cpa',model_id='gpt-5.6-luna'")
    with pytest.raises(ValueError,match='不是 Start Plan Flash'):
        current_zcode_route('',output,current_workspace,database)


def test_zcode_ingest_rejects_unrelated_or_stale_model_use(tmp_path):
    database=tmp_path/'zcode.sqlite';output=tmp_path/'answer.json';output.write_text('{}')
    with sqlite3.connect(database) as con:
        con.execute('create table session (id text, directory text)')
        con.execute('create table model_usage (session_id text, query_source text, provider_id text, model_id text, status text, completed_at integer)')
        con.execute('insert into session values (?,?)',('sess_'+'a'*36,str(tmp_path/'other-project')))
        con.execute('insert into model_usage values (?,?,?,?,?,?)',('sess_'+'a'*36,'main_turn','account:bigmodel-start-plan','GLM-5.3-Flash','completed',round(output.stat().st_mtime*1000)))
    with pytest.raises(ValueError,match='无法唯一确定'):
        current_zcode_route('',output,tmp_path/'target-project',database)
