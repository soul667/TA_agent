"""内容寻址复用：相同输入直接克隆已验证问卷，零 LLM 调用（LangChain 优化·第三批）。

问卷可视为 (assignment_id, prompt_version, 代码指纹, 输出语言) 的纯函数——
互相抄袭/重复提交的代码无需再次生成：
- 索引：generated/reuse-index.json（memo key → 来源学生），flock 串行化读写；
- 单飞（single-flight）：同 key 的并发生成合并为一次真实调用，后来者等锁后二次查索引；
- 克隆仅复用模型产出内容；student_id / questionnaire_id / version / generated_at
  由服务端重置，并盖 reused_from 来源章（学生端 public() 不含该字段，无泄露）；
- 前置条件：agent.build_messages 已把学生身份移出可克隆载荷（只在消息头）。
- 环境变量 AA_REUSE=0 全局关闭。
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import os
from typing import Callable, Iterator, Optional

from . import cases, storage
from .models import Questionnaire, new_id, now_utc

ENABLE_ENV = "AA_REUSE"


def enabled() -> bool:
    return os.getenv(ENABLE_ENV, "1").strip().lower() not in ("0", "false", "no")


def memo_key(
    assignment_id: str, prompt_version: str, code_fingerprint: str, output_lang: str
) -> str:
    raw = "|".join((assignment_id, prompt_version, code_fingerprint, output_lang))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def index_path():
    return storage.generated_dir() / "reuse-index.json"


def _load_index() -> dict:
    data = storage.load_json(index_path())
    return data if isinstance(data, dict) else {}


@contextlib.contextmanager
def _index_lock() -> Iterator[None]:
    storage.locks_dir().mkdir(parents=True, exist_ok=True)
    with open(storage.locks_dir() / "reuse-index.lock", "a+") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def singleflight(key: str) -> Iterator[None]:
    """同 key 互斥：并发请求在此排队，进锁后必须二次查索引（先来者已写入）。"""
    storage.locks_dir().mkdir(parents=True, exist_ok=True)
    with open(storage.locks_dir() / f"reuse-{key}.lock", "a+") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


def find_source(key: str) -> Optional[Questionnaire]:
    """按 key 取缓存问卷快照（与来源学生的当前文件无关，免疫后续重生成）。"""
    with _index_lock():
        entry = _load_index().get("entries", {}).get(key)
    if not isinstance(entry, dict) or not isinstance(entry.get("questionnaire"), dict):
        return None
    try:
        return Questionnaire.model_validate(entry["questionnaire"])
    except Exception:
        return None


def store(key: str, questionnaire: Questionnaire) -> None:
    entry = {
        "questionnaire": questionnaire.model_dump(mode="json"),
        "prompt_version": questionnaire.prompt_version,
        "code_fingerprint": questionnaire.code_fingerprint,
        "created_at": now_utc().isoformat(),
    }
    with _index_lock():
        idx = _load_index()
        idx.setdefault("entries", {})[key] = entry
        storage.save_json_atomic(index_path(), idx)


def clone(source: Questionnaire, submission) -> Questionnaire:
    """以来源问卷为模板生成目标学生的问卷：内容复用，身份全新。"""
    q = source.model_copy(deep=True)
    q.student_id = submission.student_id
    q.assignment_id = submission.assignment_id
    q.questionnaire_id = new_id()
    q.version = 1  # 版本由调用方按目标学生的版本规则覆盖
    q.generated_at = now_utc()
    q.reused_from = source.student_id
    q.code_fingerprint = cases.code_fingerprint(submission.code)
    return q


def get_or_generate(
    assignment,
    submission,
    generate_fn: Callable[[], Questionnaire],
    allow_reuse: bool = True,
    output_lang: Optional[str] = None,
) -> tuple[Questionnaire, bool]:
    """优先克隆缓存，未命中则在单飞锁内生成并写索引。返回 (问卷, 是否复用)。"""
    import os

    lang = (output_lang or os.getenv("AA_OUTPUT_LANG", "zh")).strip().lower() or "zh"
    key = memo_key(
        assignment.id,
        _prompt_version(),
        cases.code_fingerprint(submission.code),
        lang,
    )
    if not allow_reuse or not enabled():
        questionnaire = generate_fn()
        store(key, questionnaire)
        return questionnaire, False

    source = find_source(key)
    if source is not None:
        return clone(source, submission), True
    with singleflight(key):
        source = find_source(key)  # 等锁期间先来者可能已写入
        if source is not None:
            return clone(source, submission), True
        questionnaire = generate_fn()
        store(key, questionnaire)
        return questionnaire, False


def _prompt_version() -> str:
    from .prompts import version as prompt_version

    return prompt_version("questionnaire")
