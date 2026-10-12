"""Prompt 注册表：prompts/ 目录为唯一事实来源，内容哈希即版本（LangChain 优化·第二批）。

- PROMPT_VERSION = "pv-" + sha256(prompt)[:12]：生成/评价记录与 LLM 台账都盖此章，
  使生产历史上「prompt 变了」与「模型变了」可以区分（provenance 归因）；
- prompts 是代码资产随仓库分发，不受 AA_ROOT 影响；
- 修改 prompt 后版本号自动变化，历史记录不受影响。
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
PROMPT_NAMES = ("questionnaire", "evaluation")


@lru_cache(maxsize=None)
def get_prompt(name: str) -> str:
    if name not in PROMPT_NAMES:
        raise KeyError(f"未知 prompt: {name}（可选：{PROMPT_NAMES}）")
    return (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")


def version(name: str) -> str:
    return "pv-" + hashlib.sha256(get_prompt(name).encode("utf-8")).hexdigest()[:12]
