"""Deleting exams and sheets, and keeping each exam's marks tied to its own paper. Emulator only."""

import pytest

from routes import results as results_route
from routes import upload as upload_route
from services import sheet_store
from services.db import db_service
from services.queue_manager import queue_manager
from tests.test_gemini_paper import PAPER_TEXT, app_client, approve, upload  # noqa: F401

OTHER_PAPER = PAPER_TEXT + b" A different paper."
SHEET = {"total_score": 1.0, "max_total": 3.0, "status": "COMPLETE", "questions": []}


@pytest.fixture
def client(app_client):  # noqa: F811
    test_client, _calls, _replies = app_client
    test_client.app.include_router(results_route.router)
    test_client.app.include_router(upload_route.router)
    yield test_client
    for job in list(queue_manager._jobs.values()):
        queue_manager.remove_jobs(job.exam_id)


def approved_exam(client, exam_id, text=PAPER_TEXT):
    rubric = upload(client, exam_id, text=text).json()["rubric"]
    assert approve(client, exam_id, rubric, rubric["total_marks"]).status_code == 200
    return rubric


def test_marks_drafted_from_another_paper_are_not_approved(client):
    rubric_a = upload(client, "paper-a").json()["rubric"]
    upload(client, "paper-b", text=OTHER_PAPER)

    response = approve(client, "paper-b", rubric_a, rubric_a["total_marks"])

    assert response.status_code == 409
    assert "different question paper" in response.json()["detail"]
    assert db_service.get_approved_rubric("paper-b") is None


def test_a_paper_with_graded_sheets_cannot_be_replaced(client):
    approved_exam(client, "graded")
    db_service.save_grading_result("graded", "S1", dict(SHEET))
    before = db_service.get_question_paper("graded")["doc_fingerprint"]

    response = upload(client, "graded", text=OTHER_PAPER, force=True)

    assert response.status_code == 409
    assert "1 answer sheets" in response.json()["detail"]
    assert db_service.get_question_paper("graded")["doc_fingerprint"] == before


def test_the_exam_list_names_the_paper_file(client):
    approved_exam(client, "named")
    exam = db_service.get_exam("named")
    assert exam["paper_filename"] == "paper.txt"
    assert exam["question_count"] == 2


def test_deleting_an_exam_removes_everything_in_it(client):
    approved_exam(client, "gone")
    db_service.save_grading_result("gone", "S1", dict(SHEET))
    db_service.db.collection("exams").document("gone").collection("review_logs").add({"hall_ticket": "S1"})
    sheet_store.save("gone", "S1", ".pdf", b"%PDF-1.4")
    queue_manager.enqueue("gone", "S2", "", b"%PDF-1.4 waiting", ".pdf", "application/pdf")
    approved_exam(client, "kept", text=OTHER_PAPER)

    response = client.delete("/exam/gone")

    assert response.status_code == 200
    assert response.json()["sheets_deleted"] == 1
    assert response.json()["queued_removed"] == 1
    assert db_service.get_exam("gone") is None
    assert db_service.get_question_paper("gone") is None
    assert db_service.get_approved_rubric("gone") is None
    assert db_service.list_exam_results("gone") == []
    assert db_service.get_grading_result("gone", "S1") is None
    assert list(db_service.db.collection("rubrics").where("exam_id", "==", "gone").stream()) == []
    assert list(db_service.db.collection("exams").document("gone").collections()) == []
    assert sheet_store.load("gone", "S1") is None
    assert queue_manager.get_exam_jobs("gone") == []
    assert db_service.get_approved_rubric("kept") is not None
    assert client.delete("/exam/gone").status_code == 404


def test_an_exam_is_not_deleted_while_a_sheet_is_being_graded(client):
    approved_exam(client, "busy")
    job = queue_manager.enqueue("busy", "S1", "", b"%PDF-1.4", ".pdf", "application/pdf")
    queue_manager.update_job_status(job.job_id, "PROCESSING", sync=False)

    response = client.delete("/exam/busy")

    assert response.status_code == 409
    assert db_service.get_approved_rubric("busy") is not None
    queue_manager.update_job_status(job.job_id, "COMPLETE", sync=False)


def test_deleting_one_sheet_leaves_the_others(client):
    approved_exam(client, "two")
    db_service.save_grading_result("two", "S1", dict(SHEET))
    db_service.save_grading_result("two", "S2", dict(SHEET))
    sheet_store.save("two", "S1", ".pdf", b"%PDF-1.4")
    queue_manager.enqueue("two", "S1", "", b"%PDF-1.4 done", ".pdf", "application/pdf")

    response = client.delete("/results/two/S1")

    assert response.status_code == 200
    assert [paper["hall_ticket"] for paper in db_service.list_exam_results("two")] == ["S2"]
    assert db_service.db.collection("grading_results").document("two_S1").get().exists is False
    assert sheet_store.load("two", "S1") is None
    # The student's sheet can be uploaded again once it is deleted.
    assert upload_route._ticket_taken_by_another_file("two", "S1", "a-new-file") is False
    assert client.delete("/results/two/S1").status_code == 404


def test_sheets_are_refused_when_the_chosen_marks_changed(client):
    approved_exam(client, "versioned")

    response = client.post(
        "/upload",
        data={"exam_id": "versioned", "expected_rubric_version": "99"},
        files={"files": ("S1.pdf", b"%PDF-1.4", "application/pdf")},
    )

    assert response.status_code == 409
    assert "changed since you chose" in response.json()["detail"]
    assert queue_manager.get_exam_jobs("versioned") == []
