"""
Unit Tests for Examination Marks Validation & Structured Representation Engine
Covers Patterns A through G as specified in requirements.
"""

import pytest
from services.marks_validator import (
    parse_section_formula,
    extract_declared_marks_from_text,
    validate_and_reconcile_exam_structure,
    calculate_section_marks,
    normalize_rubric_questions_with_structure,
    parse_question_number_and_part,
    safe_int_qnum,
    SectionStructure,
    QuestionItem,
)


def test_alphanumeric_question_numbers_never_crash():
    # Never raise ValueError on alphanumeric numbers like '9A', '9B', '10A'
    assert parse_question_number_and_part("9A") == (9, "A")
    assert parse_question_number_and_part("9B") == (9, "B")
    assert parse_question_number_and_part("10A") == (10, "A")
    assert parse_question_number_and_part("9(a)") == (9, "A")
    assert parse_question_number_and_part("Q9") == (9, "")
    assert parse_question_number_and_part(9) == (9, "")
    assert safe_int_qnum("9A") == 9
    assert safe_int_qnum("10B") == 10
    assert safe_int_qnum(5) == 5


def test_parse_section_formula():
    # 4x1=4M
    count, marks, total = parse_section_formula("I. ANSWER THE FOLLOWING QUESTIONS: 4x1=4M")
    assert count == 4
    assert marks == 1.0
    assert total == 4.0

    # 4x2=8M
    count, marks, total = parse_section_formula("II. ANSWER THE FOLLOWING QUESTIONS: 4x2=8M")
    assert count == 4
    assert marks == 2.0
    assert total == 8.0

    # 2x4=8M
    count, marks, total = parse_section_formula("III. ANSWER ANY – TWO OF THE FOLLOWING: 2x4=8M")
    assert count == 2
    assert marks == 4.0
    assert total == 8.0

    # Words: Answer any two
    count, _, _ = parse_section_formula("Answer any two questions")
    assert count == 2


def test_extract_declared_marks_from_text():
    assert extract_declared_marks_from_text("MAX MARKS: 20") == 20.0
    assert extract_declared_marks_from_text("Subject: Social, Marks: 20, Time: 1hr") == 20.0
    assert extract_declared_marks_from_text("Total Marks = 100") == 100.0
    assert extract_declared_marks_from_text("M.M.: 50") == 50.0
    assert extract_declared_marks_from_text("Random header with no marks") is None


def test_pattern_a_equal_marks():
    # 10 questions x 2 marks = 20
    raw_data = {
        "declared_maximum_marks": 20.0,
        "questions": [
            {"question_number": i, "max_marks": 2.0, "question_text": f"Q{i}"}
            for i in range(1, 11)
        ]
    }
    struct = validate_and_reconcile_exam_structure(raw_data)
    assert struct.calculated_maximum_marks == 20.0
    assert struct.validation_status == "VALID"


def test_pattern_b_different_marks():
    # Q1=2, Q2=3, Q3=5, Q4=10 = 20
    raw_data = {
        "declared_maximum_marks": 20.0,
        "questions": [
            {"question_number": 1, "max_marks": 2.0},
            {"question_number": 2, "max_marks": 3.0},
            {"question_number": 3, "max_marks": 5.0},
            {"question_number": 4, "max_marks": 10.0},
        ]
    }
    struct = validate_and_reconcile_exam_structure(raw_data)
    assert struct.calculated_maximum_marks == 20.0
    assert struct.validation_status == "VALID"


def test_pattern_c_sections():
    # Section A = 10, Section B = 10 = 20
    raw_data = {
        "declared_maximum_marks": 20.0,
        "sections": [
            {
                "section_id": "A",
                "section_title": "Section A",
                "attempt_instruction": "5x2=10M",
                "questions": [{"question_number": i, "marks": 2.0} for i in range(1, 6)]
            },
            {
                "section_id": "B",
                "section_title": "Section B",
                "attempt_instruction": "2x5=10M",
                "questions": [{"question_number": i, "marks": 5.0} for i in range(6, 8)]
            }
        ]
    }
    struct = validate_and_reconcile_exam_structure(raw_data)
    assert struct.calculated_maximum_marks == 20.0
    assert struct.validation_status == "VALID"


def test_pattern_d_internal_choices():
    # Q1(a) OR Q1(b), both 5 marks. Contribution is 5 marks, NOT 10 marks!
    sec = SectionStructure(
        section_id="A",
        questions=[
            QuestionItem(question_number=1, sub_part="a", marks=5.0, choice_group="Q1"),
            QuestionItem(question_number=1, sub_part="b", marks=5.0, choice_group="Q1"),
            QuestionItem(question_number=2, marks=5.0),
        ]
    )
    total, notes = calculate_section_marks(sec)
    # Q1 group contributes max(5, 5)=5; Q2 contributes 5 -> Total = 10 (not 15!)
    assert total == 10.0
    assert any("Choice group Q1" in n for n in notes)


def test_pattern_f_attempt_any_n():
    # 8 questions of 5 marks each. Student answers any 4.
    # Total marks = 20, NOT 40!
    sec = SectionStructure(
        section_id="I",
        attempt_instruction="Answer any four of the following",
        attempt_count=4,
        questions=[QuestionItem(question_number=i, marks=5.0) for i in range(1, 9)]
    )
    total, notes = calculate_section_marks(sec)
    assert total == 20.0
    assert any("Attempt rule 'Answer any 4 of 8'" in n for n in notes)


def test_actual_20_mark_paper_reconciliation():
    """
    Test the EXACT question paper from user (Narayana Class X Social DPT):
    - Declared: 20 Marks
    - Section I: 4x1=4M (Q1-Q4 @ 1M = 4M)
    - Section II: 4x2=8M (Q5-Q8 @ 2M = 8M)
    - Section III: 2x4=8M (Q9A OR Q9B @ 4M, Q10A OR Q10B @ 4M = 8M)
    Total = 20.0 Marks (NOT 40.0 Marks).
    """
    raw_data = {
        "exam_title": "Class X Social DPT",
        "declared_maximum_marks": 20.0,
        "sections": [
            {
                "section_id": "I",
                "attempt_instruction": "I. ANSWER THE FOLLOWING QUESTIONS: 4x1=4M",
                "questions": [
                    {"question_number": 1, "marks": 1.0, "question_text": "Why did Hitler invade Poland?"},
                    {"question_number": 2, "marks": 1.0, "question_text": "Write about Collective Farming?"},
                    {"question_number": 3, "marks": 1.0, "question_text": "From India who inspired?"},
                    {"question_number": 4, "marks": 1.0, "question_text": "Immediate cause WWI?"},
                ]
            },
            {
                "section_id": "II",
                "attempt_instruction": "II. ANSWER THE FOLLOWING QUESTIONS: 4x2=8M",
                "questions": [
                    {"question_number": 5, "marks": 2.0, "question_text": "Immediate provocation WWII?"},
                    {"question_number": 6, "marks": 2.0, "question_text": "Enfranchisement of women?"},
                    {"question_number": 7, "marks": 2.0, "question_text": "Prepare slogans on peace?"},
                    {"question_number": 8, "marks": 2.0, "question_text": "Secret alliances note?"},
                ]
            },
            {
                "section_id": "III",
                "attempt_instruction": "III. ANSWER ANY – TWO OF THE FOLLOWING: 2x4=8M",
                "attempt_count": 2,
                "questions": [
                    {"question_number": 9, "sub_part": "A", "marks": 4.0, "choice_group": "Q9", "question_text": "Stalin reforms?"},
                    {"question_number": 9, "sub_part": "B", "marks": 4.0, "choice_group": "Q9", "is_or_choice": True, "question_text": "Stalin reforms alternative?"},
                    {"question_number": 10, "sub_part": "A", "marks": 4.0, "choice_group": "Q10", "question_text": "Welfare state in India?"},
                    {"question_number": 10, "sub_part": "B", "marks": 4.0, "choice_group": "Q10", "is_or_choice": True, "question_text": "Welfare state alternative?"},
                ]
            }
        ]
    }
    struct = validate_and_reconcile_exam_structure(raw_data)
    assert struct.declared_maximum_marks == 20.0
    # Crucial assertion: Must be 20.0, NOT 40.0!
    assert struct.calculated_maximum_marks == 20.0
    assert struct.validation_status in ("VALID", "RECONCILED")

    # Now verify normalized rubric questions
    rubric_questions = normalize_rubric_questions_with_structure(
        [
            {"question_number": 1, "criteria": [{"description": "Fact", "max_marks": 1.0}]},
            {"question_number": 5, "criteria": [{"description": "Point 1", "max_marks": 1.0}, {"description": "Point 2", "max_marks": 1.0}]},
            {"question_number": 9, "sub_part": "A", "criteria": [{"description": "Detail", "max_marks": 4.0}]},
        ],
        struct
    )
    assert rubric_questions[0]["max_marks"] == 1.0
    assert rubric_questions[1]["max_marks"] == 2.0
    assert rubric_questions[2]["max_marks"] == 4.0

