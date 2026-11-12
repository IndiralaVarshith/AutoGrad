"""Question paper structure checks: marks, sections, figures, duplicates. No Firestore or AI."""

import io

import pytest

from services.marks_validator import validate_and_reconcile_exam_structure
from services.rubric_cache import validate_rubric_structure
from services.paper_intake import apply_review_items


@pytest.mark.parametrize("bad_marks", [None, "", "two", True, -1, 0])
def test_validator_does_not_guess_unreadable_marks(bad_marks):
    good = {"question_number": 1, "question_text": "a", "max_marks": 2}
    bad = {"question_number": 2, "question_text": "b", "max_marks": bad_marks}
    structure = validate_and_reconcile_exam_structure({"questions": [good, bad]})

    assert structure.calculated_maximum_marks == 2.0
    assert structure.validation_status == "DISCREPANCY"
    assert any("Q2" in problem for problem in structure.marks_problems)


def test_validator_does_not_guess_when_marks_are_absent():
    structure = validate_and_reconcile_exam_structure({"sections": [{
        "section_id": "A",
        "questions": [{"question_number": 1, "question_text": "a"}],
    }]})
    assert structure.calculated_maximum_marks == 0.0
    assert structure.validation_status == "DISCREPANCY"


def test_validator_uses_printed_section_formula():
    structure = validate_and_reconcile_exam_structure({"sections": [{
        "section_id": "A",
        "attempt_instruction": "Answer the following: 2x4=8M",
        "questions": [
            {"question_number": 1, "question_text": "a"},
            {"question_number": 2, "question_text": "b"},
        ],
    }]})
    assert structure.calculated_maximum_marks == 8.0
    assert structure.marks_problems == []


def test_section_with_too_few_questions_is_flagged():
    """Real case: a scan prints 'any two, 2x4=8M' but shows only one question group."""
    structure = validate_and_reconcile_exam_structure({
        "declared_maximum_marks": 8,
        "sections": [{
            "section_id": "III",
            "attempt_instruction": "ANSWER ANY TWO OF THE FOLLOWING: 2x4=8M",
            "questions": [{"question_number": 9, "question_text": "a (OR) b", "marks": 4}],
        }],
    })
    assert structure.validation_status == "DISCREPANCY"
    assert any("requires 2 answers but only 1" in p for p in structure.marks_problems)


def test_section_with_or_alternatives_counts_each_group_once():
    questions = [
        {"question_number": n, "sub_part": part, "question_text": "t", "marks": 4,
         "choice_group": f"Q{n}", "is_or_choice": True}
        for n in (9, 10) for part in "AB"
    ]
    structure = validate_and_reconcile_exam_structure({
        "declared_maximum_marks": 8,
        "sections": [{
            "section_id": "III",
            "attempt_instruction": "ANSWER ANY TWO OF THE FOLLOWING: 2x4=8M",
            "questions": questions,
        }],
    })
    assert structure.marks_problems == []
    assert structure.calculated_maximum_marks == 8.0


def _png(image):
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _q(number, text, sub_part="", **fields):
    return {"question_number": number, "sub_part": sub_part, "question_text": text, **fields}


@pytest.mark.parametrize(
    "text",
    ["Observe the graph.", "Read the CHARTS below.", "Label the diagram.", "Use the map.",
     "Complete the table.", "See figure 3."],
)
def test_figure_words_are_detected(text):
    question = _q(1, text)
    assert len(apply_review_items([question])) == 1
    assert question["has_figure"] is True


@pytest.mark.parametrize("text", ["Explain paragraphs.", "Describe a stable market.", "Define Regression."])
def test_similar_words_are_not_figures(text):
    question = _q(1, text)
    assert apply_review_items([question]) == []
    assert question["has_figure"] is False


@pytest.mark.parametrize("page, expected", [(2, 2), ("2", 2), (0, None), (-1, None), ("x", None), (True, None), (None, None)])
def test_figure_page_is_only_kept_when_valid(page, expected):
    question = _q(1, "Plain words.", has_figure=True, figure_page=page)
    apply_review_items([question])
    assert question["figure_page"] == expected


def test_review_items_do_not_change_the_wording():
    text = "Observe the graph (Tamil Nadu 44.5)."
    question = _q(1, text)
    apply_review_items([question])
    assert question["question_text"] == text


def test_sub_parts_are_not_reported_as_duplicates():
    rubric = {"questions": [
        {"question_number": 9, "sub_part": "A", "max_marks": 4, "criteria": [{"description": "c", "max_marks": 4}]},
        {"question_number": 9, "sub_part": "B", "max_marks": 4, "criteria": [{"description": "c", "max_marks": 4}]},
    ]}
    assert validate_rubric_structure(rubric) == []


def test_real_duplicates_are_still_reported():
    rubric = {"questions": [
        {"question_number": 9, "max_marks": 4, "criteria": [{"description": "c", "max_marks": 4}]},
        {"question_number": 9, "max_marks": 4, "criteria": [{"description": "c", "max_marks": 4}]},
    ]}
    assert validate_rubric_structure(rubric) == ["Duplicate question number: 9."]
