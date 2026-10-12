"""LLM 调用台账：run_structured 的每次 attempt 追加一行 JSONL（LangChain 优化·第二批）。

- 保存当前会直接丢弃的原始输出（raw）与逐 attempt 实际发送的对话（convo），
  这是离线回放、解析器零成本回归与金标评测的前提；
- 追加写 + flock 串行化（与 storage 的锁风格一致）；
- 文件：{AA_ROOT}/responses/llm_journal.jsonl；环境变量 AA_JOURNAL=0 关闭。
"""
from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .storage import responses_dir

ENABLE_ENV = "AA_JOURNAL"


def enabled() -> bool:
    return os.getenv(ENABLE_ENV, "1").strip().lower() not in ("0", "false", "no")


def journal_path() -> Path:
    return responses_dir() / "llm_journal.jsonl"


def record(entry: dict) -> None:
    if not enabled():
        return
    payload = {"ts": datetime.now(timezone.utc).isoformat(), **entry}
    path = journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # 安全修复 R1：台账含完整 prompt/输出（含参考答案），O_CREAT 带显式 0600，
    # 避免共享主机上其他用户读取；已存在的宽松文件也一并收紧
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.fchmod(fd, 0o600)
        f = os.fdopen(fd, "a", encoding="utf-8")
        with f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
                f.flush()
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def read_entries(path: Path | None = None) -> list[dict]:
    """读取台账；损坏行（进程中断产生的半行）跳过。"""
    p = path or journal_path()
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
