"""Question paper parsing against the dataset's paper and answer key. No Firestore or AI."""

import csv
import io
from pathlib import Path

import fitz
import pytest
from PIL import Image, ImageFilter

from services import paper_intake
from services.marks_validator import validate_and_reconcile_exam_structure
from services.rubric_cache import validate_rubric_structure
from services.paper_intake import (
    MIN_SHARPNESS,
    apply_review_items,
    check_image_quality,
    parse_question_paper_text,
    questions_from_model_reply,
    sharpness,
)

DATASET = Path(__file__).resolve().parents[2] / "test_data" / "mendeley"
SCAN = Path(__file__).resolve().parents[2] / "test_data" / "question_paper.pdf"


@pytest.fixture(scope="module")
def paper_text():
    path = DATASET / "Question.txt"
    if not path.is_file():
        pytest.fail(f"Dataset paper not found at {path}. test_data/mendeley is gitignored.")
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def answer_key():
    with (DATASET / "answerkey.txt").open(encoding="utf-8") as handle:
        return {int(row["Question_Number"]): row for row in csv.DictReader(handle)}


def test_dataset_paper_questions_and_marks(paper_text):
    parsed = parse_question_paper_text(paper_text)

    assert parsed.problems == []
    assert len(parsed.questions) == 35
    assert [q["question_number"] for q in parsed.questions] == list(range(1, 36))

    part_one = [q for q in parsed.questions if q["section"] == "I"]
    part_two = [q for q in parsed.questions if q["section"] == "II"]
    assert len(part_one) == 20 and all(q["max_marks"] == 1.0 for q in part_one)
    assert len(part_two) == 15 and all(q["max_marks"] == 2.0 for q in part_two)

    structure = validate_and_reconcile_exam_structure(parsed.raw)
    assert structure.calculated_maximum_marks == 50.0
    assert structure.validation_status == "VALID"
    assert structure.marks_problems == []


def test_dataset_paper_matches_answer_key(paper_text, answer_key):
    parsed = parse_question_paper_text(paper_text)
    assert {q["question_number"] for q in parsed.questions} == set(answer_key)

    for q in parsed.questions:
        row = answer_key[q["question_number"]]
        if row["Type"] == "MCQ":
            assert q["question_format"] == "MCQ"
            labels = [option["label"] for option in q["options"]]
            assert row["Correct_Answer"] in labels, q["question_number"]
        else:
            assert q["question_format"] == "SHORT_ANSWER"
            assert q["options"] == []

    true_false = next(q for q in parsed.questions if q["question_number"] == 17)
    assert [option["label"] for option in true_false["options"]] == ["A", "B"]


def test_missing_marks_header_is_flagged_and_gives_no_marks(paper_text):
    changed = paper_text.replace("(2 Marks Each)", "")
    parsed = parse_question_paper_text(changed)

    assert any("Part II" in problem for problem in parsed.problems)
    assert all(q["max_marks"] == 0.0 for q in parsed.questions if q["section"] == "II")

    structure = validate_and_reconcile_exam_structure(parsed.raw)
    assert structure.validation_status == "DISCREPANCY"
    assert structure.calculated_maximum_marks == 20.0
    assert len(structure.marks_problems) == 15


def test_questions_outside_a_part_are_flagged():
    parsed = parse_question_paper_text("1. What is data?\n2. What is a model?\n")
    assert len(parsed.questions) == 2
    assert any("outside any part" in problem for problem in parsed.problems)
    assert validate_and_reconcile_exam_structure(parsed.raw).validation_status == "DISCREPANCY"


def test_numbering_gap_and_duplicate_are_flagged(paper_text):
    gap = parse_question_paper_text(paper_text.replace("23. What is Regression Analysis?\n", ""))
    assert any("[23]" in problem for problem in gap.problems)

    duplicate = parse_question_paper_text(paper_text.replace("23. What is", "22. What is"))
    assert any("Q22" in problem for problem in duplicate.problems)


def test_mcq_part_without_options_is_flagged():
    parsed = parse_question_paper_text(
        "Part I: Multiple Choice Questions (1 Mark Each)\n1. Pick one.\n"
    )
    assert any("without options" in problem for problem in parsed.problems)


def test_printed_total_that_disagrees_is_a_discrepancy(paper_text):
    parsed = parse_question_paper_text("Maximum Marks: 60\n" + paper_text)
    assert parsed.raw["declared_maximum_marks"] == 60.0
    assert validate_and_reconcile_exam_structure(parsed.raw).validation_status == "DISCREPANCY"


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


def test_model_reply_with_only_sections_is_read():
    questions, problems = questions_from_model_reply({"sections": [{
        "section_id": "A",
        "questions": [{"question_number": 1, "question_text": "a", "marks": 1}],
    }]})
    assert [q["section"] for q in questions] == ["A"]
    assert problems == []


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


def test_merged_alternatives_in_a_model_reply_are_flagged():
    _, problems = questions_from_model_reply({"questions": [
        {"question_number": 9, "question_text": "First? (OR) Second?"},
    ]})
    assert any("merged" in problem for problem in problems)


def test_model_reply_with_conflicting_lists_is_flagged():
    _, problems = questions_from_model_reply({
        "sections": [{"section_id": "A", "questions": [{"question_number": 1, "question_text": "a"}]}],
        "questions": [{"question_number": 2, "question_text": "b"}],
    })
    assert any("two different" in problem for problem in problems)


def test_sharpness_separates_a_sharp_page_from_a_blurred_one():
    page = Image.new("L", (800, 1000), 255)
    for y in range(50, 950, 20):
        page.paste(0, (50, y, 750, y + 4))
    blurred = page.filter(ImageFilter.GaussianBlur(10))
    assert sharpness(page) > MIN_SHARPNESS > sharpness(blurred)
    assert sharpness(Image.open(io.BytesIO(_png(Image.new("L", (800, 1000), 255))))) == 0


def _png(image):
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _scan_page():
    with fitz.open(SCAN) as document:
        pixmap = document[0].get_pixmap(dpi=150)
    return Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")


@pytest.mark.parametrize("radius, readable", [(0, True), (3, True), (4, False), (8, False)])
def test_quality_check_boundary_on_a_real_scan(radius, readable):
    page = _scan_page()
    if radius:
        page = page.filter(ImageFilter.GaussianBlur(radius))
    assert (check_image_quality([_png(page)]) == []) is readable


def test_quality_check_reports_blur_even_when_text_is_detected(monkeypatch):
    monkeypatch.setattr(paper_intake, "MIN_SHARPNESS", 1e9)
    problems = check_image_quality([_png(_scan_page())])
    assert problems == ["Page 1: the image is too blurry."]


def test_blank_pages_are_ignored_unless_every_page_is_blank():
    blank = _png(Image.new("RGB", (1400, 2000), "white"))
    assert check_image_quality([blank, blank]) == ["Every page looks blank."]
    assert check_image_quality([_png(_scan_page()), blank]) == []


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
