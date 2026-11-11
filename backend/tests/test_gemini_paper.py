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


def test_mcq_without_drafted_points_can_still_be_approved():
    mcq = question(
        1, marks=1.0, question_format="MCQ", draft_criteria=[], suggested_option="A",
        options=[{"label": "A", "text": "x"}, {"label": "B", "text": "y"}],
    )
    paper = paper_from_extraction(extraction([mcq]))
    assert not paper["problems"]
    assert paper["questions"][0]["criteria"] == [
        {"description": "Selects the correct option", "max_marks": 1.0}
    ]
    assert rubric_total_problems(paper["questions"], None)[1] == []


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
            f"{config.EMULATOR_PROJECT_ID}/databases/(default)/documents",
            timeout=3,
        )
    except requests.RequestException:
        pytest.skip("The Firestore emulator is not running.")
    if not db_service.is_connected():
        pytest.skip("The Firestore emulator is not connected.")
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


# ------------------------------------------- paper replacement and approval, on the emulator

def test_emulator_mode_never_loads_the_key():
    """Runs in a fresh process so Firebase really starts up with the loaders blocked."""
    import subprocess
    import sys
    from pathlib import Path

    script = """
from firebase_admin import credentials
def blocked(*args, **kwargs):
    raise SystemExit("KEY_LOADER_CALLED")
credentials.Certificate = blocked
credentials.ApplicationDefault = blocked
import config
config._resolve_service_account = blocked
client = config.get_firestore()
print("PROJECT=" + client.project)
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(config.__file__).parent,
        capture_output=True, text=True, timeout=60,
    )
    assert "KEY_LOADER_CALLED" not in result.stderr + result.stdout
    assert f"PROJECT={config.EMULATOR_PROJECT_ID}" in result.stdout, result.stderr


def test_health_reports_emulator_mode():
    from main import health_check

    assert health_check()["firestore_emulator"] is True


OTHER_PAPER = PAPER_TEXT + b" A different file."


def approved(client, exam_id, text=PAPER_TEXT, force=False):
    rubric = upload(client, exam_id, text=text, force=force).json()["rubric"]
    rubric["questions"][0]["correct_option"] = "B"
    response = approve(client, exam_id, rubric, 3)
    assert response.status_code == 200, response.text
    return response.json()["rubric_id"], rubric


def test_different_paper_is_refused_unless_forced(app_client):
    client, _, _ = app_client
    upload(client, "exam-r1")
    assert upload(client, "exam-r1", text=OTHER_PAPER).status_code == 409
    assert upload(client, "exam-r1", text=OTHER_PAPER, force=True).status_code == 200


def test_forced_new_paper_cancels_the_approved_rubric(app_client):
    client, _, _ = app_client
    rubric_id, _ = approved(client, "exam-r2")
    body = upload(client, "exam-r2", text=OTHER_PAPER, force=True).json()
    assert body["invalidated_rubric_ids"] == [rubric_id]
    assert db_service.get_approved_rubric("exam-r2") is None
    assert client.get("/exam/exam-r2/rubric").status_code == 404


def test_forcing_the_same_file_cancels_nothing(app_client):
    client, _, _ = app_client
    rubric_id, _ = approved(client, "exam-r3")
    body = upload(client, "exam-r3", force=True).json()
    assert body["invalidated_rubric_ids"] == []
    assert db_service.get_approved_rubric("exam-r3")["rubric_id"] == rubric_id


def test_going_back_to_the_first_paper_needs_a_new_approval(app_client):
    client, _, _ = app_client
    approved(client, "exam-r4")
    upload(client, "exam-r4", text=OTHER_PAPER, force=True)
    upload(client, "exam-r4", force=True)
    assert db_service.get_approved_rubric("exam-r4") is None
    _, rubric = approved(client, "exam-r4", force=True)
    assert db_service.get_approved_rubric("exam-r4")["version"] == 2


def test_approving_twice_keeps_one_version(app_client):
    client, _, _ = app_client
    rubric_id, rubric = approved(client, "exam-r5")
    again = approve(client, "exam-r5", rubric, 3).json()
    assert again["rubric_id"] == rubric_id and again["version"] == 1


def test_paper_starts_unverified_and_approval_confirms_it(app_client):
    client, _, _ = app_client
    upload(client, "exam-r6")
    assert db_service.get_question_paper("exam-r6")["verification_status"] == "UNVERIFIED"
    approved(client, "exam-r6")
    assert db_service.get_question_paper("exam-r6")["verification_status"] == "TEACHER_CONFIRMED"


def test_firestore_offline_is_an_error_not_a_success(app_client, monkeypatch):
    client, _, _ = app_client
    monkeypatch.setattr(type(db_service), "is_connected", lambda self: False)
    assert upload(client, "exam-r7").status_code == 503
    refused = client.post("/exam/rubric/approve", json={
        "exam_id": "exam-r7", "teacher_confirmed": True,
        "rubric": [{"question_number": 1, "question_text": "a", "max_marks": 1,
                    "criteria": [{"description": "c", "max_marks": 1}]}],
    })
    assert refused.status_code == 503


def test_textbook_pages_are_not_sent_and_the_teacher_is_told(app_client):
    client, calls, _ = app_client
    response = client.post(
        "/exam/setup",
        data={"exam_id": "exam-r8", "force_regenerate": "false"},
        files={
            "question_paper": ("paper.txt", PAPER_TEXT, "text/plain"),
            "textbook": ("book.txt", b"TEXTBOOK WORDS", "text/plain"),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["warnings"]
    assert "TEXTBOOK WORDS" not in str(calls)


def test_figure_question_is_listed_for_review_with_its_wording_unchanged():
    figure = question(1, has_figure=True, figure_page=2)
    figure["question_text"] = "Study the graph on page 2."
    paper = paper_from_extraction(extraction([figure]))
    assert paper["questions"][0]["question_text"] == "Study the graph on page 2."
    assert any("page 2" in item["reason"] for item in paper["review_items"])


# ------------------------------------------------ the kind of paper, and how strict to mark

def test_paper_type_is_tidied_and_checked_against_the_paper():
    assert [gemini_paper.normal_paper_type(v) for v in ("mcq", " Written ", "", "other")] == ["MCQ", "WRITTEN", "MIXED", "MIXED"]
    questions = [{"question_number": 1, "question_format": "MCQ"}, {"question_number": 2, "question_format": "SHORT_ANSWER"}]
    assert gemini_paper.paper_type_mismatch("MIXED", questions) == ""
    assert "Q2" in gemini_paper.paper_type_mismatch("MCQ", questions)
    assert "Q1" in gemini_paper.paper_type_mismatch("WRITTEN", questions)


def test_ticking_the_wrong_kind_of_paper_is_said_and_nothing_is_saved(app_client):
    client, _, _ = app_client
    files = {"question_paper": ("paper.txt", PAPER_TEXT, "text/plain")}
    body = client.post("/exam/setup", data={"exam_id": "kind-1", "paper_type": "WRITTEN"}, files=files).json()
    assert body["needs_teacher_review"] and not body["saved"]
    assert any("handwritten paper" in problem and "Q1" in problem for problem in body["rubric"]["problems"])
    mixed = client.post("/exam/setup", data={"exam_id": "kind-2", "paper_type": "MIXED"}, files=files).json()
    assert mixed["saved"] and not mixed["needs_teacher_review"]


def test_the_teachers_marking_style_is_saved_with_the_approved_marks(app_client):
    client, _, _ = app_client
    rubric = upload(client, "style-1").json()["rubric"]
    rubric["questions"][0]["correct_option"] = "B"
    payload = {
        "exam_id": "style-1", "rubric": rubric["questions"], "teacher_confirmed": True,
        "total_marks": rubric["total_marks"], "doc_fingerprint": rubric["doc_fingerprint"],
        "marking_style": "strict", "marking_notes": "  Cut half a mark\nfor spelling. ",
    }
    first = client.post("/exam/rubric/approve", json=payload)
    assert first.status_code == 200, first.text
    saved = db_service.get_approved_rubric("style-1")
    assert saved["marking_style"] == "STRICT" and saved["marking_notes"] == "Cut half a mark for spelling."
    # Changing only the strictness is a new version, so sheets are marked again under it.
    second = client.post("/exam/rubric/approve", json={**payload, "marking_style": "BALANCED"})
    assert second.json()["version"] == first.json()["version"] + 1


# ------------------------------- internal choices misread (TS X Social, Toppers DPT-6, 20 marks)

def toppers_paper(any_k=None, groups=("Q9", "Q10"), section_marks=8):
    """Section I 4 x 1, Section II 4 x 2, Section III 2 x 4 with 9(A) or 9(B), 10(A) or 10(B)."""
    one = [question(n, marks=1.0, evidence="4 x 1 =4m", section_id="I") for n in (1, 2, 3, 4)]
    two = [question(n, marks=2.0, evidence="4 x 2 =8m", section_id="II") for n in (5, 6, 7, 8)]
    three = [
        question(n, part, marks=4.0, evidence="2 x 4=8m", section_id="III", choice_group=group)
        for n, group in zip((9, 10), groups) for part in ("A", "B")
    ]
    sections = [
        {"section_id": "I", "printed_section_marks": 4},
        {"section_id": "II", "printed_section_marks": 8},
        {"section_id": "III", "printed_section_marks": section_marks, "answer_any_k": any_k},
    ]
    return extraction(one + two + three, total=20, sections=sections)


def test_internal_choice_read_correctly_totals_twenty():
    paper = paper_from_extraction(toppers_paper())
    assert paper["total_marks"] == 20 and not paper["needs_teacher_review"]
    assert not any("read again" in note for note in paper["reconciliation_notes"])


@pytest.mark.parametrize("misread", [
    {"any_k": 1},                                   # "answer any one alternative" taken as any 1 question
    {"groups": ("III", "III")},                     # Q9 and Q10 put in one OR choice together
    {"any_k": 1, "groups": ("III", "III")},
])
def test_a_misread_internal_choice_is_repaired_from_the_printed_marks(misread):
    paper = paper_from_extraction(toppers_paper(**misread))
    assert paper["total_marks"] == 20 and not paper["needs_teacher_review"], paper["problems"]
    assert any("Section III" in note and "read again" in note for note in paper["reconciliation_notes"])
    nine, ten = ([q for q in paper["questions"] if q["question_number"] == n] for n in (9, 10))
    assert all(q["section_attempt_count"] is None for q in nine + ten)
    assert nine[0]["choice_group"] == nine[1]["choice_group"] != ten[0]["choice_group"] == ten[1]["choice_group"]
    assert [q["max_marks"] for q in paper["questions"]] == [1] * 4 + [2] * 4 + [4] * 4


def test_no_repair_is_made_up_when_the_printed_marks_do_not_support_it():
    # The section really is 'answer any one': printed as 1 x 4 = 4, and the paper total is 16.
    paper = paper_from_extraction(toppers_paper(any_k=1, section_marks=4))
    assert paper["total_marks"] == 16
    assert not any("read again" in note for note in paper["reconciliation_notes"])
    # A wrong total with nothing to repair stays a problem for the teacher.
    wrong = paper_from_extraction(extraction([question(1), question(2)], total=10))
    assert wrong["total_marks"] == 4 and wrong["needs_teacher_review"]


def test_a_draft_read_before_the_fix_is_repaired_at_approval(app_client):
    client, _, replies = app_client
    replies["next"] = json.loads(toppers_paper().model_dump_json())
    body = upload(client, "toppers").json()
    rubric = body["rubric"]
    for q in rubric["questions"]:  # As an old draft in the browser would have it.
        if q["section"] == "III":
            q["section_attempt_count"] = 1
    response = approve(client, "toppers", rubric, 20)
    assert response.status_code == 200, response.text
    assert response.json()["total_marks"] == 20
    assert any("Section III" in w for w in response.json()["warnings"])
    saved = db_service.get_approved_rubric("toppers")
    assert all(q["section_attempt_count"] is None for q in saved["questions"] if q["section"] == "III")


# ------------------------------------------- marks printed once for a whole section ("4 x 2 =8m")

def section_paper(q7_parts=0, instruction="II. Answer the following questions 4 x 2 =8m", unsure=()):
    rows = [question(n, marks=2.0, evidence="4 x 2 =8m", section_id="II") for n in (5, 6, 8)]
    if q7_parts:
        rows += [question(7, str(i), marks=None, evidence="", section_id="II", draft_criteria=[]) for i in range(1, q7_parts + 1)]
    else:
        rows.append(question(7, marks=None, evidence="", section_id="II", draft_criteria=[]))
    sections = [{"section_id": "II", "instruction_text": instruction, "printed_section_marks": 8}]
    return extraction(rows, total=8, sections=sections, unclear=unsure)


def test_a_question_with_no_printed_marks_takes_them_from_the_section_formula():
    paper = paper_from_extraction(section_paper(unsure=[
        "Question 7 marks are not explicitly printed per sub-part, but the section total is 8m.",
    ]))
    q7 = next(q for q in paper["questions"] if q["question_number"] == 7)
    assert q7["max_marks"] == 2 and q7["marks_evidence"] == "4 x 2 =8m"
    assert q7["criteria"] == [{"description": "Correct and complete answer", "max_marks": 2}]
    assert paper["total_marks"] == 8 and not paper["needs_teacher_review"], paper["problems"]
    assert any("Q7: worth 2" in note for note in paper["reconciliation_notes"])


def test_map_parts_share_the_question_marks_in_half_steps():
    paper = paper_from_extraction(section_paper(q7_parts=4))
    parts = [q for q in paper["questions"] if q["question_number"] == 7]
    assert [p["max_marks"] for p in parts] == [0.5] * 4 and paper["total_marks"] == 8
    assert any("4 parts were given 0.5 each" in note for note in paper["reconciliation_notes"])


def test_no_marks_are_invented_without_a_formula_that_adds_up():
    for instruction in ("II. Answer the following questions", "II. 4 x 2 = 9m"):
        paper = paper_from_extraction(section_paper(instruction=instruction))
        q7 = next(q for q in paper["questions"] if q["question_number"] == 7)
        assert q7["max_marks"] == 0 and paper["needs_teacher_review"]
    paper = paper_from_extraction(section_paper(q7_parts=3))  # 2 / 3 is not a half-mark step.
    assert all(q["max_marks"] == 0 for q in paper["questions"] if q["question_number"] == 7)
