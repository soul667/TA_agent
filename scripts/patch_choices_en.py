"""一次性补丁：为全部 Mock 案例的选择题补齐 choices_en（英文选项，含 $…$/`…` 标记）。

纯数字/代码片段的选项无中文时自动复制原文。运行后跑 pytest 验证。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = ROOT / "data" / "mock_cases"

CHOICES_EN = {
    ("C02", "q2"): [
        "The loop runs one iteration short, so the largest element is missed",
        "The initial max is the constant 0, which is not in the array; every element is smaller, so $max$ is never updated",
        "The comparison operator direction is reversed",
        "An out-of-bounds index reads unrelated memory",
    ],
    ("C03", "q1"): ["1 to $n$", "0 to $n$-1", "0 to $n$", "1 to $n$-1"],
    ("C03", "q3"): [
        "Returns a random value while the rest of the program behaves normally",
        "Undefined behavior: it may crash, read garbage, or produce any other result",
        "The compiler intercepts it at runtime and reports an error",
        "The access wraps around to `arr[0]`",
    ],
    ("C04", "q2"): [
        "$max$ is the minimum among the elements traversed so far",
        "$max$ is the maximum among the elements traversed so far",
        "$max$ equals the last element of the array",
        "$max$ always equals `arr[0]`",
    ],
    ("C04", "q3"): [
        "When `arr[i]` < $max$, update $max$ with `arr[i]`",
        "When `arr[i]` > $max$, update $max$ with `arr[i]`",
        "Update $max$ with every `arr[i]` unconditionally",
        "Update only when `arr[i]` == $max$",
    ],
    ("C05", "q2"): [
        "Because the C syntax forces you to",
        "Because any unchecked element could be larger than the current maximum; skipping it breaks the guarantee",
        "Because more comparisons make the program run faster",
        "Because not checking one by one causes a memory leak",
    ],
    ("C05", "q3"): [
        "It makes the code shorter",
        "It repeats the same compare-and-update rule for every candidate, guaranteeing full coverage",
        "It fixes the number of comparisons at 2",
        "It computes the array length",
    ],
    ("C06", "q1"): [
        "Automatically returns 0",
        "Automatically returns the last variable assigned inside the function",
        "Undefined behavior; the result is unpredictable",
        "The compiler always errors out and no executable is produced",
    ],
    ("C06", "q2"): [
        "$max$ is a local variable; only a return statement hands its value back to the caller",
        "The printf function itself is defective",
        "$max$ was never initialized",
        "The array was modified inside the function",
    ],
    ("C06", "q3"): [
        "Return the result to the caller with a return statement before exiting",
        "Declare $max$ as a global variable, otherwise it never works",
        "Write the result to a file for the caller to read",
        "Nothing — the return value becomes available automatically",
    ],
    ("C07", "q1"): [
        "Always false: $max$ starts as `arr[0]`, and a number is not greater than itself",
        "Always true: the first round always updates $max$",
        "It depends on the array contents",
        "It is undefined behavior",
    ],
    ("C07", "q2"): [
        "Identical result, just one extra comparison that can never update $max$",
        "The result may differ: the maximum could be missed",
        "It reads `arr[n]` out of bounds",
        "One element fewer is examined",
    ],
    ("C07", "q3"): ["$n$-1 and $n$", "$n$ and $n$-1", "Both $n$", "Both $n$-1"],
    ("C08", "q1"): [
        "It is a valid candidate that really exists in the array; under the guarantee $n >= 1$ that index is always accessible",
        "Because the last element is always the maximum",
        "Because any negative number works as an initial value",
        "It is not safe: `arr[n-1]` is an out-of-bounds access",
    ],
    ("C08", "q2"): [
        "All elements from `arr[n-2]` back to `arr[0]`; `arr[n-1]` already served as the initial value",
        "Only the first half of the array",
        "All elements including `arr[n-1]`",
        "`arr[0]` is missed",
    ],
    ("C08", "q3"): [
        "The initial value is `arr[0]`; the loop condition $n$-2 = -1 fails immediately, so `arr[0]` is returned",
        "It reads `arr[-1]` out of bounds",
        "It loops forever",
        "It returns 0",
    ],
    ("C09", "q1"): [
        "No int value is smaller than it, so the first comparison necessarily sets $max$ to an array element (or an equal value)",
        "Because it is the largest negative number",
        "Because every array contains this value",
        "Just a habit; correctness is unaffected",
    ],
    ("C09", "q2"): [
        "Not necessarily: if every element is below `-9999`, $max$ stays at the sentinel, which is not in the array",
        "Always correct as long as the negative number is small enough",
        "Always wrong; only `arr[0]` works",
        "It depends on the array length",
    ],
    ("C09", "q3"): [
        "It can fail: elements may be smaller than the int minimum, so the sentinel is no longer a valid lower bound",
        "Completely unaffected",
        "It does not compile",
        "It automatically uses the long long minimum",
    ],
    ("C10", "q1"): [
        "It evaluates int $max$ = `arr[1]`, accessing a nonexistent index — out-of-bounds undefined behavior",
        "It returns `arr[0]` normally",
        "It returns 0",
        "The loop never runs; the program does nothing and has no side effects",
    ],
    ("C10", "q2"): [
        "Returns 3; the true maximum 5 never takes part in any comparison",
        "Returns 5; the program is correct",
        "Returns 2; the comparison direction is reversed",
        "The result is undefined and unpredictable",
    ],
    ("C10", "q3"): [
        "It is a candidate that exists in the array, and its index is accessible for every legal input satisfying the guarantee",
        "Numerically as small as possible, even a value outside the array",
        "It must be the constant 0",
        "It must be `arr[1]`",
    ],
    ("C11", "q2"): [
        "Arrays whose elements are all below `-9999`",
        "Arrays whose elements are all above `-9999`",
        "Every input fails",
        "Only arrays of length 1",
    ],
    ("C11", "q3"): [
        "Array elements are in the array and int values are never below the type minimum: both hold for every legal input",
        "Because they run faster",
        "Because the C syntax only allows this",
        "Just habits; they can equally fail",
    ],
    ("C12", "q2"): [
        "`arr[1]`: it is neither the initial value nor inside the loop range",
        "`arr[0]`: the initial element needs no check",
        "The last element",
        "Nothing is missed; everything is examined",
    ],
    ("C12", "q3"): [
        "Every element except the initial value takes part in at least one comparison",
        "The loop variable must start at 0",
        "The number of comparisons must equal $n$",
        "The array must be sorted",
    ],
    ("C13", "q2"): [
        "Arrays whose maximum happens to be the last element",
        "Arrays whose maximum is the first element",
        "Every input fails",
        "Only all-negative arrays",
    ],
    ("C13", "q3"): [
        "The former excludes `arr[n-1]` from comparisons, covering one valid element fewer",
        "They are fully equivalent",
        "The former checks `arr[0]` one time fewer",
        "The former reads `arr[n]` out of bounds",
    ],
    ("C14", "q2"): [
        "The update stores the index $i$ into $max$ instead of the element value `arr[i]`",
        "The maximum is initialized incorrectly",
        "The loop accesses out of bounds",
        "The function lacks a return",
    ],
    ("C14", "q3"): [
        "It always holds the maximum among the traversed elements (a value, not a position)",
        "It stores the index of the current element",
        "It stores the number of elements",
        "It stores the number of comparisons made",
    ],
    ("C15", "q2"): [
        "It returns before the traversal finishes, with no guarantee that the remaining elements are not larger",
        "The maximum is initialized incorrectly",
        "The comparison direction is reversed",
        "It reads `arr[n]` out of bounds",
    ],
    ("C15", "q3"): [
        "Only when it can be proven that no unchecked element can exceed the current value (this problem gives no such information, so the loop must finish)",
        "Return immediately upon finding one larger element",
        "No return may appear anywhere in the loop",
        "Every iteration must return once",
    ],
    ("C16", "q2"): [
        "7: the computation itself is fully correct",
        "4: same as the return value",
        "2",
        "0",
    ],
    ("C16", "q3"): [
        "It returns the wrong variable: the computed $max$ never reaches the caller",
        "The maximum computation is wrong",
        "The loop accesses out of bounds",
        "A variable was never initialized",
    ],
    ("C17", "q1"): [
        "Yes: after sorting, the last element is necessarily the maximum",
        "No: the return value is often wrong",
        "It is correct only for positive arrays",
        "It is undefined behavior",
    ],
    ("C17", "q2"): [
        "The assignment explicitly forbids modifying the original array, yet the sort swaps elements in place",
        "The bubble sort syntax is invalid",
        "Functions must not have nested loops",
        "The return type should be void",
    ],
    ("C17", "q3"): [
        "It is sorted ascending; the original order is lost",
        "It keeps its original order",
        "It is zeroed out",
        "Its content is undefined",
    ],
    ("C18", "q1"): [
        "Two: the initial value assumes a value outside the array, and the loop condition accesses one element too many",
        "One: only the out-of-bounds issue",
        "One: only the initialization issue",
        "None; the program is correct",
    ],
    ("C18", "q2"): [
        "1 and 2, where `arr[2]` is out of bounds",
        "0 and 1: the normal range",
        "Only 1",
        "0, 1 and 2, where 0 is redundant",
    ],
    ("C18", "q3"): [
        "0: the initial 0 is larger than every element, so $max$ is never updated",
        "-3: after the fix it returns the correct result",
        "-7",
        "Still undefined behavior",
    ],
    # "未定义" → Undefined 这类短标签
    ("C11", "q1"): ["`-9999`", "-10000", "-20000", "Undefined"],
    ("C12", "q1"): ["3", "9", "2", "Undefined"],
    ("C13", "q1"): ["5", "9", "2", "Undefined"],
    ("C15", "q1"): ["3", "9", "1", "Undefined"],
    ("C16", "q1"): ["4", "7", "2", "Undefined"],
}

HANZI = re.compile(r"[\u4e00-\u9fff]")


def main():
    patched = 0
    for i in range(1, 19):
        cid = f"C{i:02d}"
        path = CASES_DIR / f"{cid}.json"
        case = json.loads(path.read_text(encoding="utf-8"))
        for q in case["questions"]:
            if not q.get("choices"):
                continue
            en = CHOICES_EN.get((cid, q["id"]))
            if en is None:
                # 无中文的选项（纯数字/代码）直接复制
                if not any(HANZI.search(c) for c in q["choices"]):
                    en = list(q["choices"])
                else:
                    print(f"[MISS] {cid}/{q['id']} 仍缺英文选项")
                    continue
            assert len(en) == len(q["choices"]), f"{cid}/{q['id']} 选项数不一致"
            q["choices_en"] = en
            patched += 1
        path.write_text(json.dumps(case, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"完成：补齐 {patched} 道题的 choices_en")


if __name__ == "__main__":
    main()
