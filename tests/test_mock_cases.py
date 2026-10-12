"""Mock 案例库验收：18 份代码案例 × 4 组预置作答 = 72 组基础样例（审批 WP2）。

- 每组预置作答的评价结果必须与案例中审校的 expect 一致（理解档位 + 是否需复核）；
- 代码质量与理解程度正交（code_quality 来自案例，understanding 来自作答证据）；
- 诊断按代码内容匹配：同案例换学号不变、同学号换代码按新代码匹配；
- 未覆盖代码 → 通用问卷 + 待人工核验；关键词重合但断言相反 → 待核验而非判对。
"""
from __future__ import annotations

import re

import pytest

from app import cases, storage
from app.agent import generate_questionnaire
from app.evaluator import evaluate
from app.llm import MockLLMProvider
from app.models import ResponseFile, Submission, now_utc

VALID_KP_STATUS = {"mastered", "partial", "limited", "insufficient", "uncovered"}


def _submission(code: str, sid: str) -> Submission:
    return Submission(
        student_id=sid,
        assignment_id="c-find-max",
        code=code,
        language="c",
        submitted_at="2026-09-12T00:00:00",
    )


def _evaluate_profile(provider, assignment, case, sid, profile):
    submission = _submission(case.code, sid)
    questionnaire = generate_questionnaire(provider, assignment, submission)
    assert questionnaire.case_id == case.case_id
    answers = questionnaire.normalize_answers(profile.answers)
    response = ResponseFile(
        student_id=sid, submitted_at=now_utc(), answers=answers, attempt_count=1
    )
    return evaluate(provider, assignment, submission, questionnaire, response)


# ---------------- 库完整性 ----------------

def test_case_library_shape():
    all_cases = cases.list_cases()
    assert [c.case_id for c in all_cases] == [f"C{i:02d}" for i in range(1, 19)]
    assert sum(len(c.profiles) for c in all_cases) == 72
    for case in all_cases:
        assert 3 <= len(case.questions) <= 5, case.case_id
        assert case.code_quality in ("correct", "incorrect")
        assert case.review_status == "reviewed"
        assert len(case.profiles) == 4
        text_questions = [q for q in case.questions if q.type == "text"]
        assert text_questions, f"{case.case_id} 应至少包含 1 道 text 题"
        sample_verdicts = set()
        for q in text_questions:
            for sample in q.samples:
                assert sample.variants, f"{case.case_id}/{q.id} 样例缺少变体"
                sample_verdicts.add(sample.verdict)
        assert {"correct", "partial", "wrong", "insufficient"} <= sample_verdicts, (
            f"{case.case_id} 的 text 样例应覆盖四类 verdict"
        )


def test_profile_answers_are_declared_variants():
    """自洽性：每个档案的 text 回答必须是该题已声明的样例变体。"""
    for case in cases.list_cases():
        by_id = {q.id: q for q in case.questions}
        for profile in case.profiles:
            for qid, answer in profile.answers.items():
                q = by_id[qid]
                if q.type != "text":
                    continue
                normalized = {v for s in q.samples for v in s.variants}
                assert answer in normalized, (
                    f"{case.case_id}/{profile.profile_id}/{qid} 的回答不在样例变体中"
                )


# ---------------- 72 组预置作答应与审校 expect 一致 ----------------

def test_all_72_profiles_produce_expected_evaluation():
    provider = MockLLMProvider()
    assignment = storage.load_assignment()
    checked = 0
    for case in cases.list_cases():
        sid = f"90{int(case.case_id[1:]):05d}"  # 9000001..9000018 → 7 位数字
        for profile in case.profiles:
            evaluation = _evaluate_profile(provider, assignment, case, sid, profile)
            expect = profile.expect
            assert evaluation.understanding.value == expect["understanding"], (
                f"{case.case_id}/{profile.profile_id}: "
                f"{evaluation.understanding.value} != {expect['understanding']}"
            )
            assert evaluation.needs_ta_review == expect["needs_ta_review"], (
                f"{case.case_id}/{profile.profile_id} needs_ta_review 不一致"
            )
            # 两个正交维度
            assert evaluation.code_quality == case.code_quality
            # 逐知识点证据结构完整
            assert evaluation.knowledge_points, f"{case.case_id} 缺少知识点结论"
            for kp in evaluation.knowledge_points:
                assert kp.status in VALID_KP_STATUS
                assert kp.question_ids
                assert kp.basis
            checked += 1
    assert checked == 72


def test_feedback_counters_match_verdicts():
    """回归：反馈中的题数统计必须与逐题判定一致（曾因元组比较恒假全部显示 0）。"""
    provider = MockLLMProvider()
    assignment = storage.load_assignment()
    case = next(c for c in cases.list_cases() if c.case_id == "C01")

    mastered = next(p for p in case.profiles if p.profile_id == "mastered")
    evaluation = _evaluate_profile(provider, assignment, case, "90202601", mastered)
    assert "共 5 题：答对 5 题，部分正确 0 题，答错 0 题，证据不足 0 题" in evaluation.feedback

    contradictory = next(p for p in case.profiles if p.profile_id == "contradictory")
    evaluation = _evaluate_profile(provider, assignment, case, "90202602", contradictory)
    assert "共 5 题：答对 2 题，部分正确 0 题，答错 3 题" in evaluation.feedback

    # 结构不变量：四类计数之和恒等于题量（遍历全部 72 组，防再退化）
    for c in cases.list_cases():
        for profile in c.profiles:
            ev = _evaluate_profile(provider, assignment, c, "90302601", profile)
            m = re.search(r"共 (\d+) 题：答对 (\d+) 题，部分正确 (\d+) 题，答错 (\d+) 题，证据不足 (\d+) 题", ev.feedback)
            assert m, f"{c.case_id}/{profile.profile_id}: 反馈缺少题数统计: {ev.feedback[:60]}"
            assert sum(int(g) for g in m.groups()[1:]) == int(m.group(1)), (
                f"{c.case_id}/{profile.profile_id}: 统计之和 != 总题数"
            )


def test_code_quality_and_understanding_are_orthogonal():
    """审批 §1 四象限：代码正确但解释矛盾；代码有错但分析准确。"""
    provider = MockLLMProvider()
    assignment = storage.load_assignment()
    c01 = cases.load_case("C01")  # 代码正确
    c02 = cases.load_case("C02")  # 代码错误

    c01_profiles = {p.profile_id: p for p in c01.profiles}
    c02_profiles = {p.profile_id: p for p in c02.profiles}

    ev = _evaluate_profile(provider, assignment, c01, "99000001", c01_profiles["contradictory"])
    assert ev.code_quality == "correct" and ev.understanding.value == "limited"

    ev = _evaluate_profile(provider, assignment, c02, "99000002", c02_profiles["mastered"])
    assert ev.code_quality == "incorrect" and ev.understanding.value == "mastered"


# ---------------- 诊断按代码匹配（P1 修复） ----------------

def test_same_case_different_student_id_same_diagnosis():
    provider = MockLLMProvider()
    assignment = storage.load_assignment()
    c03 = cases.load_case("C03")
    q_a = generate_questionnaire(provider, assignment, _submission(c03.code, "98000001"))
    q_b = generate_questionnaire(provider, assignment, _submission(c03.code, "98000002"))
    assert q_a.case_id == q_b.case_id == "C03"
    assert q_a.diagnosis.summary == q_b.diagnosis.summary
    assert [q.question for q in q_a.questions] == [q.question for q in q_b.questions]


def test_same_student_id_different_code_matches_new_case():
    provider = MockLLMProvider()
    assignment = storage.load_assignment()
    sid = "98000003"
    q1 = generate_questionnaire(
        provider, assignment, _submission(cases.load_case("C02").code, sid)
    )
    q2 = generate_questionnaire(
        provider, assignment, _submission(cases.load_case("C03").code, sid)
    )
    assert q1.case_id == "C02" and q2.case_id == "C03"
    assert q1.diagnosis.summary != q2.diagnosis.summary


def test_unknown_code_gets_generic_questionnaire_with_manual_review_flag():
    provider = MockLLMProvider()
    assignment = storage.load_assignment()
    unknown_code = (
        "int find_max(int arr[], int n) {\n"
        "    int max = arr[0];\n"
        "    for (int i = 1; i < n; i++) {\n"
        "        if (arr[i] > max) {\n"
        "            max = arr[i] + 1;\n"   # 未收录的变体
        "        }\n"
        "    }\n"
        "    return max;\n"
        "}"
    )
    q = generate_questionnaire(provider, assignment, _submission(unknown_code, "97000001"))
    assert q.case_id == ""
    assert q.case_match == "none"
    assert "待人工核验" in q.diagnosis.summary
    assert 3 <= len(q.questions) <= 5


# ---------------- 未覆盖回答 → 待核验，而不是按字词重合判对 ----------------

def test_keyword_overlap_with_opposite_claim_is_not_counted_correct():
    provider = MockLLMProvider()
    assignment = storage.load_assignment()
    c03 = cases.load_case("C03")
    questionnaire = generate_questionnaire(
        provider, assignment, _submission(c03.code, "96000001")
    )
    answers = questionnaire.normalize_answers({
        "q1": "B", "q2": "B", "q3": "B",
        # 包含大量参考答案关键词，但断言完全相反：
        "q4": "最后一次合法访问的下标不是 n-1 而是 n，i == n 时继续循环访问 arr[n] 也是合法的，不需要停止",
    })
    response = ResponseFile(
        student_id="96000001", submitted_at=now_utc(), answers=answers, attempt_count=1
    )
    evaluation = evaluate(
        provider, assignment, _submission(c03.code, "96000001"), questionnaire, response
    )
    assert evaluation.understanding.value != "mastered"
    assert evaluation.needs_ta_review is True
    kp = {k.point: k for k in evaluation.knowledge_points}["loop-boundary"]
    assert kp.status == "uncovered"
    assert "待人工核验" in evaluation.feedback


def test_blank_text_answer_is_insufficient_evidence():
    provider = MockLLMProvider()
    assignment = storage.load_assignment()
    c03 = cases.load_case("C03")
    questionnaire = generate_questionnaire(
        provider, assignment, _submission(c03.code, "96000002")
    )
    answers = questionnaire.normalize_answers({"q1": "B", "q2": "B", "q3": "B", "q4": ""})
    response = ResponseFile(
        student_id="96000002", submitted_at=now_utc(), answers=answers, attempt_count=1
    )
    evaluation = evaluate(
        provider, assignment, _submission(c03.code, "96000002"), questionnaire, response
    )
    assert evaluation.needs_ta_review is True
    assert evaluation.understanding.value == "partial"  # 选择题对但 text 无证据


def test_paraphrase_variants_match():
    """同义表达：与样例措辞不同但语义相同的变体应命中样例。"""
    provider = MockLLMProvider()
    assignment = storage.load_assignment()
    c03 = cases.load_case("C03")
    questionnaire = generate_questionnaire(
        provider, assignment, _submission(c03.code, "96000003")
    )
    answers = questionnaire.normalize_answers({
        "q1": "B", "q2": "B", "q3": "B",
        "q4": "合法下标最大是 n-1，i 到 n 就必须停止，再往下就是越界。",
        "q5": "C",
    })
    response = ResponseFile(
        student_id="96000003", submitted_at=now_utc(), answers=answers, attempt_count=1
    )
    evaluation = evaluate(
        provider, assignment, _submission(c03.code, "96000003"), questionnaire, response
    )
    assert evaluation.understanding.value == "mastered"
    assert evaluation.needs_ta_review is False


def test_six_demo_students_still_match_baseline_cases():
    """原有 6 份提交与 C01~C06 一一对应（回归基线）。"""
    provider = MockLLMProvider()
    assignment = storage.load_assignment()
    expected = {
        "20260001": "C01", "20260002": "C02", "20260003": "C03",
        "20260004": "C04", "20260005": "C05", "20260006": "C06",
    }
    for submission in storage.list_submissions():
        q = generate_questionnaire(provider, assignment, submission)
        assert q.case_id == expected[submission.student_id], submission.student_id
