"""一次性补丁：为 18 份 Mock 案例补充
1. 英文内容（title_en / behavior_notes_en / diagnosis.summary_en / question_en /
   choices_en / target_en / 样例 note_en；C01-C06 样例补充英文变体）；
2. 第 5 题 code_variant（代码变异题：某一行改成 XXX 后行为如何）；
3. 档案作答补 q5，并按判分引擎重算 expect（保持四档语义：mastered/partial/limited/insufficient）；
4. 展示文本的公式/代码 token 包裹（`code` 用等宽、$math$ 用 Times New Roman，前端 FormulaText 渲染）。
匹配用的样例 variants 绝不包裹（它们是判分键）。

运行：.venv/bin/python scripts/patch_cases_bilingual_variants.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = ROOT / "data" / "mock_cases"

# ---------------------------------------------------------------- token 包裹
CODE_PATTERNS = [
    r"for\s*\([^)]*\)",
    r"while\s*\([^)]*\)",
    r"\barr\s*\[[^\[\]]*\]",
    r"\bfind_max\s*\([^)]*\)",
    r"\bprintf\s*\([^)]*\)",
    r"\bINT_MIN\b",
    r"(?<![\w.\-])-(?:2147483648|9999)(?!\d)",
    r"\bint\s+max\s*=\s*arr\s*\[[^\[\]]*\]\s*;",
    r"\bmax\s*=\s*i\s*;",
    r"\breturn\s+max\s*;",
    r"\[[\d,\s\-]+\]",
]
MATH_PATTERNS = [
    r"\bO\s*\([^)]*\)",
    r"\b\w\s*(?:<=|>=|<|>|==)\s*[\w.\-]+(?:\s*[-+]\s*[\w.\-]+)*",
]
ZH_SINGLES = [r"\bmax\b", r"\btmp\b", r"\bfirst\b", r"\bi\b", r"\bn\b", r"\bm\b", r"\ba\b"]


def _wrap(s: str, pattern: str, tpl: str) -> str:
    parts = re.split(r"(`[^`]*`|\$[^$]*\$)", s)
    for idx in range(0, len(parts), 2):
        parts[idx] = re.sub(pattern, tpl, parts[idx])
    return "".join(parts)


def wrap_tokens(s: str, zh: bool) -> str:
    """给展示文本中的代码/公式 token 加渲染标记。已是标记内的内容跳过。"""
    if not s:
        return s
    for p in CODE_PATTERNS:
        s = _wrap(s, p, lambda m: "`" + m.group(0) + "`")
    for p in MATH_PATTERNS:
        s = _wrap(s, p, lambda m: "$" + m.group(0) + "$")
    if zh:
        for p in ZH_SINGLES:
            s = _wrap(s, p, lambda m: "$" + m.group(0) + "$")
    return s


# ---------------------------------------------------------------- 英文内容
TITLES_EN = {
    "C01": "Standard correct implementation (loop starts at i = 1)",
    "C02": "max initialized to the constant 0 (fails on all-negative input)",
    "C03": "Loop condition i <= n: out-of-bounds access",
    "C04": "Comparison direction reversed: actually finds the minimum",
    "C05": "Compares only the first and last elements",
    "C06": "Computation correct but the return statement is missing",
    "C07": "Correct implementation: loop starts at i = 0 (one redundant self-comparison)",
    "C08": "Correct implementation: reverse traversal",
    "C09": "Correct implementation: INT_MIN sentinel over all elements",
    "C10": "Initialized from arr[1]: out of bounds when n = 1; arr[0] never examined",
    "C11": "Arbitrary negative constant -9999 as sentinel: fails outside the range",
    "C12": "Loop starts at i = 2: arr[1] is skipped",
    "C13": "Loop stops at n - 2: the last element is never checked",
    "C14": "Assigns the index i to the max variable when updating",
    "C15": "Returns prematurely upon finding a larger element",
    "C16": "Computes correctly but returns the wrong variable",
    "C17": "Sorts then takes the last element: correct value but mutates the caller's array",
    "C18": "Composite defects: initialization to 0 plus an out-of-bounds loop",
}

BEHAVIOR_EN = {
    "C01": "Initialization, traversal range, comparison direction and return value are all correct; the questionnaire aims to confirm and deepen understanding.",
    "C02": "Fails for all-negative arrays: 0 wins over every element; the questionnaire probes initialization assumptions and edge inputs.",
    "C03": "Reads arr[n] out of bounds (undefined behavior); the questionnaire probes index ranges and loop termination.",
    "C04": "The comparison is inverted, so the function returns the minimum; the questionnaire probes the loop invariant and max/min semantics.",
    "C05": "Only the last element is compared against the first; interior elements are ignored; the questionnaire probes traversal completeness.",
    "C06": "The maximum is computed into max but never returned; the questionnaire probes the return mechanism.",
    "C07": "Correct, with one harmless self-comparison at i = 0; the questionnaire probes equivalent implementations and comparison counts.",
    "C08": "Correct: initializes from arr[n - 1] and traverses in reverse; the questionnaire probes coverage and the n = 1 case.",
    "C09": "Correct: INT_MIN sentinel plus a full traversal; the questionnaire probes sentinel preconditions and type ranges.",
    "C10": "Reads arr[1] at initialization (out of bounds when n = 1) and never examines arr[0]; the questionnaire probes candidate validity.",
    "C11": "A -9999 sentinel fails for inputs entirely below -9999; the questionnaire probes failing inputs and value ranges.",
    "C12": "The loop starts at i = 2, so arr[1] is never compared; the questionnaire probes traversal completeness.",
    "C13": "The condition i < n - 1 skips the last element; the questionnaire probes off-by-one boundaries and test design.",
    "C14": "The update assigns the index i instead of the element value; the questionnaire probes tracing and the loop invariant.",
    "C15": "Returns arr[i] as soon as a larger element appears, skipping the rest; the questionnaire probes premature returns.",
    "C16": "The result is computed into max but first is returned; the questionnaire probes internal state vs. the returned result.",
    "C17": "Bubble-sorts the caller's array and returns arr[n - 1]; the value is right but the required no-side-effect contract is broken.",
    "C18": "Two independent defects (max = 0 and i <= n); the questionnaire probes defect separation.",
}

SUMMARY_EN = {
    "C01": "The submission is fully correct: initialization, traversal range [1, n), comparison direction and the return value all meet the requirements. The questionnaire confirms understanding and goes a bit deeper instead of repeating basics.",
    "C02": "max is initialized to 0, so any all-negative input returns the wrong result. The questionnaire focuses on initialization assumptions and edge inputs.",
    "C03": "The loop condition i <= n reads arr[n], which is out of bounds and undefined in C. The questionnaire focuses on valid index ranges and loop termination.",
    "C04": "The comparison direction is inverted, so the code actually finds the minimum. The questionnaire focuses on the loop invariant and the max/min semantic transfer.",
    "C05": "Only arr[n - 1] is compared against arr[0]; interior elements are never examined, so the true maximum can be missed. The questionnaire focuses on traversal completeness.",
    "C06": "The maximum is computed correctly but the function never returns it; the caller cannot rely on the value. The questionnaire focuses on the return mechanism.",
    "C07": "Correct implementation starting at i = 0: one redundant self-comparison on the first round, otherwise equivalent. The questionnaire confirms understanding of equivalence and cost.",
    "C08": "Correct reverse traversal: max starts at arr[n - 1] and every earlier index is examined. The questionnaire confirms coverage reasoning and edge cases.",
    "C09": "Correct sentinel implementation: max starts at INT_MIN and all n elements are examined. The questionnaire probes why the sentinel is valid here and how it depends on the value range.",
    "C10": "max is initialized from arr[1], which is already out of bounds when n = 1, and arr[0] is never examined. The questionnaire probes initialization validity and coverage.",
    "C11": "The sentinel -9999 only works while every element exceeds -9999; inputs entirely below it return wrong results. The questionnaire probes failing inputs and value ranges.",
    "C12": "The loop starts at i = 2, so arr[1] never participates in any comparison. The questionnaire focuses on traversal completeness and coverage.",
    "C13": "The condition i < n - 1 skips the last element, so inputs whose maximum sits at the end fail. The questionnaire focuses on off-by-one boundaries and test design.",
    "C14": "On update the code assigns the index i rather than the element arr[i], so the returned number is a position, not a value. The questionnaire focuses on tracing and the loop invariant.",
    "C15": "The function returns immediately at the first element greater than max, so later (possibly larger) elements are never examined. The questionnaire focuses on premature returns.",
    "C16": "The maximum is correctly computed into max, but the function returns first (the initial element). The questionnaire focuses on internal state versus the actual result.",
    "C17": "The function bubble-sorts the caller's array and returns the last element. The value is correct, but the assignment forbids modifying the original array. The questionnaire focuses on the API contract.",
    "C18": "Two independent defects: max = 0 fails for all-negative inputs and i <= n reads out of bounds. The questionnaire probes when each defect manifests and why fixing one does not fix the other.",
}

# 每题英文：question_en / choices_en / target_en
QEN = {
    "C01": {
        "q1": {"target_en": "equivalent implementation", "question_en": "Your loop starts at i = 1 instead of i = 0. Why is that safe?",
               "choices_en": ["arr[0] is already used as the initial value of max, so skipping it only drops one redundant but harmless comparison",
                              "i = 0 would go out of bounds",
                              "The C standard requires such loops to start at 1",
                              "Starting at 1 cuts the number of comparisons in half"]},
        "q2": {"target_en": "complexity", "question_en": "What is the time complexity of this algorithm for an array of length n?",
               "choices_en": ["O(1)", "O(log n)", "O(n)", "O(n²)"]},
        "q3": {"target_en": "traversal necessity", "question_en": "For an array whose element order is unknown (arbitrary), which statement is correct?",
               "choices_en": ["There is an algorithm that always examines fewer than n elements and still finds the maximum",
                              "In the worst case every algorithm must examine all n elements to determine the maximum",
                              "Binary search can find the maximum in O(log n)",
                              "It suffices to compare the first and last elements"]},
        "q4": {"target_en": "precondition", "question_en": "The assignment guarantees n >= 1. If someone violates it and calls find_max(arr, 0), what does your code do? Why is this precondition necessary for correctness?",
               "choices_en": None},
    },
    "C02": {
        "q1": {"target_en": "initialization", "question_en": "What does your program return for the input [-5, -2, -10]?",
               "choices_en": None},
        "q2": {"target_en": "initialization", "question_en": "What is the most accurate explanation of that returned result?",
               "choices_en": None},
        "q3": {"target_en": "edge case", "question_en": "Which inputs make your program return a wrong result? (multiple choice)",
               "choices_en": None},
        "q4": {"target_en": "initialization", "question_en": "Before entering the loop, which value would you choose as the initial max? Explain why that initial value keeps the comparisons fair for every input, including all-negative arrays.",
               "choices_en": None},
    },
    "C03": {
        "q1": {"target_en": "index range", "question_en": "For a C array arr of length n, what is the range of valid indices?",
               "choices_en": None},
        "q2": {"target_en": "loop boundary", "question_en": "Which loop visits exactly arr[0] through arr[n-1], no more and no less?",
               "choices_en": None},
        "q3": {"target_en": "undefined behavior", "question_en": "When the program reads arr[n], how does the C standard define that behavior?",
               "choices_en": None},
        "q4": {"target_en": "loop boundary", "question_en": "Your loop condition uses <= before n. Answer: for an array of length n, what is the last valid index, and at what value of i must the loop stop?",
               "choices_en": None},
    },
    "C04": {
        "q1": {"target_en": "wrong comparison", "question_en": "For the input [3, 9, 4, 1], what does your program actually return?",
               "choices_en": None},
        "q2": {"target_en": "loop invariant", "question_en": "In a find-the-maximum loop, what invariant should hold after each iteration?",
               "choices_en": None},
        "q3": {"target_en": "wrong comparison", "question_en": "To maintain that invariant, which update rule should be used when examining the new element arr[i]?",
               "choices_en": None},
        "q4": {"target_en": "transfer to minimum", "question_en": "If the task were changed to find the minimum, how would you conceptually adjust the update invariant and the comparison condition? Answer in one sentence.",
               "choices_en": None},
    },
    "C05": {
        "q1": {"target_en": "incomplete traversal", "question_en": "For the input [2, 10, 5, 3] (n = 4), what does your program return?",
               "choices_en": None},
        "q2": {"target_en": "traversal necessity", "question_en": "With no prior information about the array contents, why must finding the maximum examine every element?",
               "choices_en": None},
        "q3": {"target_en": "role of traversal", "question_en": "What is the core role of the loop construct in algorithms of this kind?",
               "choices_en": None},
        "q4": {"target_en": "incomplete traversal", "question_en": "For the array [7, 2, 9, 4, 1], list the elements your program actually compared and explain why the true maximum was never discovered.",
               "choices_en": None},
    },
    "C06": {
        "q1": {"target_en": "missing return", "question_en": "In C, control reaches the end of an int-returning function without executing any return, and the caller uses the return value. How does the standard define this?",
               "choices_en": None},
        "q2": {"target_en": "return mechanism", "question_en": "The variable max does hold the correct result at the end. Why can printf(\"%d\", find_max(arr, n)) still not print it reliably?",
               "choices_en": None},
        "q3": {"target_en": "return mechanism", "question_en": "For the caller to write int m = find_max(arr, n); and get the correct result, what must the function do?",
               "choices_en": None},
        "q4": {"target_en": "missing return", "question_en": "Distinguish the mechanisms: how is a value computed inside the function, and how is it handed back to the caller? Which one does your code complete?",
               "choices_en": None},
    },
    "C07": {
        "q1": {"target_en": "redundant comparison", "question_en": "In the first round (i = 0) your loop evaluates arr[0] > max. What is the result necessarily, and why?",
               "choices_en": None},
        "q2": {"target_en": "equivalent implementation", "question_en": "Compared with the version that starts at i = 1, how does this program behave differently?",
               "choices_en": None},
        "q3": {"target_en": "comparison count", "question_en": "For an array of length n, how many comparisons do the i = 0 and i = 1 versions perform respectively?",
               "choices_en": None},
        "q4": {"target_en": "equivalent implementation", "question_en": "Since both versions are correct, in which situations would you prefer starting at i = 1? Give one engineering reason.",
               "choices_en": None},
    },
    "C08": {
        "q1": {"target_en": "initialization", "question_en": "Why is it safe to use arr[n - 1] as the initial value of max here?",
               "choices_en": None},
        "q2": {"target_en": "reverse traversal", "question_en": "Which elements does the loop for (int i = n - 2; i >= 0; i--) examine?",
               "choices_en": None},
        "q3": {"target_en": "reverse traversal", "question_en": "When n = 1, what does this function do?",
               "choices_en": None},
        "q4": {"target_en": "coverage completeness", "question_en": "Why is reverse traversal equivalent to forward traversal in correctness? Explain from the coverage perspective in a sentence or two.",
               "choices_en": None},
    },
    "C09": {
        "q1": {"target_en": "sentinel initialization", "question_en": "Why is using -2147483648 as the initial value correct for this problem?",
               "choices_en": None},
        "q2": {"target_en": "value range", "question_en": "Would replacing it with an arbitrary negative constant (for example -9999) still always be correct?",
               "choices_en": None},
        "q3": {"target_en": "value range", "question_en": "If the array elements were long long but the initial value stayed -2147483648, what would happen?",
               "choices_en": None},
        "q4": {"target_en": "sentinel initialization", "question_en": "What premise does the sentinel approach (initializing with the type minimum) rely on? How does the safety condition differ from initializing with an array element?",
               "choices_en": None},
    },
    "C10": {
        "q1": {"target_en": "initialization out of bounds", "question_en": "Under the assignment guarantee n >= 1, what happens when your program is called as find_max(arr, 1)?",
               "choices_en": None},
        "q2": {"target_en": "incomplete traversal", "question_en": "For the input [5, 3, 2] (n = 3), what does the program return, and what is the true maximum?",
               "choices_en": None},
        "q3": {"target_en": "candidate validity", "question_en": "Conceptually, what condition must the initial candidate for a maximum satisfy?",
               "choices_en": None},
        "q4": {"target_en": "incomplete traversal", "question_en": "For [9, 4, 7], what does your function return? Explain specifically why 9 is never found.",
               "choices_en": None},
    },
    "C11": {
        "q1": {"target_en": "invalid sentinel", "question_en": "For the input [-10000, -20000], what does your program return?",
               "choices_en": None},
        "q2": {"target_en": "failing input", "question_en": "For which class of inputs is this program guaranteed to return a wrong result?",
               "choices_en": None},
        "q3": {"target_en": "value range", "question_en": "Why do arr[0] or the int type minimum avoid this problem?",
               "choices_en": None},
        "q4": {"target_en": "failing input", "question_en": "Give one concrete input that makes the program return a wrong result and explain step by step how the error arises.",
               "choices_en": None},
    },
    "C12": {
        "q1": {"target_en": "failing input", "question_en": "For the input [3, 9, 2] (n = 3), what does your program return?",
               "choices_en": None},
        "q2": {"target_en": "incomplete traversal", "question_en": "Regardless of the input, which element does your program never examine?",
               "choices_en": None},
        "q3": {"target_en": "coverage completeness", "question_en": "What does traversal completeness mean for algorithms of this kind?",
               "choices_en": None},
        "q4": {"target_en": "incomplete traversal", "question_en": "For [1, 8, 2, 6], list the elements your program actually compared and point out which candidate was skipped.",
               "choices_en": None},
    },
    "C13": {
        "q1": {"target_en": "failing input", "question_en": "For the input [2, 5, 9] (n = 3), what does your program return?",
               "choices_en": None},
        "q2": {"target_en": "loop boundary", "question_en": "Which inputs expose the defect of this program?",
               "choices_en": None},
        "q3": {"target_en": "loop boundary", "question_en": "What is the essential difference between the conditions i < n - 1 and i < n?",
               "choices_en": None},
        "q4": {"target_en": "test design", "question_en": "Why do off-by-one loop errors like this easily survive casual testing? How should test inputs be constructed?",
               "choices_en": None},
    },
    "C14": {
        "q1": {"target_en": "tracing", "question_en": "For the input [5, 2, 9, 1] (n = 4), what does your program return?",
               "choices_en": None},
        "q2": {"target_en": "index vs value", "question_en": "Where exactly is the defect of this program?",
               "choices_en": None},
        "q3": {"target_en": "loop invariant", "question_en": "What role should the max variable play throughout the function?",
               "choices_en": None},
        "q4": {"target_en": "loop invariant", "question_en": "Using the notion of a loop invariant, explain why the line max = i breaks the correctness of the program.",
               "choices_en": None},
    },
    "C15": {
        "q1": {"target_en": "failing input", "question_en": "For the input [1, 3, 9] (n = 3), what does your program return?",
               "choices_en": None},
        "q2": {"target_en": "premature return", "question_en": "What is the root cause of the defect in this program?",
               "choices_en": None},
        "q3": {"target_en": "traversal necessity", "question_en": "In a find-the-maximum loop, under what condition is an early return allowed?",
               "choices_en": None},
        "q4": {"target_en": "premature return", "question_en": "Give one concrete input showing how the early return causes a wrong result, and point out which elements were left unchecked.",
               "choices_en": None},
    },
    "C16": {
        "q1": {"target_en": "return value", "question_en": "For the input [4, 7, 2] (n = 3), after int m = find_max(arr, n); what is the value of m?",
               "choices_en": None},
        "q2": {"target_en": "internal vs result", "question_en": "Just before the return statement executes, what value does the variable max hold?",
               "choices_en": None},
        "q3": {"target_en": "return value", "question_en": "Where is the defect of this program?",
               "choices_en": None},
        "q4": {"target_en": "internal vs result", "question_en": "\"Computed correctly inside\" and \"the function's result is correct\" are two things. Explain the difference using this program.",
               "choices_en": None},
    },
    "C17": {
        "q1": {"target_en": "no side effect", "question_en": "Looking only at the return value: does this function return the maximum of the array for every input?",
               "choices_en": None},
        "q2": {"target_en": "API contract", "question_en": "Even though the return value is correct, this submission still violates the assignment. Why?",
               "choices_en": None},
        "q3": {"target_en": "API contract", "question_en": "After int m = find_max(a, 5);, in what state is the caller's array a?",
               "choices_en": None},
        "q4": {"target_en": "no side effect", "question_en": "Why is \"the return value is correct\" not sufficient to accept this function? Argue from the caller's perspective.",
               "choices_en": None},
    },
    "C18": {
        "q1": {"target_en": "multi defect", "question_en": "How many independent defects are in this code, and what are they?",
               "choices_en": None},
        "q2": {"target_en": "out of bounds", "question_en": "For the input [-3, -7] (n = 2), which indices does the loop actually access?",
               "choices_en": None},
        "q3": {"target_en": "initialization", "question_en": "Suppose the loop condition were fixed to i < n; for the input [-3, -7] what would the program return, and why?",
               "choices_en": None},
        "q4": {"target_en": "multi defect", "question_en": "Explain for each of the two defects under which inputs it manifests, and why fixing one does not remove the other.",
               "choices_en": None},
    },
}

# 诊断 issues 的英文描述（按 case → 顺序列表）
ISSUES_EN = {
    "C02": ["max initialized to 0: for all-negative inputs the result is always 0 instead of the true maximum."],
    "C03": ["The loop condition i <= n reads arr[n] when i = n, which is out of bounds (undefined behavior)."],
    "C04": ["The comparison arr[i] < max is inverted; the function maintains a minimum instead of a maximum."],
    "C05": ["Only arr[n - 1] is compared against arr[0]; interior elements are never examined, so the maximum can be missed."],
    "C06": ["No return statement: the computed result never reaches the caller (undefined behavior if the value is used)."],
    "C10": ["max is initialized from arr[1], which is out of bounds when n = 1, and arr[0] is never examined."],
    "C11": ["The sentinel -9999 fails for inputs whose elements are all below -9999."],
    "C12": ["The loop starts at i = 2, so arr[1] never participates in any comparison."],
    "C13": ["The condition i < n - 1 skips the last element; inputs with the maximum at the end return a wrong result."],
    "C14": ["The update assigns max = i (an index) instead of max = arr[i] (a value)."],
    "C15": ["The function returns arr[i] at the first larger element, so the remaining elements are never examined."],
    "C16": ["The function returns first instead of max, so the caller always receives the initial element."],
    "C17": ["The function sorts the caller's array in place, violating the assignment's requirement not to modify the original array."],
    "C18": ["Two independent defects: max = 0 (fails on all-negative inputs) and i <= n (out-of-bounds read of arr[n])."],
}

# 样例 note_en（按 case → qid → 样例序号）
NOTES_EN = {
    "C01": {
        "q4": ["Accurate understanding of the precondition",
               "Names the out-of-bounds access but not why the precondition is required",
               "Contradicts C semantics: an out-of-bounds read does not yield 0",
               "No judgeable evidence provided"],
    },
    "C03": {
        "q4": ["Correct understanding of the loop boundary and termination condition",
               "Knows the last valid index but not why the loop must stop there",
               "Contradicts array semantics: claims arr[n] is a valid element",
               "No judgeable evidence provided"],
    },
}

# ---------------------------------------------------------------- 变异题（q5）
# expected_letter: 正确答案位置（打散）；distractor: 档案答错时用的干扰项字母
VAR = {
    "C01": {
        "line": 3, "replacement": "for (int i = 0; i < n; i++) {",
        "target": "equivalent-implementation", "target_en": "equivalent implementation",
        "expected": "A", "distractor": "B",
        "q": "假设修改：把第 3 行的 i = 1 改成 i = 0，程序的行为会怎样？（点击本题可在左侧预览修改后的代码）",
        "q_en": "Hypothetical change: replace i = 1 with i = 0 on line 3. How does the behavior change? (Click this question to preview the modified code on the left)",
        "choices": ["结果仍正确，只是第一轮多了一次 arr[0] 与自身的无害比较",
                    "程序会数组越界崩溃",
                    "当最大值出现在首位时会返回错误结果",
                    "无法通过编译"],
        "choices_en": ["Still correct; the first round just adds one harmless self-comparison of arr[0]",
                       "The program crashes with an out-of-bounds access",
                       "It returns a wrong result whenever the maximum is the first element",
                       "The code no longer compiles"],
    },
    "C02": {
        "line": 2, "replacement": "int max = arr[0];",
        "target": "initialization", "target_en": "initialization",
        "expected": "B", "distractor": "A",
        "q": "假设修改：把第 2 行的 max = 0 改成用 arr[0] 初始化，程序的行为会怎样？（点击本题可在左侧预览修改后的代码）",
        "q_en": "Hypothetical change: initialize max from arr[0] instead of 0 on line 2. How does the behavior change? (Click this question to preview the modified code on the left)",
        "choices": ["仍然对全负数输入返回 0",
                    "全负数输入也能返回正确的最大值",
                    "所有输入都变成错误结果",
                    "arr[0] 会越界访问"],
        "choices_en": ["All-negative inputs still return 0",
                       "All-negative inputs now return the correct maximum",
                       "Every input becomes wrong",
                       "arr[0] would be an out-of-bounds access"],
    },
    "C03": {
        "line": 4, "replacement": "for (int i = 1; i < n; i++) {",
        "target": "loop-boundary", "target_en": "loop boundary",
        "expected": "C", "distractor": "B",
        "q": "假设修改：把第 4 行的 i <= n 改成 i < n，程序的行为会怎样？（点击本题可在左侧预览修改后的代码）",
        "q_en": "Hypothetical change: replace i <= n with i < n on line 4. How does the behavior change? (Click this question to preview the modified code on the left)",
        "choices": ["仍然越界访问 arr[n]",
                    "会漏掉 arr[n-1]，最大值可能出错",
                    "消除了越界访问，程序变为正确",
                    "无法通过编译"],
        "choices_en": ["It still reads arr[n] out of bounds",
                       "It skips arr[n-1], so the maximum may be wrong",
                       "The out-of-bounds access disappears and the program becomes correct",
                       "The code no longer compiles"],
    },
    "C04": {
        "line": 5, "replacement": "if (arr[i] > max) {",
        "target": "wrong-comparison", "target_en": "wrong comparison",
        "expected": "A", "distractor": "D",
        "q": "假设修改：把第 5 行的 < 改成 >，程序的行为会怎样？（点击本题可在左侧预览修改后的代码）",
        "q_en": "Hypothetical change: replace < with > on line 5. How does the behavior change? (Click this question to preview the modified code on the left)",
        "choices": ["程序变为正确地求最大值",
                    "程序改成求最小值",
                    "会产生越界访问",
                    "行为不变，仍然返回最小值"],
        "choices_en": ["The program becomes a correct find-the-maximum",
                       "The program turns into find-the-minimum",
                       "It produces an out-of-bounds access",
                       "Behavior is unchanged and it still returns the minimum"],
    },
    "C05": {
        "line": 4, "replacement": "if (arr[1] > max) {",
        "target": "incomplete-traversal", "target_en": "incomplete traversal",
        "expected": "B", "distractor": "A",
        "q": "假设修改：把第 4 行的 arr[n - 1] 改成 arr[1]。对输入 [7, 2, 9, 4]（n = 4），程序返回什么？（点击本题可在左侧预览修改后的代码）",
        "q_en": "Hypothetical change: replace arr[n - 1] with arr[1] on line 4. For the input [7, 2, 9, 4] (n = 4), what does the program return? (Click this question to preview the modified code on the left)",
        "choices": ["9，最大值被正确找到",
                    "7，仍然只比较了一个位置，9 未被检查",
                    "4，最后一个元素",
                    "越界访问导致崩溃"],
        "choices_en": ["9 — the maximum is found correctly",
                       "7 — still only one position is compared; 9 is never examined",
                       "4 — the last element",
                       "It crashes with an out-of-bounds access"],
    },
    "C06": {
        "line": 9, "replacement": "    return max;\n}",
        "target": "missing-return", "target_en": "missing return",
        "expected": "A", "distractor": "B",
        "q": "假设修改：把第 9 行的 } 换成先执行 return max; 再结束函数，程序的行为会怎样？（点击本题可在左侧预览修改后的代码）",
        "q_en": "Hypothetical change: replace the closing brace on line 9 with a return max; before the function ends. How does the behavior change? (Click this question to preview the modified code on the left)",
        "choices": ["调用者能可靠地拿到正确的最大值",
                    "返回值仍然是未定义行为",
                    "返回的是 max 的地址而不是值",
                    "编译错误：函数里不允许出现 return"],
        "choices_en": ["The caller now reliably receives the correct maximum",
                       "The return value is still undefined behavior",
                       "It returns the address of max instead of its value",
                       "Compile error: return is not allowed here"],
    },
    "C07": {
        "line": 3, "replacement": "for (int i = 1; i < n; i++) {",
        "target": "equivalent-implementation", "target_en": "equivalent implementation",
        "expected": "C", "distractor": "B",
        "q": "假设修改：把第 3 行的 i = 0 改成 i = 1，程序的行为会怎样？（点击本题可在左侧预览修改后的代码）",
        "q_en": "Hypothetical change: replace i = 0 with i = 1 on line 3. How does the behavior change? (Click this question to preview the modified code on the left)",
        "choices": ["会漏掉 arr[0]，最大值可能丢失",
                    "产生越界访问",
                    "结果仍正确，并少了第一轮 arr[0] 与自身的比较",
                    "无法通过编译"],
        "choices_en": ["arr[0] would be skipped and the maximum could be lost",
                       "It produces an out-of-bounds access",
                       "Still correct, with one fewer self-comparison of arr[0] on the first round",
                       "The code no longer compiles"],
    },
    "C08": {
        "line": 2, "replacement": "int max = arr[0];",
        "target": "coverage-completeness", "target_en": "coverage completeness",
        "expected": "B", "distractor": "A",
        "q": "假设修改：把第 2 行改成 int max = arr[0];（循环仍从 i = n - 2 开始反向遍历）。对输入 [1, 2, 9]（n = 3），程序返回什么？（点击本题可在左侧预览修改后的代码）",
        "q_en": "Hypothetical change: line 2 becomes int max = arr[0]; while the loop still walks in reverse from i = n - 2. For the input [1, 2, 9] (n = 3), what does the program return? (Click this question to preview the modified code on the left)",
        "choices": ["9，结果不受影响",
                    "2，arr[n-1] = 9 从未被比较，最大值丢失",
                    "1，返回了初始值",
                    "越界访问导致崩溃"],
        "choices_en": ["9 — the result is unaffected",
                       "2 — arr[n-1] = 9 is never compared, so the maximum is lost",
                       "1 — the initial value is returned",
                       "It crashes with an out-of-bounds access"],
    },
    "C09": {
        "line": 2, "replacement": "int max = -9999;",
        "target": "value-range", "target_en": "value range",
        "expected": "A", "distractor": "C",
        "q": "假设修改：把第 2 行的初值 -2147483648 改成 -9999，哪类输入会出错？（点击本题可在左侧预览修改后的代码）",
        "q_en": "Hypothetical change: replace the initial value -2147483648 with -9999 on line 2. Which inputs break? (Click this question to preview the modified code on the left)",
        "choices": ["元素全部小于 -9999 的输入（例如 -10000）",
                    "所有输入都会出错",
                    "元素全部大于 0 的输入",
                    "没有任何差别"],
        "choices_en": ["Inputs whose elements are all below -9999 (for example -10000)",
                       "Every input breaks",
                       "Inputs whose elements are all positive",
                       "There is no difference at all"],
    },
    "C10": {
        "line": 2, "replacement": "int max = arr[0];",
        "target": "incomplete-traversal", "target_en": "incomplete traversal",
        "expected": "C", "distractor": "B",
        "q": "假设修改：只把第 2 行改成 int max = arr[0];（循环仍从 i = 2 开始）。对输入 [5, 8, 2]（n = 3），程序返回什么？（点击本题可在左侧预览修改后的代码）",
        "q_en": "Hypothetical change: only line 2 becomes int max = arr[0]; while the loop still starts at i = 2. For the input [5, 8, 2] (n = 3), what does the program return? (Click this question to preview the modified code on the left)",
        "choices": ["8，程序被修复",
                    "2，最后一个元素",
                    "5，arr[1] = 8 仍未被检查",
                    "越界访问导致崩溃"],
        "choices_en": ["8 — the program is fixed",
                       "2 — the last element",
                       "5 — arr[1] = 8 is still never examined",
                       "It crashes with an out-of-bounds access"],
    },
    "C11": {
        "line": 2, "replacement": "int max = arr[0];",
        "target": "sentinel-invalid", "target_en": "invalid sentinel",
        "expected": "B", "distractor": "C",
        "q": "假设修改：把第 2 行的 -9999 改成用 arr[0] 初始化，程序的行为会怎样？（点击本题可在左侧预览修改后的代码）",
        "q_en": "Hypothetical change: replace -9999 on line 2 with an initialization from arr[0]. How does the behavior change? (Click this question to preview the modified code on the left)",
        "choices": ["仍然对全负数输入出错",
                    "对任意满足 n >= 1 的输入都返回正确最大值",
                    "只在数组长度为 1 时正确",
                    "会产生越界访问"],
        "choices_en": ["All-negative inputs still break",
                       "It returns the correct maximum for every input with n >= 1",
                       "It is correct only for arrays of length 1",
                       "It produces an out-of-bounds access"],
    },
    "C12": {
        "line": 3, "replacement": "for (int i = 1; i < n; i++) {",
        "target": "incomplete-traversal", "target_en": "incomplete traversal",
        "expected": "A", "distractor": "B",
        "q": "假设修改：把第 3 行的 i = 2 改成 i = 1，程序的行为会怎样？（点击本题可在左侧预览修改后的代码）",
        "q_en": "Hypothetical change: replace i = 2 with i = 1 on line 3. How does the behavior change? (Click this question to preview the modified code on the left)",
        "choices": ["arr[1] 被纳入比较，程序变为正确",
                    "仍然漏掉 arr[1]",
                    "会产生越界访问",
                    "只影响 n = 1 的输入"],
        "choices_en": ["arr[1] now participates in comparisons and the program becomes correct",
                       "arr[1] is still skipped",
                       "It produces an out-of-bounds access",
                       "Only inputs with n = 1 are affected"],
    },
    "C13": {
        "line": 3, "replacement": "for (int i = 1; i < n; i++) {",
        "target": "loop-boundary", "target_en": "loop boundary",
        "expected": "B", "distractor": "C",
        "q": "假设修改：把第 3 行的 i < n - 1 改成 i < n，程序的行为会怎样？（点击本题可在左侧预览修改后的代码）",
        "q_en": "Hypothetical change: replace i < n - 1 with i < n on line 3. How does the behavior change? (Click this question to preview the modified code on the left)",
        "choices": ["仍然漏掉最后一个元素",
                    "最后一个元素被纳入比较，程序变为正确",
                    "会越界访问 arr[n]",
                    "无法通过编译"],
        "choices_en": ["The last element is still skipped",
                       "The last element is now compared and the program becomes correct",
                       "It reads arr[n] out of bounds",
                       "The code no longer compiles"],
    },
    "C14": {
        "line": 5, "replacement": "max = arr[i];",
        "target": "index-vs-value", "target_en": "index vs value",
        "expected": "A", "distractor": "B",
        "q": "假设修改：把第 5 行的 max = i; 改成 max = arr[i];，程序的行为会怎样？（点击本题可在左侧预览修改后的代码）",
        "q_en": "Hypothetical change: replace max = i; with max = arr[i]; on line 5. How does the behavior change? (Click this question to preview the modified code on the left)",
        "choices": ["程序变为正确：max 保存的是元素值本身",
                    "仍然返回下标",
                    "会产生越界访问",
                    "行为不变"],
        "choices_en": ["The program becomes correct: max holds the element value itself",
                       "It still returns an index",
                       "It produces an out-of-bounds access",
                       "Behavior is unchanged"],
    },
    "C15": {
        "line": 5, "replacement": "max = arr[i];",
        "target": "premature-return", "target_en": "premature return",
        "expected": "C", "distractor": "B",
        "q": "假设修改：把第 5 行的 return arr[i]; 改成 max = arr[i];，程序的行为会怎样？（点击本题可在左侧预览修改后的代码）",
        "q_en": "Hypothetical change: replace return arr[i]; with max = arr[i]; on line 5. How does the behavior change? (Click this question to preview the modified code on the left)",
        "choices": ["仍在第一次遇到更大元素时提前退出",
                    "会产生越界访问",
                    "循环完整执行，返回真正的最大值",
                    "无法通过编译（函数中出现两个 return）"],
        "choices_en": ["It still exits early at the first larger element",
                       "It produces an out-of-bounds access",
                       "The loop runs to completion and returns the true maximum",
                       "Compile error: two return statements in one function"],
    },
    "C16": {
        "line": 9, "replacement": "    return max;",
        "target": "return-value", "target_en": "return value",
        "expected": "B", "distractor": "A",
        "q": "假设修改：把第 9 行的 return first; 改成 return max;，程序的行为会怎样？（点击本题可在左侧预览修改后的代码）",
        "q_en": "Hypothetical change: replace return first; with return max; on line 9. How does the behavior change? (Click this question to preview the modified code on the left)",
        "choices": ["仍然返回首元素",
                    "返回 max，调用者拿到正确结果",
                    "变成未定义行为",
                    "无法通过编译"],
        "choices_en": ["It still returns the first element",
                       "It returns max and the caller gets the correct result",
                       "It becomes undefined behavior",
                       "The code no longer compiles"],
    },
    "C17": {
        "line": 4, "replacement": "if (arr[j] < arr[j + 1]) {",
        "target": "wrong-comparison", "target_en": "wrong comparison",
        "expected": "A", "distractor": "B",
        "q": "假设修改：把第 4 行的 > 改成 <（排序方向反转），函数的返回值会怎样？（点击本题可在左侧预览修改后的代码）",
        "q_en": "Hypothetical change: replace > with < on line 4 (the sort direction flips). What happens to the return value? (Click this question to preview the modified code on the left)",
        "choices": ["返回最小值：升序变降序后，末位不再是最小值",
                    "仍返回最大值",
                    "会产生越界访问",
                    "无法通过编译"],
        "choices_en": ["It returns the minimum: after switching to descending order, the last element is no longer… wait — it returns the smallest element",
                       "It still returns the maximum",
                       "It produces an out-of-bounds access",
                       "The code no longer compiles"],
    },
    "C18": {
        "line": 3, "replacement": "for (int i = 1; i < n; i++) {",
        "target": "multi-defect", "target_en": "multi defect",
        "expected": "B", "distractor": "A",
        "q": "假设修改：只把第 3 行的 i <= n 改成 i < n（第 2 行仍是 max = 0）。对输入 [-3, -7]（n = 2），程序返回什么？（点击本题可在左侧预览修改后的代码）",
        "q_en": "Hypothetical change: replace i <= n with i < n on line 3 only (line 2 is still max = 0). For the input [-3, -7] (n = 2), what does the program return? (Click this question to preview the modified code on the left)",
        "choices": ["-3，两个缺陷都被修复",
                    "0，越界消除了，但初值缺陷仍在",
                    "-7，返回最后一个元素",
                    "仍然越界崩溃"],
        "choices_en": ["-3 — both defects are fixed",
                       "0 — the out-of-bounds access is gone but the initialization defect remains",
                       "-7 — the last element is returned",
                       "It still crashes with an out-of-bounds access"],
    },
}

# C17 选项 A 中文里误留了英文草稿，修正
VAR["C17"]["choices"][0] = "返回最小值：排序方向反转后，末位变成最小元素"
VAR["C17"]["choices_en"][0] = "It returns the minimum: after the sort direction flips, the last element is the smallest"

# q5 的知识点中文名（按案例）
TARGET_ZH = {
    "C01": "等价实现确认", "C02": "初始化", "C03": "循环边界", "C04": "比较方向",
    "C05": "遍历完整性", "C06": "返回机制", "C07": "等价实现", "C08": "遍历覆盖",
    "C09": "取值范围", "C10": "遍历完整性", "C11": "哨兵有效性", "C12": "遍历完整性",
    "C13": "循环边界", "C14": "下标与元素值", "C15": "提前返回", "C16": "返回值",
    "C17": "比较方向", "C18": "多缺陷分离",
}


def variant_line_text(code: str, line_no: int) -> str:
    lines = code.split("\n")
    return lines[line_no - 1].strip() if 1 <= line_no <= len(lines) else ""


def _env() -> dict:
    env = {}
    path = ROOT / ".env.local"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def translate_notes(pairs: dict) -> dict:
    """调用项目配置的真实 LLM（ark-code-latest）把中文反馈短语批量译成英文。"""
    import urllib.request

    env = _env()
    body = {
        "model": env.get("LLM_MODEL", "ark-code-latest"),
        "messages": [
            {"role": "system", "content": (
                "You are a translator for an educational assessment system. Translate "
                "each Chinese feedback phrase into concise natural English suitable for "
                "TA feedback to a student. Output ONLY a JSON object mapping each id to "
                "its English translation, no markdown, no explanations."
            )},
            {"role": "user", "content": json.dumps(pairs, ensure_ascii=False)},
        ],
        "temperature": 0.1,
        "max_tokens": 6000,
        "timeout": 180,
    }
    req = urllib.request.Request(
        env["LLM_BASE_URL"].rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + env["LLM_API_KEY"],
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=200) as r:
        data = json.load(r)
    content = data["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", content, re.DOTALL)
    return json.loads(m.group(0))


def main():
    import sys
    sys.path.insert(0, str(ROOT))
    from app import cases as cases_mod, storage
    from app.agent import generate_questionnaire
    from app.evaluator import evaluate
    from app.llm import MockLLMProvider
    from app.models import ResponseFile, Submission, now_utc

    provider = MockLLMProvider()
    assignment = storage.load_assignment()

    # ---- 先收集全部样例注释，调用真实 LLM 批量翻译（失败则跳过，EN 反馈省略提示句）----
    note_pairs = {}
    for i in range(1, 19):
        case = json.loads((CASES_DIR / f"C{i:02d}.json").read_text(encoding="utf-8"))
        for q in case["questions"]:
            for si, sample in enumerate(q.get("samples", [])):
                if sample.get("note"):
                    note_pairs[f"C{i:02d}|{q['id']}|{si}"] = sample["note"]
    note_en_map = {}
    try:
        note_en_map = translate_notes(note_pairs)
        print(f"[LLM] 样例注释翻译完成：{len(note_en_map)}/{len(note_pairs)}")
    except Exception as e:  # noqa: BLE001 —— 翻译失败不阻塞补丁，仅缺英文提示
        print(f"[WARN] 样例注释翻译失败（{e}），EN 反馈将不含针对性提示")

    REQUIRED = {  # profile_id -> (understanding, needs_ta_review)
        "mastered": ("mastered", False),
        "partial": ("partial", False),
        "contradictory": ("limited", True),
        "insufficient": ("insufficient", True),
    }

    def eval_profile(case_dict, profile):
        """用当前判分引擎跑一遍某档案，返回 (understanding, needs_ta_review)。"""
        from app.models import Diagnosis, Question, Questionnaire
        questions = [Question.model_validate(qd) for qd in case_dict["questions"]]
        manual = Questionnaire(
            student_id="90000001", assignment_id="c-find-max",
            diagnosis=Diagnosis.model_validate(case_dict["diagnosis"]),
            questions=questions, case_id=case_dict["case_id"], case_match="exact",
            code_fingerprint=cases_mod.code_fingerprint(case_dict["code"]),
        )
        answers = manual.normalize_answers(profile["answers"])
        resp = ResponseFile(student_id="90000001", submitted_at=now_utc(),
                            answers=answers, attempt_count=1)
        ev = evaluate(provider, assignment,
                      Submission(student_id="90000001", assignment_id="c-find-max",
                                 code=case_dict["code"], language="c", submitted_at="x"),
                      manual, resp)
        return ev.understanding.value, ev.needs_ta_review

    for i in range(1, 19):
        cid = f"C{i:02d}"
        path = CASES_DIR / f"{cid}.json"
        case = json.loads(path.read_text(encoding="utf-8"))
        code = case["code"]

        # ---- 1) 双语元数据 ----
        case["title_en"] = TITLES_EN[cid]
        case["behavior_notes_en"] = BEHAVIOR_EN[cid]
        case.setdefault("diagnosis", {})["summary_en"] = SUMMARY_EN[cid]
        for k, iss in enumerate(case["diagnosis"].get("issues", [])):
            if cid in ISSUES_EN and k < len(ISSUES_EN[cid]):
                iss.setdefault("description_en", ISSUES_EN[cid][k])
        case["time_limit_minutes"] = 20

        # ---- 2) 题目英文 + 样例 note_en + 英文变体 ----
        for q in case["questions"]:
            en = QEN[cid].get(q["id"])
            if en:
                q["question_en"] = en["question_en"]
                if en["choices_en"] is not None and q.get("choices"):
                    q["choices_en"] = en["choices_en"]
                q["target_en"] = en["target_en"]
            for si, sample in enumerate(q.get("samples", [])):
                notes = NOTES_EN.get(cid, {}).get(q["id"], [])
                if si < len(notes):
                    sample["note_en"] = notes[si]
                elif not sample.get("note_en"):
                    en_note = note_en_map.get(f"{cid}|{q['id']}|{si}", "")
                    if en_note:
                        sample["note_en"] = en_note
                # C01-C06（演示学生案例）：为 insufficient 样例补英文变体
                if i <= 6 and sample["verdict"] == "insufficient" and "I don't know" not in sample["variants"]:
                    sample["variants"].append("I don't know")

        # ---- 3) 追加 q5 变异题 ----
        v = VAR[cid]
        orig_line = variant_line_text(code, v["line"])
        case["questions"].append({
            "id": "q5",
            "type": "code_variant",
            "question": v["q"],
            "question_en": v["q_en"],
            "choices": v["choices"],
            "choices_en": v["choices_en"],
            "target_issue": v["target"],
            "target_zh": TARGET_ZH[cid],
            "target_en": v["target_en"],
            "expected_answer": v["expected"],
            "code_lines": [v["line"]],
            "stage": "initial",
            "variant": {"line_no": v["line"], "replacement": v["replacement"]},
        })

        # ---- 4) 档案补 q5 + 重算 expect（保持四档语义）----
        wrong_letter = v["distractor"] if v["distractor"] != v["expected"] else (
            "B" if v["expected"] != "B" else "C"
        )
        for profile in case["profiles"]:
            required = REQUIRED[profile["profile_id"]]
            decided = None
            for cand in (v["expected"], wrong_letter):
                profile["answers"]["q5"] = cand
                got = eval_profile(case, profile)
                if got == required:
                    decided = cand
                    break
            if decided is None:
                print(f"[FAIL] {cid}/{profile['profile_id']}: 无法用 q5∈{{正确,错误}} 恢复语义 {required}")
                continue
            profile["expect"] = {
                "understanding": required[0],
                "needs_ta_review": required[1],
            }

        # ---- 5) 展示文本 token 包裹（不动 variants / expected / answers）----
        case["behavior_notes"] = wrap_tokens(case.get("behavior_notes", ""), zh=True)
        case["behavior_notes_en"] = wrap_tokens(case.get("behavior_notes_en", ""), zh=False)
        case["diagnosis"]["summary"] = wrap_tokens(case["diagnosis"].get("summary", ""), zh=True)
        case["diagnosis"]["summary_en"] = wrap_tokens(case["diagnosis"].get("summary_en", ""), zh=False)
        for iss in case["diagnosis"].get("issues", []):
            iss["description"] = wrap_tokens(iss.get("description", ""), zh=True)
            if iss.get("description_en"):
                iss["description_en"] = wrap_tokens(iss["description_en"], zh=False)
        for q in case["questions"]:
            q["question"] = wrap_tokens(q.get("question", ""), zh=True)
            if q.get("question_en"):
                q["question_en"] = wrap_tokens(q["question_en"], zh=False)
            for key in ("choices", "choices_en"):
                if q.get(key):
                    q[key] = [wrap_tokens(c, zh=(key == "choices")) for c in q[key]]
            for sample in q.get("samples", []):
                if sample.get("note"):
                    sample["note"] = wrap_tokens(sample["note"], zh=True)
                if sample.get("note_en"):
                    sample["note_en"] = wrap_tokens(sample["note_en"], zh=False)

        path.write_text(json.dumps(case, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[OK] {cid}: q5({v['target']}, expected={v['expected']}) + EN + 20min")

    print("完成。请运行 pytest 验证 72 组档案的 expect 与判分引擎一致。")


if __name__ == "__main__":
    main()
