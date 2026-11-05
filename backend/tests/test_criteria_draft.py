"""A1: criteria drafted from an answer key, edited and approved on the emulator."""

from __future__ import annotations

from pathlib import Path

import pytest

from services.criteria_draft import AnswerKeyError, draft_criteria, parse_answer_key
from tests.test_question_paper_emulator import (  # noqa: F401
    EXAM,
    clean_state,
    client,
    firestore_client,
    upload,
)

DATA = Path(__file__).resolve().parents[2] / "test_data" / "mendeley"

PAPER = b"""Science Exam
Part I: Multiple Choice Questions (1 Mark Each)
1. First?
A. one
B. two
2. Second?
A. one
B. two
Part II: Short Answer Questions (2 Marks Each)
3. Define it.
4. Explain it.
"""

KEY = b"""Question_Number,Type,Correct_Answer
1,MCQ,B
2,MCQ,A
3,Short_Answer,"Overfitting; the model failed to generalize."
4,Short_Answer,"A single clear point about it."
"""


def draft(client, key=KEY):
    return client.post(
        f"/exam/{EXAM}/rubric/draft-criteria", files={"answer_key": ("key.txt", key)}
    )


def approve(client, questions, confirmed=True):
    return client.post(
        "/exam/rubric/approve",
        json={"exam_id": EXAM, "teacher_confirmed": confirmed, "rubric": questions},
    )


def test_draft_splits_points_and_keeps_marks():
    questions = [
        {"question_number": 1, "max_marks": 2.0, "question_format": "SHORT_ANSWER"},
        {"question_number": 2, "max_marks": 2.0, "question_format": "SHORT_ANSWER"},
    ]
    drafted, problems = draft_criteria(questions, {1: "Alpha; beta.", 2: "Only one point, e.g., this."})
    assert problems == []
    assert [c["max_marks"] for c in drafted[0]["criteria"]] == [1.0, 1.0]
    assert [c["max_marks"] for c in drafted[1]["criteria"]] == [2.0]


def test_uneven_points_stay_one_criterion():
    drafted, _ = draft_criteria(
        [{"question_number": 1, "max_marks": 2.0}], {1: "One; two; three."}
    )
    assert len(drafted[0]["criteria"]) == 1


def test_gaps_stay_empty_and_are_reported():
    questions = [
        {"question_number": 1, "max_marks": 1.0, "question_format": "MCQ",
         "options": [{"label": "A", "text": "x"}]},
        {"question_number": 2, "max_marks": 2.0},
        {"question_number": 3, "max_marks": 0.0},
    ]
    drafted, problems = draft_criteria(questions, {1: "Z", 3: "Answer", 9: "Extra"})
    assert all(q["criteria"] == [] for q in drafted)
    text = " ".join(problems)
    for expected in ("Q1:", "Q2:", "Q3:", "Q9:"):
        assert expected in text


def test_bad_keys_are_rejected():
    with pytest.raises(AnswerKeyError):
        parse_answer_key("")
    with pytest.raises(AnswerKeyError):
        parse_answer_key("a,b\n1,2\n")
    key, problems = parse_answer_key("Question_Number,Correct_Answer\n1,A\n1,B\nx,C\n2,\n")
    assert key == {1: "A"}
    assert len(problems) == 3


def test_draft_edit_approve_round_trip(client):
    assert upload(client, PAPER).status_code == 200
    reply = draft(client)
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["approved"] is False and body["problems"] == []
    questions = body["questions"]
    assert client.get(f"/exam/{EXAM}/rubric").status_code != 200  # a draft is not approved

    questions[2]["criteria"][0]["description"] = "Teacher's edited point"
    questions[0]["correct_option"] = "A"
    saved = approve(client, questions)
    assert saved.status_code == 200, saved.text
    rubric = client.get(f"/exam/{EXAM}/rubric").json()["questions"]
    assert rubric[2]["criteria"][0]["description"] == "Teacher's edited point"
    assert rubric[0]["correct_option"] == "A"


def test_draft_needs_a_saved_paper(client):
    assert draft(client).status_code == 404


@pytest.mark.parametrize("damage", ["empty", "blank", "no_option", "bad_option"])
def test_incomplete_criteria_are_blocked(client, firestore_client, damage):
    upload(client, PAPER)
    questions = draft(client).json()["questions"]
    if damage == "empty":
        questions[2]["criteria"] = []
    elif damage == "blank":
        questions[3]["criteria"][0]["description"] = "   "
    elif damage == "no_option":
        questions[0]["correct_option"] = ""
    else:
        questions[0]["correct_option"] = "Z"
    reply = approve(client, questions)
    assert reply.status_code == 422, reply.text
    assert client.get(f"/exam/{EXAM}/rubric").status_code != 200


def test_unconfirmed_draft_is_blocked(client):
    upload(client, PAPER)
    assert approve(client, draft(client).json()["questions"], confirmed=False).status_code == 422


@pytest.mark.skipif(not (DATA / "Question.txt").exists(), reason="Dataset not present.")
def test_real_exam_key_drafts_every_question(client):
    assert upload(client, (DATA / "Question.txt").read_bytes()).status_code == 200
    body = draft(client, (DATA / "answerkey.txt").read_bytes()).json()
    questions = body["questions"]
    assert body["problems"] == []
    assert len(questions) == 35
    assert sum(q["max_marks"] for q in questions) == 50
    assert all(q["criteria"] for q in questions)
    assert questions[0]["correct_option"] == "B"
    assert approve(client, questions).status_code == 200
