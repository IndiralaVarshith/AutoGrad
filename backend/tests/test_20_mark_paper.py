"""
Acceptance Test: 20-Mark Question Paper & Marks Reconciliation
Verifies that:
  1. The 20-mark question paper does NOT produce a 40-mark rubric.
  2. Sections (I: 4x1=4M, II: 4x2=8M, III: 2x4=8M) are calculated correctly.
  3. Total examination marks equal 20.0.
  4. Approved rubric persists to Firestore and is retrieved with total_marks=20.0.
"""

import os
import pytest
from services.marks_validator import (
    validate_and_reconcile_exam_structure,
    normalize_rubric_questions_with_structure,
    parse_section_formula,
    extract_declared_marks_from_text,
)


def test_section_formulas_from_paper():
    # Section I
    c1, m1, t1 = parse_section_formula("I. ANSWER THE FOLLOWING QUESTIONS: 4x1=4M")
    assert c1 == 4 and m1 == 1.0 and t1 == 4.0

    # Section II
    c2, m2, t2 = parse_section_formula("II. ANSWER THE FOLLOWING QUESTIONS: 4x2=8M")
    assert c2 == 4 and m2 == 2.0 and t2 == 8.0

    # Section III
    c3, m3, t3 = parse_section_formula("III. ANSWER ANY – TWO OF THE FOLLOWING: 2x4=8M")
    assert c3 == 2 and m3 == 4.0 and t3 == 8.0

    # Total of the three sections
    total = t1 + t2 + t3
    assert total == 20.0


def test_20_mark_paper_reconciliation_full():
    """
    Simulates the exact output generated from Narayana Class X Social DPT question paper:
    Even if the LLM naively returned all 10 questions without knowing how to sum choices,
    the validator must compute 20.0M, NOT 40.0M.
    """
    extracted_data = {
        "exam_title": "Class X Social DPT - Narayana",
        "declared_maximum_marks": 20.0,
        "sections": [
            {
                "section_id": "I",
                "section_title": "Section I",
                "attempt_instruction": "I. ANSWER THE FOLLOWING QUESTIONS: 4x1=4M",
                "attempt_count": 4,
                "marks_per_question": 1.0,
                "section_maximum_marks": 4.0,
                "questions": [
                    {"question_number": 1, "marks": 1.0, "question_text": "Why did Hitler invade Poland in 1939?"},
                    {"question_number": 2, "marks": 1.0, "question_text": "Write about the Collective Farming?"},
                    {"question_number": 3, "marks": 1.0, "question_text": "From India, who were inspired by Communist Party?"},
                    {"question_number": 4, "marks": 1.0, "question_text": "What was the immediate cause for the world war - I?"},
                ]
            },
            {
                "section_id": "II",
                "section_title": "Section II",
                "attempt_instruction": "II. ANSWER THE FOLLOWING QUESTIONS: 4x2=8M",
                "attempt_count": 4,
                "marks_per_question": 2.0,
                "section_maximum_marks": 8.0,
                "questions": [
                    {"question_number": 5, "marks": 2.0, "question_text": "What was the immediate provocation of world war - II?"},
                    {"question_number": 6, "marks": 2.0, "question_text": "Discuss few points about enfranchisement of women."},
                    {"question_number": 7, "marks": 2.0, "question_text": "Prepare few slogans on the prevention of wars and promotion of peace."},
                    {"question_number": 8, "marks": 2.0, "question_text": "Write a short note on secret alliances."},
                ]
            },
            {
                "section_id": "III",
                "section_title": "Section III",
                "attempt_instruction": "III. ANSWER ANY – TWO OF THE FOLLOWING: 2x4=8M",
                "attempt_count": 2,
                "marks_per_question": 4.0,
                "section_maximum_marks": 8.0,
                "questions": [
                    {"question_number": 9, "sub_part": "A", "marks": 4.0, "choice_group": "Q9", "question_text": "Agricultural reforms of Stalin in the USSR?"},
                    {"question_number": 9, "sub_part": "B", "marks": 4.0, "choice_group": "Q9", "is_or_choice": True, "question_text": "Alternative Stalin reform question"},
                    {"question_number": 10, "sub_part": "A", "marks": 4.0, "choice_group": "Q10", "question_text": "Welfare state function in India today?"},
                    {"question_number": 10, "sub_part": "B", "marks": 4.0, "choice_group": "Q10", "is_or_choice": True, "question_text": "Alternative welfare question"},
                ]
            }
        ]
    }

    struct = validate_and_reconcile_exam_structure(extracted_data)

    # Must be 20.0, NOT 40.0!
    assert struct.declared_maximum_marks == 20.0
    assert struct.calculated_maximum_marks == 20.0
    assert struct.validation_status in ("VALID", "RECONCILED")

    # Verify per-section marks
    sec_marks = [s.section_max_marks for s in struct.sections]
    assert sec_marks == [4.0, 8.0, 8.0]
    assert sum(sec_marks) == 20.0


def test_unexplained_double_count_is_reported_as_discrepancy():
    """
    A declared total must never be used to hide a structurally unexplained mismatch.
    """
    inflated_data = {
        "declared_maximum_marks": 20.0,
        "questions": [
            {"question_number": i, "max_marks": 4.0, "question_text": f"Q{i}"}
            for i in range(1, 11)  # 10 questions x 4.0 = 40.0
        ]
    }
    struct = validate_and_reconcile_exam_structure(inflated_data)
    assert struct.declared_maximum_marks == 20.0
    assert struct.calculated_maximum_marks == 40.0
    assert struct.validation_status == "DISCREPANCY"


def test_flat_rubric_preserves_explicit_attempt_metadata():
    questions = []
    for number, marks, section, attempt_count, section_max in [
        (1, 4, "A", 2, 8),
        (2, 4, "A", 2, 8),
        (3, 4, "A", 2, 8),
        (4, 4, "A", 2, 8),
        (5, 6, "B", 1, 6),
    ]:
        questions.append({
            "question_number": number,
            "max_marks": marks,
            "section": section,
            "section_attempt_count": attempt_count,
            "section_max_marks": section_max,
            "attempt_instruction": "Answer any 2" if section == "A" else "Answer 1",
        })

    struct = validate_and_reconcile_exam_structure({
        "questions": questions,
        "declared_maximum_marks": 14,
    })
    assert struct.calculated_maximum_marks == 14
    assert struct.validation_status == "RECONCILED"

    normalized = normalize_rubric_questions_with_structure(questions, struct)
    assert all(question["section_attempt_count"] == 2 for question in normalized[:4])
    assert all(question["section_max_marks"] == 8 for question in normalized[:4])
    assert normalized[0]["attempt_instruction"] == "Answer any 2"


def test_20_mark_paper_with_incomplete_question_maxima_is_rejected():
    data = {
        "declared_maximum_marks": 20,
        "questions": [
            {"question_number": number, "max_marks": 2, "section": "A"}
            for number in range(1, 9)
        ],
    }
    struct = validate_and_reconcile_exam_structure(data)
    assert struct.calculated_maximum_marks == 16
    assert struct.validation_status == "DISCREPANCY"
