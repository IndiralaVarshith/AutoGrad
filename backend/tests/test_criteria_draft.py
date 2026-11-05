"""A1: criteria drafted from an answer key, edited and approved on the emulator."""

from __future__ import annotations

import pytest

import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
from routes import exam as exam_route
from services import rubric_cache
from services.criteria_draft import AnswerKeyError, draft_criteria, parse_answer_key
from services.gemini_paper import PaperExtraction, paper_from_extraction

EXAM = "criteria_exam"
PAPER = b"Science Exam. (1 Mark Each) (2 Marks Each)"


def _question(number, marks, evidence, fmt="SHORT_ANSWER", options=()):
    return {
        "question_number": number, "section_id": "A", "question_text": f"Question {number}?",
        "question_format": fmt, "options": list(options), "printed_marks": marks,
        "marks_evidence": evidence,
    }


OPTIONS = [{"label": "A", "text": "one"}, {"label": "B", "text": "two"}]
REPLY = PaperExtraction.model_validate({
    "readable": True, "exam_title": "Science", "printed_total_marks": None,
    "sections": [{"section_id": "A"}],
    "questions": [
        _question(1, 1.0, "(1 Mark Each)", "MCQ", OPTIONS),
        _question(2, 1.0, "(1 Mark Each)", "MCQ", OPTIONS),
        _question(3, 2.0, "(2 Marks Each)"),
        _question(4, 2.0, "(2 Marks Each)"),
    ],
})


@pytest.fixture
def client(monkeypatch):
    try:
        requests.delete(
            f"http://{config.os.environ['FIRESTORE_EMULATOR_HOST']}/emulator/v1/projects/"
            f"{config.EMULATOR_PROJECT_ID}/databases/(default)/documents",
            timeout=3,
        )
    except requests.RequestException:
        pytest.skip("The Firestore emulator is not running.")
    rubric_cache._draft_cache.clear()
    monkeypatch.setattr(
        exam_route, "read_question_paper",
        lambda data, ext, **kwargs: paper_from_extraction(REPLY, data.decode()),
    )
    app = FastAPI()
    app.include_router(exam_route.router)
    return TestClient(app)


def upload(client, paper):
    return client.post(
        "/exam/setup", data={"exam_id": EXAM},
        files={"question_paper": ("paper.txt", paper, "text/plain")},
    )


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
def test_incomplete_criteria_are_blocked(client, damage):
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


