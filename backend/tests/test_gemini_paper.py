"""Question paper on the Gemini path. Fake Gemini reply, real Firestore emulator."""

import json
from types import SimpleNamespace

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
from routes import exam as exam_route
from services import gemini_paper, rubric_cache
from services.db import db_service
from services.gemini_client import DailyLimitReached, GeminiClient
from services.gemini_paper import (
    PaperExtraction,
    calculate_section_total,
    paper_from_extraction,
    rubric_total_problems,
)


def question(number, part="", marks=2.0, evidence=None, **extra):
    return {
        "question_number": number,
        "sub_part": part,
        "section_id": extra.pop("section_id", "A"),
        "question_text": f"Question {number}{part}?",
        "question_format": extra.pop("question_format", "SHORT_ANSWER"),
        "printed_marks": marks,
        "marks_evidence": f"[{marks:g} marks]" if evidence is None else evidence,
        "draft_criteria": extra.pop(
            "draft_criteria",
            [{"description": "Point", "max_marks": marks}] if marks else [],
        ),
        **extra,
    }


def extraction(questions, total=None, sections=None, unclear=()):
    return PaperExtraction.model_validate({
        "readable": True,
        "exam_title": "Test",
        "printed_total_marks": total,
        "sections": sections or [{"section_id": "A"}],
        "questions": questions,
        "unclear_items": list(unclear),
    })


def row(number, marks, part="", group="", side=""):
    return {
        "question_number": number,
        "sub_part": part,
        "max_marks": marks,
        "choice_group": group,
        "choice_alternative": side,
    }


def test_sub_parts_add_up():
    assert calculate_section_total([row(1, 2, "a"), row(1, 3, "b")], None, "A")[0] == 5


def test_or_between_two_questions_counts_once():
    total, problems, _ = calculate_section_total([row(1, 5, group="g"), row(2, 5, group="g")], None, "A")
    assert total == 5 and not problems


def test_or_inside_a_question_counts_once_with_other_parts():
    rows = [row(3, 4, "a", group="g"), row(3, 4, "b", group="g"), row(3, 2, "c")]
    assert calculate_section_total(rows, None, "A")[0] == 6


def test_or_between_alternatives_with_several_parts():
    rows = [
        row(5, 3, "a", group="g", side="5"), row(5, 2, "b", group="g", side="5"),
        row(6, 4, "a", group="g", side="6"), row(6, 1, "b", group="g", side="6"),
    ]
    total, problems, _ = calculate_section_total(rows, None, "A")
    assert total == 5 and not problems


def test_or_with_unequal_alternatives_is_flagged():
    total, problems, _ = calculate_section_total([row(1, 5, group="g"), row(2, 3, group="g")], None, "A")
    assert total == 5 and "different marks" in problems[0]


def test_or_with_one_alternative_is_flagged():
    _, problems, _ = calculate_section_total([row(1, 5, group="g")], None, "A")
    assert "only one alternative" in problems[0]


def test_any_k_of_n():
    rows = [row(n, 5) for n in range(1, 6)]
    total, problems, notes = calculate_section_total(rows, 3, "A")
    assert total == 15 and not problems and "any 3 of 5" in notes[0]


def test_any_k_with_sub_parts_and_or_counts_whole_questions():
    rows = [
        row(1, 2, "a"), row(1, 3, "b"),
        row(2, 5, group="g"), row(3, 5, group="g"),
        row(4, 5),
    ]
    total, problems, _ = calculate_section_total(rows, 2, "A")
    assert total == 10 and not problems


def test_any_k_with_unequal_marks_is_flagged():
    total, problems, _ = calculate_section_total([row(1, 5), row(2, 3)], 1, "A")
    assert total == 5 and "different marks" in problems[0]


def test_any_k_larger_than_the_section_is_flagged():
    _, problems, _ = calculate_section_total([row(1, 5)], 2, "A")
    assert "answer any 2" in problems[0]


def test_clean_paper_needs_no_review():
    paper = paper_from_extraction(extraction([question(1), question(2)], total=4))
    assert paper["total_marks"] == 4
    assert paper["validation_status"] == "VALID"
    assert not paper["needs_teacher_review"]


def test_printed_total_mismatch_is_flagged_and_marks_are_not_changed():
    paper = paper_from_extraction(extraction([question(1), question(2)], total=10))
    assert paper["needs_teacher_review"]
    assert paper["validation_status"] == "DISCREPANCY"
    assert paper["total_marks"] == 4
    assert [q["max_marks"] for q in paper["questions"]] == [2, 2]


def test_printed_section_total_mismatch_is_flagged():
    paper = paper_from_extraction(
        extraction([question(1)], sections=[{"section_id": "A", "printed_section_marks": 9}])
    )
    assert any("prints 9 marks" in p for p in paper["problems"])


def test_missing_marks_are_never_filled_in():
    paper = paper_from_extraction(extraction([question(1, marks=None, evidence="")]))
    assert paper["questions"][0]["max_marks"] == 0
    assert any("prints no marks" in p for p in paper["problems"])


def test_marks_without_matching_printed_text_are_flagged():
    paper = paper_from_extraction(extraction([question(1, marks=5, evidence="[3 marks]")]))
    assert any("could not be matched" in p for p in paper["problems"])


def test_marks_text_not_on_the_paper_is_flagged():
    paper = paper_from_extraction(extraction([question(1)]), paper_text="Nothing relevant here.")
    assert any("was not found on the paper" in p for p in paper["problems"])
    clean = paper_from_extraction(extraction([question(1)]), paper_text="Q1 [2\n  MARKS]")
    assert not clean["problems"]


def test_criteria_are_flagged_not_scaled():
    bad = question(1, draft_criteria=[{"description": "Only", "max_marks": 1}])
    paper = paper_from_extraction(extraction([bad]))
    assert paper["questions"][0]["criteria"][0]["max_marks"] == 1
    assert any("criteria add up to 1" in p for p in paper["problems"])


def test_mcq_suggestion_never_becomes_the_key_by_itself():
    mcq = question(
        1, question_format="MCQ", suggested_option="b",
        options=[{"label": "A", "text": "x"}, {"label": "B", "text": "y"}],
    )
    record = paper_from_extraction(extraction([mcq]))["questions"][0]
    assert record["correct_option"] == "" and record["suggested_option"] == "B"


def test_mcq_problems():
    no_options = question(1, question_format="MCQ", options=[])
    bad_pick = question(
        2, question_format="MCQ", suggested_option="Z",
        options=[{"label": "A", "text": "x"}, {"label": "B", "text": "y"}],
    )
    problems = paper_from_extraction(extraction([no_options, bad_pick]))["problems"]
    assert any("without clear options" in p for p in problems)
    assert any("not an option" in p for p in problems)


def test_duplicate_numbers_and_gemini_doubts_are_listed():
    paper = paper_from_extraction(extraction([question(1), question(1)], unclear=["Page 2 is faint"]))
    assert any("more than once" in p for p in paper["problems"])
    assert any("Page 2 is faint" in p for p in paper["problems"])


def test_approval_totals():
    good = {
        "question_number": 1, "sub_part": "", "max_marks": 2.0,
        "criteria": [{"description": "Point", "max_marks": 2.0}],
    }
    assert rubric_total_problems([good], 2.0)[:2] == (2.0, [])
    assert "prints a total of 9" in rubric_total_problems([good], 9.0)[1][0]
    assert rubric_total_problems([good], None)[1] == []  # Teacher accepted the calculated total.
    assert "criteria add up" in rubric_total_problems([{**good, "max_marks": 3.0}], None)[1][0]
    assert "has no marks" in rubric_total_problems([{**good, "max_marks": 0.0}], None)[1][0]


# ------------------------------------------------------------ route, on the emulator

def gemini_reply(payload):
    return SimpleNamespace(
        text=json.dumps(payload),
        parsed=None,
        usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=5, thoughts_token_count=0),
        candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name="STOP"))],
    )


CLEAN = {
    "readable": True,
    "exam_title": "Test",
    "printed_total_marks": 3,
    "sections": [{"section_id": "A", "instruction_text": "Answer all", "printed_section_marks": 3}],
    "questions": [
        question(
            1, marks=1.0, evidence="1 Mark Each", question_format="MCQ", suggested_option="B",
            options=[{"label": "A", "text": "x"}, {"label": "B", "text": "y"}],
        ),
        question(2, marks=2.0, evidence="2 Marks"),
    ],
}
PAPER_TEXT = b"Test. 1 Mark Each. 1. Pick. A x B y. 2 Marks. 2. Explain."


@pytest.fixture
def app_client(monkeypatch, tmp_path):
    try:
        requests.delete(
            f"http://{config.os.environ['FIRESTORE_EMULATOR_HOST']}/emulator/v1/projects/"
            f"{config.FIREBASE_PROJECT_ID or 'demo-autograde'}/databases/(default)/documents",
            timeout=3,
        )
    except requests.RequestException:
        pytest.skip("The Firestore emulator is not running.")
    if not db_service.is_connected():
        pytest.skip("The Firestore emulator is not connected.")
    monkeypatch.setattr(config, "GRADING_ENGINE", "gemini")
    monkeypatch.setattr(config, "GEMINI_RPM_LIMIT", 6000)
    rubric_cache._draft_cache.clear()
    calls = []
    replies = {"next": CLEAN}

    def transport(model, contents, generation_config):
        calls.append(contents)
        outcome = replies["next"]
        if isinstance(outcome, Exception):
            raise outcome
        return gemini_reply(outcome)

    fake = GeminiClient(transport, usage_dir=tmp_path, sleep=lambda _s: None)
    monkeypatch.setattr(gemini_paper, "get_client", lambda: fake)
    app = FastAPI()
    app.include_router(exam_route.router)
    return TestClient(app), calls, replies


def upload(client, exam_id, text=PAPER_TEXT, key=None, force=False):
    files = {"question_paper": ("paper.txt", text, "text/plain")}
    if key:
        files["answer_key"] = ("key.txt", key, "text/plain")
    return client.post(
        "/exam/setup",
        data={"exam_id": exam_id, "force_regenerate": "true" if force else "false"},
        files=files,
    )


def approve(client, exam_id, rubric, total, confirmed=True):
    return client.post(
        "/exam/rubric/approve",
        json={
            "exam_id": exam_id, "rubric": rubric["questions"], "teacher_confirmed": confirmed,
            "total_marks": total, "doc_fingerprint": rubric["doc_fingerprint"],
        },
    )


def test_route_upload_edit_approve_saves_to_firestore(app_client):
    client, calls, _ = app_client
    response = upload(client, "exam-g1")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["saved"] and not body["needs_teacher_review"]
    assert len(calls) == 1
    rubric = body["rubric"]
    assert rubric["questions"][0]["correct_option"] == ""

    # The suggestion is not enough: the teacher has to choose.
    refused = approve(client, "exam-g1", rubric, 3)
    assert refused.status_code == 422 and "valid correct option" in refused.text

    rubric["questions"][0]["correct_option"] = "A"  # Teacher overrides Gemini's B.
    rubric["questions"][1]["criteria"] = [{"description": "Edited by teacher", "max_marks": 2.0}]
    assert approve(client, "exam-g1", rubric, 3, confirmed=False).status_code == 422
    done = approve(client, "exam-g1", rubric, 3)
    assert done.status_code == 200, done.text
    saved = db_service.get_approved_rubric("exam-g1")
    assert saved["teacher_confirmed"] is True and saved["total_marks"] == 3
    assert saved["questions"][0]["correct_option"] == "A"
    assert saved["questions"][0]["marks_evidence"] == "1 Mark Each"
    assert saved["questions"][1]["criteria"][0]["description"] == "Edited by teacher"


def test_same_files_cost_no_second_request(app_client):
    client, calls, _ = app_client
    upload(client, "exam-g2")
    again = upload(client, "exam-g2")
    assert again.json()["duplicate"] is True
    assert len(calls) == 1


def test_key_fills_the_picker_and_goes_in_the_one_request(app_client):
    client, calls, _ = app_client
    body = upload(client, "exam-g3", key=b"1. B").json()
    assert body["rubric"]["questions"][0]["correct_option"] == "B"
    assert len(calls) == 1 and "ANSWER KEY:" in calls[0]


def test_paper_with_problems_is_returned_not_saved(app_client):
    client, _, replies = app_client
    replies["next"] = {**CLEAN, "printed_total_marks": 50}
    body = upload(client, "exam-g4").json()
    assert body["needs_teacher_review"] and not body["saved"]
    assert any("prints a total of 50" in p for p in body["problems"])
    assert db_service.get_approved_rubric("exam-g4") is None
    # Printed total stays wrong until the teacher accepts the calculated one.
    rubric = body["rubric"]
    rubric["questions"][0]["correct_option"] = "B"
    assert approve(client, "exam-g4", rubric, 50).status_code == 422
    assert approve(client, "exam-g4", rubric, None).status_code == 200


def test_unreadable_paper_saves_nothing(app_client):
    client, _, replies = app_client
    replies["next"] = {"readable": False, "unclear_items": ["Blank pages"]}
    response = upload(client, "exam-g5")
    assert response.status_code == 422 and "Blank pages" in response.text


def test_quota_stop_saves_nothing_and_says_why(app_client, monkeypatch):
    client, _, _ = app_client

    def stopped(*_args, **_kwargs):
        raise DailyLimitReached("Daily allowance used up.")

    monkeypatch.setattr(exam_route, "read_question_paper", stopped)
    response = upload(client, "exam-g6")
    assert response.status_code == 429 and "Nothing was saved" in response.text
