"""创建虚拟测试学生（TA 验证答题与上传链路用），不调用任何模型。

用法：
    .venv/bin/python -m scripts.create_pa1_test_student          # 创建（已存在则跳过）
    .venv/bin/python -m scripts.create_pa1_test_student --force  # 删除旧答卷后重建问卷

虚拟学号固定为 20260999（与真实学号 8 位段错开），问卷 generated_by=mock，
学生端会显示"演示问卷"提示。答题、提交、评价走与真实学生完全相同的接口。
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("AA_ROOT", str(ROOT / "datasets/pa1_2026fa"))

from app import cases, storage  # noqa: E402
from app.models import (  # noqa: E402
    Diagnosis,
    Question,
    Questionnaire,
    Submission,
    SourceFile,
    now_utc,
)
from app.source import paper_source_fingerprint  # noqa: E402

TEST_SID = "20260999"

TEST_CODE = '''#include <stdio.h>

int main() {
    int i = 78;
    int j;
    j = 56;
    printf("sum=%d, diff=%d, product=%d, quotient=%d\\n", i + j, i - j, i * j, i / j);
    return 0;
}
'''


def build_questions() -> list[dict]:
    variant_line = (
        '    printf("sum=%d, diff=%d, product=%d, quotient=%d\\n",'
        " i + j, i - j, i * j, j / i);"
    )
    return [
        {
            "id": "q1", "type": "text",
            "question": "你的程序里 i 在声明时初始化为 78，j 先声明（第 5 行）、第 6 行才赋值 56。"
                        "这两种写法的最终效果有什么相同点？你为什么把 j 拆成两步写？",
            "question_en": "i is initialized at declaration while j is declared first and assigned later. "
                           "What is the same in the end, and why did you split j into two steps?",
            "target_issue": "变量声明与初始化",
            "expected_answer": "两者最终都是 i=78、j=56，效果等价；拆两步的理由由学生解释。",
            "code_lines": [4, 5, 6],
        },
        {
            "id": "q2", "type": "single_choice",
            "question": "程序运行时，sum= 后面打印的数是？",
            "question_en": "What number is printed after sum=?",
            "choices": ["134", "78", "56", "1434"],
            "choices_en": ["134", "78", "56", "1434"],
            "target_issue": "四则计算",
            "expected_answer": "A",
            "code_lines": [4, 6, 7],
        },
        {
            "id": "q3", "type": "code_variant",
            "question": "假设把第 7 行整行替换为给出的变式（除法换成 j / i），quotient= 后面会打印什么？",
            "question_en": "Suppose line 7 is replaced with the variant (division changed to j / i). "
                           "What prints after quotient=?",
            "choices": ["0，56/78 整除向零截断", "1，结果不变", "1.39，%d 会显示小数", "编译报错"],
            "choices_en": ["0, truncated toward zero", "1, unchanged",
                           "1.39, %d shows decimals", "compile error"],
            "target_issue": "整数除法截断",
            "expected_answer": "A",
            "code_lines": [7],
            "variant": {"line_no": 7, "replacement": variant_line},
        },
        {
            "id": "q4", "type": "multiple_choice",
            "question": "关于第 7 行的 i / j，下列说法正确的有哪些？",
            "question_en": "About i / j on line 7, which statements are correct?",
            "choices": ["两个 int 相除，商向零截断", "结果是 1.39", "结果是 1", "会触发运行时错误"],
            "choices_en": ["int division truncates toward zero", "result is 1.39",
                           "result is 1", "it raises a runtime error"],
            "target_issue": "整除语义",
            "expected_answer": "A,C",
            "code_lines": [7],
        },
        {
            "id": "q5", "type": "single_choice",
            "question": "要编译并运行这份代码，下列哪组命令是正确的？",
            "question_en": "Which pair of commands correctly compiles and runs this code?",
            "choices": ["gcc arith.c -o arith，然后 ./arith", "gcc ./arith.c 直接运行源文件",
                        "直接运行 arith.c，不需要编译", "gcc -run arith.c"],
            "choices_en": ["gcc arith.c -o arith, then ./arith", "gcc ./arith.c runs the source",
                           "run arith.c directly, no compilation", "gcc -run arith.c"],
            "target_issue": "编译与运行",
            "expected_answer": "A",
            "code_lines": [1],
        },
    ]


def create(force: bool = False) -> None:
    assignment = storage.load_assignment()
    existing = storage.load_submission(TEST_SID)
    if existing is None:
        submission = Submission(
            student_id=TEST_SID,
            assignment_id=assignment.id,
            code=TEST_CODE,
            language="c",
            submitted_at=now_utc().isoformat(),
            source_files=[SourceFile(filename="arith.c", code=TEST_CODE, line_start=1)],
            import_notes=["虚拟测试用户（TA 创建，用于验证答题/提交/上传链路，非真实学生）"],
        )
        storage.save_json_atomic(
            storage.submissions_dir() / f"{TEST_SID}.json", submission.model_dump(mode="json")
        )
        print(f"已创建虚拟学生提交: {TEST_SID}")
    else:
        print(f"虚拟学生提交已存在: {TEST_SID}（保留）")

    # 访问码：与真实学生同机制写入 tokens.json（访问码开关关闭时仅凭学号即可进入）
    tokens = storage.load_tokens()
    if TEST_SID not in (tokens.get("students") or {}):
        tokens.setdefault("students", {})[TEST_SID] = "s-" + secrets.token_hex(6)
        storage.save_json_atomic(storage.access_dir() / "tokens.json", tokens)
        print("已在 tokens.json 登记访问码")

    questionnaire = storage.load_questionnaire(TEST_SID)
    if questionnaire is not None:
        if not force:
            print(f"问卷已存在（v{questionnaire.version}），跳过。如需重建请加 --force")
            return
        # 只清作答与当前/历史问卷快照，不碰真实学生数据
        resp_path = storage.responses_dir() / f"{TEST_SID}.json"
        if resp_path.exists():
            resp_path.unlink()
            print("已删除旧作答记录（--force）")
    fingerprint = paper_source_fingerprint(storage.load_submission(TEST_SID), assignment)
    q = Questionnaire(
        student_id=TEST_SID,
        assignment_id=assignment.id,
        diagnosis=Diagnosis(
            summary="虚拟测试用户专用问卷（TA 手工创建，用于验证答题与上传链路，非 AI 生成）。",
            issues=[],
        ),
        questions=[Question.model_validate(item) for item in build_questions()],
        generated_by="mock",
        version=storage.next_questionnaire_version(TEST_SID),
        code_fingerprint=cases.code_fingerprint(TEST_CODE),
        source_fingerprint=fingerprint,
    )
    storage.save_questionnaire(q)
    print(f"问卷已生成: v{q.version}，{len(q.questions)} 题（generated_by=mock，学生端显示演示提示）")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="删除该虚拟用户的旧作答后重建问卷")
    args = parser.parse_args()
    create(force=args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
