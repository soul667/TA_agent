"""命令行：为所有学生生成诊断问卷（并发批跑 + 内容寻址复用）。

用法:
    python -m app.generate_all                     # 跳过已生成
    python -m app.generate_all --force             # 全部重新生成
    python -m app.generate_all --force --no-reuse  # 忽略复用缓存，全部真实调用
    AA_GEN_CONCURRENCY=4（默认）控制并发上限。

失败学生不会被静默略过：末尾输出明确的失败清单（合规报告），退出码非 0。
"""
from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor

from . import reuse, storage
from .agent import generate_questionnaire
from .llm import LLMError, get_provider


def _worker_count() -> int:
    try:
        return max(1, int(os.getenv("AA_GEN_CONCURRENCY", "4")))
    except ValueError:
        return 4


def main(force: bool = False, workers: int | None = None, allow_reuse: bool = True) -> int:
    provider = get_provider()
    print(f"LLM provider: {provider.name}")
    try:
        assignment = storage.load_assignment()
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return 1
    submissions = storage.list_submissions()
    if not submissions:
        print("[ERROR] data/submissions/ 下没有任何提交文件")
        return 1

    def process(submission) -> tuple[str, str, str]:
        sid = submission.student_id
        target = storage.generated_dir() / f"{sid}.json"
        if target.exists() and not force:
            return sid, "skip", "已存在（--force 可重新生成）"
        try:
            questionnaire, reused = reuse.get_or_generate(
                assignment, submission,
                lambda: generate_questionnaire(provider, assignment, submission),
                allow_reuse=allow_reuse,
            )
            with storage.student_lock(sid):
                storage.save_questionnaire(questionnaire)
            note = f"复用自 {questionnaire.reused_from}" if reused else ""
            return sid, "ok", (
                f"{len(questionnaire.questions)} 题, "
                f"issues={len(questionnaire.diagnosis.issues)} {note}"
            ).strip()
        except LLMError as e:
            return sid, "fail", str(e)[:200]

    ok = fail = skip = 0
    failed_ids: list[str] = []
    with ThreadPoolExecutor(max_workers=workers or _worker_count()) as ex:
        for sid, status, note in ex.map(process, submissions):
            if status == "ok":
                ok += 1
                print(f"[OK]   {sid}: {note}")
            elif status == "skip":
                skip += 1
                print(f"[SKIP] {sid}: {note}")
            else:
                fail += 1
                failed_ids.append(sid)
                print(f"[FAIL] {sid}: {note}")

    print(f"\n生成完成: 成功 {ok} / 失败 {fail} / 跳过 {skip}（共 {len(submissions)} 名学生）")
    if failed_ids:
        print(f"失败清单（未生成有效问卷，需人工重试）: {', '.join(failed_ids)}")
    return 1 if fail else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="为所有学生生成个性化诊断问卷")
    parser.add_argument("--force", action="store_true", help="覆盖已生成的问卷")
    parser.add_argument("--workers", type=int, default=None, help="并发上限（默认 AA_GEN_CONCURRENCY=4）")
    parser.add_argument("--no-reuse", action="store_true", help="忽略复用缓存，全部真实调用")
    args = parser.parse_args()
    sys.exit(main(force=args.force, workers=args.workers, allow_reuse=not args.no_reuse))
