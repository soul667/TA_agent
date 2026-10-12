"""命令行：对所有已提交学生的**最新一次作答**运行 / 重跑评价。

评价以作答为单位记录（不新增作答）；失败会落盘为 failed 记录并可重试。
用法:
    python -m app.evaluate_all
"""
from __future__ import annotations

import sys

from . import storage
from .llm import get_provider  # noqa: F401  —— 提前确认 provider 模式
from .main import _run_evaluation


def main() -> int:
    provider = get_provider()
    print(f"LLM provider: {provider.name}")
    try:
        storage.load_assignment()
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return 1

    responses_dir = storage.responses_dir()
    if not responses_dir.exists() or not list(responses_dir.glob("*.json")):
        print("还没有任何学生提交问卷（responses/ 为空）")
        return 0

    ok = fail = skip = 0
    for path in sorted(responses_dir.glob("*.json")):
        sid = path.stem
        response = storage.load_response(sid)
        submission = storage.load_submission(sid)
        if not (submission and response and response.attempts):
            print(f"[SKIP] {sid}: 缺少提交或回答数据")
            skip += 1
            continue
        with storage.student_lock(sid):
            attempt = storage.latest_attempt(response)
            record = _run_evaluation(sid, attempt)
        if record.status == "ok" and record.result is not None:
            evaluation = record.result
            print(
                f"[OK]   {sid}: 第 {attempt.attempt_no} 次作答 → "
                f"{evaluation.understanding.value}, {evaluation.score}/{evaluation.max_score}"
            )
            ok += 1
        else:
            print(f"[FAIL] {sid}: {record.error}")
            fail += 1

    print(f"\n评价完成: 成功 {ok} / 失败 {fail} / 跳过 {skip}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
