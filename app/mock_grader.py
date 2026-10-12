"""Mock 判分引擎：逐知识点、确定性、证据可追溯（docs §2 / §5）。

判分输入只有服务端保存的问卷与作答，学生请求中的任何字段不参与判分。

- 选择题：与参考答案精确匹配（basis=exact-choice，确定性）。
- text 题（有预置样例）：与样例变体显式匹配（basis=sample-match，确定性）；
  未命中 → verdict=uncovered（待人工核验），**不用**字词重合度冒充语义理解。
- text 题（无预置样例，如通用问卷）：字词重合启发式兜底（basis=heuristic），
  必须触发 needs_ta_review 并在反馈中明示。
"""
from __future__ import annotations

import re
from typing import Optional

# verdict 档位（高 → 低）；uncovered 独立处理
_VERDICT_ORDER = {"correct": 4, "partial": 3, "wrong": 2, "insufficient": 1}
_VERDICT_TO_KP = {"correct": "mastered", "partial": "partial",
                  "wrong": "limited", "insufficient": "insufficient"}
_VERDICT_ZH = {"correct": "答对", "partial": "部分正确", "wrong": "答错",
               "insufficient": "证据不足", "uncovered": "待核验"}
_VERDICT_EN = {"correct": "correct", "partial": "partially correct", "wrong": "wrong",
               "insufficient": "insufficient evidence", "uncovered": "uncovered"}
_KP_STATUS_ZH = {"mastered": "已表现出的理解", "partial": "部分理解",
                 "limited": "理解有限", "insufficient": "未表现出理解",
                 "uncovered": "证据不足 / 待核验"}
_KP_STATUS_EN = {"mastered": "solid understanding", "partial": "partial understanding",
                 "limited": "limited understanding", "insufficient": "no evidence of understanding",
                 "uncovered": "insufficient evidence / needs review"}


def normalize_text(s: str) -> str:
    """text 样例匹配用的规范化：统一全半角标点、去全部空白、小写。"""
    s = str(s or "")
    full = "，。；：、（）【】“”‘’！？"
    half = ",.;:,()[]\"\"''!?"
    table = str.maketrans(full, half)
    return re.sub(r"\s+", "", s.translate(table)).lower()


def _norm_choice(value) -> str:
    if isinstance(value, list):
        letters = sorted({str(p).strip().upper() for p in value if str(p).strip()})
        return ",".join(letters)
    return str(value or "").strip().upper()


def _grade_choice(item: dict) -> tuple[str, str, str, str]:
    got = _norm_choice(item.get("answer", ""))
    if not got:
        return "insufficient", "uncovered", "", ""
    expected = _norm_choice(item.get("expected", ""))
    return ("correct" if got == expected else "wrong"), "exact-choice", "", ""


def _grade_text(item: dict) -> tuple[str, str, str, str]:
    """返回 (verdict, basis, note_zh, note_en)。"""
    answer = str(item.get("answer", "") or "")
    samples = item.get("samples") or []
    if answer.strip() == "":
        return "insufficient", "uncovered", "", ""
    norm = normalize_text(answer)
    for sample in samples:
        for variant in sample.get("variants", []):
            if norm == normalize_text(variant):
                return (sample.get("verdict", "uncovered"), "sample-match",
                        sample.get("note", ""), sample.get("note_en", ""))
    if samples:
        # 案例流程：未覆盖的回答明确转待人工核验，不做启发式判分
        return "uncovered", "uncovered", "", ""
    # 无样例数据（通用问卷 / 直连调用）的字词重合启发式兜底
    ratio = _text_overlap(item.get("expected", ""), answer)
    if ratio >= 0.4:
        verdict = "correct"
    elif ratio >= 0.2:
        verdict = "partial"
    else:
        verdict = "wrong"
    return verdict, "heuristic", "", ""


def _bigrams(s: str) -> set[str]:
    tokens = re.findall(r"[\w\u4e00-\u9fff]+", str(s).lower())
    grams: set[str] = set()
    for t in tokens:
        if len(t) == 1:
            grams.add(t)
        else:
            grams.update(t[i: i + 2] for i in range(len(t) - 1))
    return grams


def _text_overlap(expected: str, answer: str) -> float:
    exp = _bigrams(expected)
    if not exp:
        return 0.0
    return len(exp & _bigrams(answer)) / len(exp)


def grade(items: list[dict], case_meta: Optional[dict] = None) -> dict:
    """items: GRADING_DATA_JSON 的题目列表（含 samples / target_zh / code_evidence）。

    返回与 Evaluation 模型对齐的 dict（含 knowledge_points 逐知识点证据与双语反馈）。
    """
    case_meta = case_meta or {}
    per_q: dict[str, tuple[str, str, str, str]] = {}
    for item in items:
        if item.get("type") == "text":
            per_q[item["qid"]] = _grade_text(item)
        else:
            per_q[item["qid"]] = _grade_choice(item)

    # ---- 知识点聚合（docs §5）----
    kp_map: dict[str, dict] = {}
    for item in items:
        point = item.get("target") or item.get("qid")
        kp = kp_map.setdefault(point, {
            "point": point,
            "point_zh": item.get("target_zh") or point,
            "point_en": item.get("target_en") or "",
            "question_ids": [],
            "code_evidence": item.get("code_evidence", ""),
            "answers": [],
            "verdicts": [],
            "statuses": [],
            "bases": [],
        })
        kp["question_ids"].append(item["qid"])
        verdict, basis, _, _ = per_q[item["qid"]]
        kp["verdicts"].append(verdict)
        kp["statuses"].append(_VERDICT_TO_KP.get(verdict, "insufficient"))
        kp["bases"].append(basis)
        answer = item.get("answer", "")
        kp["answers"].append(
            ",".join(answer) if isinstance(answer, list) else str(answer)
        )

    knowledge_points = []
    for kp in kp_map.values():
        if "uncovered" in kp["verdicts"]:
            status = "uncovered"
        else:
            worst = min(kp["verdicts"], key=lambda v: _VERDICT_ORDER.get(v, 0))
            status = _VERDICT_TO_KP.get(worst, "insufficient")
        student_evidence = "；".join(
            f"{qid}:{_short(ans)}" for qid, ans in zip(kp["question_ids"], kp["answers"])
        )
        knowledge_points.append({
            "point": kp["point"],
            "point_zh": kp["point_zh"],
            "point_en": kp["point_en"],
            "question_ids": kp["question_ids"],
            "code_evidence": kp["code_evidence"],
            "student_evidence": student_evidence,
            "status": status,
            "basis": "+".join(dict.fromkeys(kp["bases"])),
            "note": "",
        })

    # ---- 整体理解与内部诊断分 ----
    scored = [v for v, _, _, _ in per_q.values() if v != "uncovered"]
    counts = {k: sum(1 for v, _, _, _ in per_q.values() if v == k) for k in _VERDICT_ZH}
    # 待核验 / 启发式信号以 basis（判断来源）为准：空白回答的 verdict 是
    # insufficient，但其 basis=uncovered 同样必须触发人工复核
    uncovered_any = any(b == "uncovered" for _, b, _, _ in per_q.values())
    heuristic_any = any(b == "heuristic" for _, b, _, _ in per_q.values())
    sample_any = any(b == "sample-match" for _, b, _, _ in per_q.values())
    has_text = any(i.get("type") == "text" for i in items)

    if scored:
        ratio = sum(1.0 if v == "correct" else 0.5 if v == "partial" else 0.0
                    for v in scored) / len(scored)
    else:
        ratio = None
    if ratio is None:
        understanding = "insufficient"
    elif ratio >= 0.9:
        understanding = "mastered"
    elif ratio >= 0.6:
        understanding = "partial"
    elif ratio >= 0.3:
        understanding = "limited"
    else:
        understanding = "insufficient"

    needs_review = (
        ratio is not None and ratio < 0.6
    ) or uncovered_any or heuristic_any
    if uncovered_any and understanding == "mastered":
        understanding = "partial"  # 存在待核验时封顶 partial（docs §5）

    score = round(ratio * 10) if ratio is not None else 0

    # ---- 反馈（中英双语；具体、引用题号、不含修正代码）----
    total = len(items)
    zh_parts = [
        f"本次诊断共 {total} 题：答对 {counts['correct']} 题，部分正确 {counts['partial']} 题，"
        f"答错 {counts['wrong']} 题，证据不足 {counts['insufficient'] + counts['uncovered']} 题。"
    ]
    en_parts = [
        f"This diagnostic had {total} questions: {counts['correct']} correct, "
        f"{counts['partial']} partially correct, {counts['wrong']} wrong, "
        f"{counts['insufficient'] + counts['uncovered']} with insufficient evidence."
    ]
    weak_points, weak_points_en = [], []
    for kp in knowledge_points:
        if kp["status"] != "mastered":
            weak_points.append(f"{kp['point_zh']}（{_KP_STATUS_ZH[kp['status']]}）")
            weak_points_en.append(
                f"{kp['point_en'] or kp['point']} ({_KP_STATUS_EN[kp['status']]})"
            )
    if weak_points:
        zh_parts.append("尚待核验的知识点：" + "、".join(weak_points) + "。")
        en_parts.append("Points to revisit: " + ", ".join(weak_points_en) + ".")
    else:
        zh_parts.append("各知识点均已表现出充分理解，继续保持。")
        en_parts.append("All knowledge points show solid understanding. Keep it up!")
    zh_notes = [n for _, _, n, _ in per_q.values() if n]
    en_notes = [n for _, _, _, n in per_q.values() if n]
    if zh_notes:
        zh_parts.append("针对性提示：" + "；".join(dict.fromkeys(zh_notes)) + "。")
    if en_notes:
        en_parts.append("Targeted hints: " + "; ".join(dict.fromkeys(en_notes)) + ".")
    if uncovered_any:
        zh_parts.append(f"有 {counts['uncovered']} 个回答未覆盖在演示规则内，已标记待人工核验。")
        en_parts.append(
            f"{counts['uncovered']} answer(s) fall outside the demo rules and were "
            "flagged for manual review."
        )
    if heuristic_any:
        zh_parts.append("本次 text 题使用了启发式判分（演示规则未覆盖），建议助教复核。")
        en_parts.append(
            "Some text answers were graded by the heuristic fallback (not covered by "
            "the demo rules); TA review is recommended."
        )

    bases = []
    if sample_any:
        bases.append("sample-match")
    if any(b == "exact-choice" for _, b, _, _ in per_q.values()):
        bases.append("exact-choice")
    if heuristic_any:
        bases.append("heuristic")
    if uncovered_any:
        bases.append("uncovered")

    confidence = 0.9
    if has_text:
        confidence = 0.85
    if heuristic_any:
        confidence = 0.5
    if uncovered_any:
        confidence = 0.4

    return {
        "understanding": understanding,
        "score": score,
        "max_score": 10,
        "feedback": "".join(zh_parts),
        "feedback_en": "".join(en_parts),
        "remaining_issues": weak_points,
        "remaining_issues_en": weak_points_en,
        "needs_ta_review": needs_review,
        "confidence": confidence,
        "code_quality": case_meta.get("code_quality", "unknown"),
        "knowledge_points": knowledge_points,
        "basis": "+".join(bases) if bases else "exact-choice",
    }


def _short(s: str, limit: int = 80) -> str:
    s = str(s).strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"
