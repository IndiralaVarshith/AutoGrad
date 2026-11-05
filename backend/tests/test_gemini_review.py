"""Teacher review on the emulator: decisions, approval, logs, pages and export."""

import io
import time

import fitz
import openpyxl
import pytest
from fastapi.testclient import TestClient

import main
from config import get_firestore
from models.schemas import PaperResult, QuestionGrade
from services import sheet_store
from services.db import db_service


def grade(number, routing="AUTO_ACCEPT", final=0.0, suggested=None, reason="", part="", text="my words"):
    return QuestionGrade(
        question_number=number, sub_part=part, max_marks=2.0, routing=routing, final_score=final,
        suggested_score=suggested, flag_reason=reason, extracted_text=text, grader_score=suggested or final,
    )


@pytest.fixture
def sheet():
    if not db_service.is_connected():
        pytest.skip("The Firestore emulator is not connected.")
    exam = f"r-{time.time_ns()}"
    result = PaperResult(
        hall_ticket="HT1", exam_id=exam, max_total=8.0, total_score=2.0, flagged_count=3,
        status="NEEDS_REVIEW", page_count=2,
        questions=[
            grade(1, final=2.0),
            grade(2, "FLAGGED", suggested=1.5, reason="LOW_CONFIDENCE"),
            grade(3, "MANUAL", reason="UNREADABLE", text=""),
            grade(4, "MANUAL", reason="MISSING", text=""),
        ],
    )
    db_service.save_grading_result(exam, "HT1", result)
    return exam


client = TestClient(main.app)


def stored(exam):
    return db_service.get_grading_result(exam, "HT1")


def question(exam, number):
    return next(q for q in stored(exam)["questions"] if q["question_number"] == number)


def logs(exam):
    db = get_firestore()
    return [d.to_dict() for d in db.collection("exams").document(exam).collection("review_logs").stream()]


def edit(exam, number, score, **extra):
    return client.post("/review/edit", json={
        "exam_id": exam, "hall_ticket": "HT1", "question_number": number, "new_score": score, **extra,
    })


def confirm(exam, number):
    return client.post("/review/confirm", json={"exam_id": exam, "hall_ticket": "HT1", "question_number": number})


def approve(exam, **extra):
    return client.post("/review/approve", json={"exam_id": exam, "hall_ticket": "HT1", **extra})


def decide_all(exam):
    assert confirm(exam, 2).status_code == 200
    assert edit(exam, 3, 1.0).status_code == 200
    assert client.post("/review/confirm-blanks", json={"exam_id": exam, "hall_ticket": "HT1"}).status_code == 200


def test_accepting_turns_the_suggestion_into_a_mark_and_logs_it(sheet):
    assert confirm(sheet, 2).status_code == 200
    assert question(sheet, 2)["final_score"] == 1.5
    assert stored(sheet)["total_score"] == 3.5
    entry = next(e for e in logs(sheet) if e["action"] == "CONFIRM")
    assert entry["flag_reason"] == "LOW_CONFIDENCE" and entry["accepted_score"] == 1.5


def test_nothing_to_accept_means_the_teacher_must_enter_a_mark(sheet):
    response = confirm(sheet, 3)
    assert response.status_code == 409 and "Enter the mark" in response.text
    assert question(sheet, 3)["routing"] == "MANUAL"


def test_edit_keeps_geminis_reading_and_logs_the_override(sheet):
    response = edit(sheet, 2, 2.0, corrected_text="what it really says", edit_reason="Misread")
    assert response.status_code == 200, response.text
    q = question(sheet, 2)
    assert q["final_score"] == 2.0 and q["teacher_override"] is True
    assert q["teacher_corrected_text"] == "what it really says"
    assert q["extracted_text"] == "my words"
    entry = next(e for e in logs(sheet) if e["action"] == "EDIT")
    assert entry["gemini_score"] == 1.5 and entry["new_score"] == 2.0
    assert entry["old_text"] == "my words" and entry["new_text"] == "what it really says"
    assert entry["edit_reason"] == "Misread"


def test_marks_outside_the_range_are_refused(sheet):
    assert edit(sheet, 2, 2.5).status_code == 400
    assert edit(sheet, 2, -1).status_code == 400


def test_confirming_blanks_only_touches_answers_flagged_missing(sheet):
    body = client.post("/review/confirm-blanks", json={"exam_id": sheet, "hall_ticket": "HT1"}).json()
    assert body["confirmed"] == ["4"]
    assert question(sheet, 4)["final_score"] == 0 and question(sheet, 4)["routing"] == "AUTO_ACCEPT"
    assert question(sheet, 3)["routing"] == "MANUAL"
    assert any(e["action"] == "CONFIRM_BLANKS" for e in logs(sheet))


def test_cannot_approve_while_flags_remain(sheet):
    response = approve(sheet)
    assert response.status_code == 409 and "flagged" in response.text
    assert stored(sheet)["finality_status"] != "FINAL"


def test_only_approval_makes_a_sheet_final_and_a_later_edit_undoes_it(sheet):
    decide_all(sheet)
    assert stored(sheet)["status"] == "COMPLETE"
    assert stored(sheet)["finality_status"] != "FINAL"
    assert approve(sheet).status_code == 200
    assert stored(sheet)["finality_status"] == "FINAL"
    assert any(e["action"] == "APPROVE" for e in logs(sheet))

    assert edit(sheet, 1, 0.5).status_code == 200
    assert stored(sheet)["finality_status"] != "FINAL"
    assert stored(sheet)["teacher_approved"] is False


def test_unmatched_writing_must_be_checked_before_approval(sheet):
    decide_all(sheet)
    data = stored(sheet)
    data["unmapped_answers"] = [{"extracted_text": "stray"}]
    db_service.save_grading_result(sheet, "HT1", data)
    response = approve(sheet)
    assert response.status_code == 409 and "fit no question" in response.text
    assert approve(sheet, unmatched_checked=True).status_code == 200


def test_bulk_approval_skips_flagged_and_unmatched_sheets():
    if not db_service.is_connected():
        pytest.skip("The Firestore emulator is not connected.")
    exam = f"r-{time.time_ns()}"
    clean = PaperResult(hall_ticket="clean", exam_id=exam, max_total=2.0, questions=[grade(1, final=2.0)], status="COMPLETE")
    flagged = PaperResult(hall_ticket="flagged", exam_id=exam, max_total=2.0, flagged_count=1, questions=[grade(1, "FLAGGED", suggested=1.0)], status="NEEDS_REVIEW")
    stray = PaperResult(hall_ticket="stray", exam_id=exam, max_total=2.0, questions=[grade(1, final=2.0)], status="NEEDS_REVIEW", unmapped_answers=[{"extracted_text": "x"}])
    for paper in (clean, flagged, stray):
        db_service.save_grading_result(exam, paper.hall_ticket, paper)
    body = client.post(f"/review/approve-unflagged/{exam}").json()
    assert body["approved"] == ["clean"]
    assert sorted(body["still_need_review"]) == ["flagged", "stray"]
    assert db_service.get_grading_result(exam, "flagged")["finality_status"] != "FINAL"


def test_scanned_page_comes_from_the_local_copy(sheet):
    document = fitz.open()
    for _ in range(2):
        document.new_page()
    sheet_store.save(sheet, "HT1", ".pdf", document.tobytes())
    response = client.get(f"/review/source-page/{sheet}/HT1/2")
    assert response.status_code == 200 and response.headers["content-type"] == "image/png"
    assert client.get(f"/review/source-page/{sheet}/HT1/3").status_code == 404


def test_store_names_cannot_leave_their_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(sheet_store.config, "QUEUE_DATA_DIR", str(tmp_path))
    sheet_store.save("../../evil", "../../evil", ".pdf", b"x")
    assert all(tmp_path in path.parents for path in tmp_path.rglob("*"))
    assert sheet_store.load("../../evil", "../../evil") == (b"x", ".pdf")


def workbook(response):
    return openpyxl.load_workbook(io.BytesIO(response.content)).active


def test_export_refuses_non_final_sheets(sheet):
    response = client.get(f"/export/{sheet}")
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["not_final"] == ["HT1"] and "NOT FINAL" in detail["message"]


def test_marked_export_says_not_final_everywhere(sheet):
    response = client.get(f"/export/{sheet}", params={"include_not_final": True})
    assert response.status_code == 200
    assert "NOT_FINAL" in response.headers["content-disposition"]
    ws = workbook(response)
    assert ws.title == "NOT FINAL"
    rows = list(ws.values)
    header, row = rows[0], rows[1]
    assert row[header.index("Final?")] == "NOT FINAL"
    assert row[header.index("Q2")] == "Needs review" and row[header.index("Q1")] == 2.0


def test_final_export_needs_no_flag_and_is_clean(sheet):
    decide_all(sheet)
    approve(sheet)
    response = client.get(f"/export/{sheet}")
    assert response.status_code == 200
    assert "NOT_FINAL" not in response.headers["content-disposition"]
    ws = workbook(response)
    assert ws.title == "Final marks"
    rows = list(ws.values)
    assert rows[1][rows[0].index("Final?")] == "FINAL"


def test_export_keeps_sub_parts_apart_and_shows_not_attempted():
    if not db_service.is_connected():
        pytest.skip("The Firestore emulator is not connected.")
    exam = f"r-{time.time_ns()}"
    paper = PaperResult(
        hall_ticket="HT1", exam_id=exam, max_total=4.0, teacher_approved=True, finality_status="FINAL",
        status="COMPLETE",
        questions=[
            grade(1, final=1.0, part="A"), grade(1, final=2.0, part="B"),
            grade(2, "NOT_ATTEMPTED"),
        ],
    )
    db_service.save_grading_result(exam, "HT1", paper)
    rows = list(workbook(client.get(f"/export/{exam}")).values)
    assert rows[1][rows[0].index("Q1A")] == 1.0 and rows[1][rows[0].index("Q1B")] == 2.0
    assert rows[1][rows[0].index("Q2")] == "Not attempted"
