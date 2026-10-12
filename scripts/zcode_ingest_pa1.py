"""Import one ZCode-produced PA1 response only when its session used Start Plan Flash."""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path


def current_zcode_route(session_id: str, response_file: Path, workspace_root: Path,
                        database: Path | None = None) -> dict:
    database = database or Path.home() / '.zcode/cli/db/db.sqlite'
    with sqlite3.connect(f'file:{database}?mode=ro', uri=True) as con:
        if not session_id:
            seen = con.execute('''select distinct usage.session_id from model_usage as usage
                join session on session.id=usage.session_id
                where session.directory=? and usage.query_source='main_turn'
                and usage.completed_at between ? and ?''',
                (str(workspace_root), round(response_file.stat().st_mtime*1000)-300000,
                 round(response_file.stat().st_mtime*1000)+5000)).fetchall()
            if len(seen) != 1:
                raise ValueError("无法唯一确定生成本文件的 ZCode 会话；请传入 --session-id")
            session_id = seen[0][0]
        if not re.fullmatch(r"sess_[0-9a-f-]{36}", session_id):
            raise ValueError("ZCode sessionId 格式不正确")
        row = con.execute('''select provider_id,model_id,status,completed_at from model_usage
            where session_id=? and query_source='main_turn'
            order by completed_at desc limit 1''', (session_id,)).fetchone()
    if not row:
        raise ValueError("当前会话没有已完成的主模型调用，不能确认额度来源")
    provider, model, status, completed_ms = row
    # 用户 2026-09-28 确认：接受本会话实际的 BigModel 套餐 GLM-5.3-Flash，来源按数据库实况记录。
    if status != 'completed' or model != 'GLM-5.3-Flash' or not provider.startswith('account:bigmodel'):
        raise ValueError(f"最近一轮模型不是 BigModel GLM-5.3-Flash（实际 {provider}/{model}，{status}），拒绝入库")
    age = response_file.stat().st_mtime - completed_ms / 1000
    if not -5 <= age <= 300:
        raise ValueError("结果文件与已核实的模型调用时间不匹配，请重新生成这一份")
    return {'provider_id': provider, 'model_id': model,
            'zcode_session_id': session_id, 'model_completed_at_ms': completed_ms}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--session-id', default=os.environ.get('ZCODE_SESSION_ID', ''))
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--student-id')
    group.add_argument('--risk-batch', nargs='+')
    group.add_argument('--risk-input', type=Path)
    parser.add_argument('response_file', type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if root.name != 'pa1_2026fa' or not (root/'data/assignment/cs111-2026fa-pa1.json').is_file():
        raise ValueError('目标必须是准备好的 CS111 2026 秋 PA1 数据根目录')
    args.response_file = args.response_file.resolve()
    if not args.response_file.is_relative_to(root/'bulk'):
        raise ValueError('模型输出必须保存在 PA1 bulk 目录内')
    verified = current_zcode_route(args.session_id, args.response_file, Path(__file__).resolve().parent.parent)
    from app import storage
    from app.zcode_bulk import ingest_question_result, ingest_risk_only_batch
    os.environ['AA_ROOT'] = str(root)
    envelope = {'verified_runtime': verified, 'response': args.response_file.read_text(encoding='utf-8')}
    if args.student_id:
        result = ingest_question_result(args.student_id, envelope)
    else:
        if args.risk_input:
            risk_input = args.risk_input.resolve()
            if not risk_input.is_relative_to(root/'bulk/risk-only-inputs'):
                raise ValueError('风险输入必须来自 PA1 bulk/risk-only-inputs')
            student_ids = json.loads(risk_input.read_text(encoding='utf-8'))['student_ids']
        else:
            student_ids = args.risk_batch
        result = ingest_risk_only_batch(student_ids, envelope)
    audit = root/'bulk/ingestion-audit.jsonl'
    audit.parent.mkdir(parents=True, exist_ok=True)
    if not audit.exists():
        audit.touch(mode=0o600)
    with audit.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'timestamp': time.time(), 'result': result,
                                 'session_id': verified['zcode_session_id'],
                                 'provider_id': verified['provider_id'],
                                 'model_id': verified['model_id']}, ensure_ascii=False)+'\n')
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print(f'PA1 ZCode 入库失败: {exc}', file=sys.stderr)
        sys.exit(1)
