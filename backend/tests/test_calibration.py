"""Calibration from one teacher-marked sheet: the rules, then the routes on the test emulator."""

import hashlib
import uuid

import pytest

from routes import calibration as calibration_route
from routes import results as results_route
from routes import review as review_route
from routes import upload as upload_route
from services import calibration, sheet_store
from services.db import db_service
from services.queue_manager import QueueJob, queue_manager
from tests.test_gemini_paper import app_client, approve, upload  # noqa: F401
from tests.test_gemini_sheet import answer, client_for, good_reply, mcq_answer, pdf  # noqa: F401

RUBRIC = [
    {"question_number": 1, "sub_part": "", "question_format": "MCQ"},
    {"question_number": 2, "sub_part": "", "question_format": "SHORT_ANSWER"},
    {"question_number": 3, "sub_part": "A", "question_format": "LONG_ANSWER"},
]


def q(number, part="", **extra):
    return {
        "question_number": number, "sub_part": part, "max_marks": 2.0, "grader_score": 1.0,
        "final_score": 1.0, "routing": "AUTO_ACCEPT", "local_routing": "AUTO_ACCEPT",
        "extracted_text": f"answer {number}", **extra,
    }


# ------------------------------------------------------------------------- the rules

def test_one_marked_sheet_becomes_one_example_per_written_answer():
    sheet = {
        "exam_id": "e", "hall_ticket": "S1", "rubric_version": "3",
        "questions": [
            q(1, extracted_text="B"),  # multiple choice: marked by code, never calibrated
            q(2, final_score=2.0, teacher_override=True, edit_reason="Water acting as a lens is enough",
              teacher_corrected_text="lemon looks big due to water"),
            q(3, "a", local_routing="MANUAL", grader_score=0.0, final_score=1.5),
            q(4),  # not on the paper
            q(5, blank=True),
        ],
    }
    record = calibration.build(sheet, RUBRIC, now=10)
    assert record["hall_ticket"] == "S1" and record["rubric_version"] == "3" and record["created_at"] == 10
    by_number = {example["question_number"]: example for example in record["examples"]}
    assert sorted(by_number) == [2, 3]
    assert by_number[2] == {
        "question_number": 2, "sub_part": "", "max_marks": 2.0, "answer": "lemon looks big due to water",
        "app_marks": 1.0, "teacher_marks": 2.0, "remark": "Water acting as a lens is enough", "changed": True,
    }
    # The app could not mark this one, so there is no proposal to compare with.
    assert by_number[3]["app_marks"] is None and by_number[3]["sub_part"] == "A"
    assert record["corrections"] == 2


def test_extra_and_unattempted_answers_and_empty_text_teach_nothing():
    sheet = {"questions": [
        q(2, extra=True), q(3, "A", routing="NOT_ATTEMPTED"), q(2, extracted_text="   "),
    ]}
    assert calibration.build(sheet, RUBRIC)["examples"] == []


def test_the_prompt_example_is_short_and_leaves_out_what_is_unknown():
    record = {"examples": [
        {"question_number": 2, "sub_part": "", "answer": "x" * 5000, "app_marks": None,
         "teacher_marks": 1.5, "remark": ""},
        {"question_number": 3, "sub_part": "a", "answer": "y", "app_marks": 2.0, "teacher_marks": 1.0,
         "remark": "r" * 900},
    ]}
    shown = calibration.examples_for_prompt(record)
    assert shown[(2, "")] == {"answer": "x" * calibration.ANSWER_LIMIT, "teacher_marks": 1.5}
    assert shown[(3, "A")]["app_marks"] == 2.0
    assert len(shown[(3, "A")]["teacher_remark"]) == calibration.REMARK_LIMIT
    assert calibration.examples_for_prompt(None) == {}


def test_a_calibration_counts_only_for_the_same_approved_marks():
    record = {"rubric_version": "2", "examples": [{"answer": "x"}]}
    assert calibration.applies_to(record, "2") and calibration.applies_to(record, 2)
    assert not calibration.applies_to(record, "3")
    assert not calibration.applies_to({"rubric_version": "2", "examples": []}, "2")
    assert not calibration.applies_to(None, "2")


def test_teacher_decisions_are_noticed():
    assert not calibration.has_teacher_decisions({"questions": [q(2)]})
    assert calibration.has_teacher_decisions({"questions": [q(2, teacher_confirmed=True)]})
    assert calibration.has_teacher_decisions({"questions": [q(2, teacher_override=True)]})
    assert calibration.has_teacher_decisions({"teacher_approved": True, "questions": []})


# ------------------------------------------------------------- the routes, on the emulator

@pytest.fixture
def client(app_client, monkeypatch):  # noqa: F811
    test_client, _calls, _replies = app_client
    for router in (review_route.router, calibration_route.router, results_route.router, upload_route.router):
        test_client.app.include_router(router)
    # Nothing is graded for real here: the queue only collects the jobs.
    monkeypatch.setattr(queue_manager, "start_worker", lambda *_args, **_kwargs: None)
    yield test_client
    for job in list(queue_manager._jobs.values()):
        queue_manager.remove_jobs(job.exam_id)


def approved_exam(client, exam_id):
    rubric = upload(client, exam_id).json()["rubric"]
    rubric["questions"][0]["correct_option"] = "B"
    assert approve(client, exam_id, rubric, rubric["total_marks"]).status_code == 200


def marked_sheet(ticket, *, decided=True, teacher_touched=True, **extra):
    """Q1 multiple choice, Q2 written: the app gave 1, the teacher made it 2."""
    written = {
        "question_number": 2, "sub_part": "", "max_marks": 2.0, "grader_score": 1.0,
        "final_score": 2.0 if teacher_touched else 1.0, "routing": "AUTO_ACCEPT" if decided else "FLAGGED",
        "local_routing": "FLAGGED", "extracted_text": f"{ticket}: lemon looks big due to water",
        "teacher_override": teacher_touched, "teacher_confirmed": teacher_touched,
        "edit_reason": "Water acting as a lens is enough" if teacher_touched else "",
    }
    return {
        "hall_ticket": ticket, "exam_id": "", "rubric_version": "1", "max_total": 3.0,
        "total_score": 3.0, "status": "COMPLETE" if decided else "NEEDS_REVIEW",
        "flagged_count": 0 if decided else 1, "finality_status": "PENDING_VALIDATION",
        "source_file_reference": f"exams/x/sheets/{ticket}/{ticket}.pdf",
        "questions": [
            {"question_number": 1, "sub_part": "", "max_marks": 1.0, "final_score": 1.0, "grader_score": 1.0,
             "routing": "AUTO_ACCEPT", "local_routing": "AUTO_ACCEPT", "selected_option": "B", "extracted_text": "B"},
            written,
        ],
        **extra,
    }


def test_a_sheet_with_undecided_flags_cannot_teach_anything(client):
    approved_exam(client, "cal-flagged")
    db_service.save_grading_result("cal-flagged", "S1", marked_sheet("S1", decided=False, teacher_touched=False))

    response = client.post("/calibration/cal-flagged/sheet/S1")

    assert response.status_code == 409 and "Finish marking this sheet first" in response.json()["detail"]
    assert db_service.get_calibration("cal-flagged") is None
    assert db_service.get_grading_result("cal-flagged", "S1")["finality_status"] != "FINAL"


def test_using_a_marked_sheet_approves_it_and_lists_the_sheets_to_mark_again(client):
    exam = "cal-use"
    approved_exam(client, exam)
    db_service.save_grading_result(exam, "S1", marked_sheet("S1"))
    db_service.save_grading_result(exam, "S2", marked_sheet("S2", teacher_touched=False))
    db_service.save_grading_result(exam, "S3", marked_sheet("S3"))  # the teacher already worked on it

    response = client.post(f"/calibration/{exam}/sheet/S1")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["active"] and body["current"] and body["hall_ticket"] == "S1"
    assert body["answers"] == 1 and body["corrections"] == 1 and body["remarks"] == 1
    assert body["can_remark"] == ["S2"] and body["kept"] == ["S3"]
    assert db_service.get_grading_result(exam, "S1")["finality_status"] == "FINAL"
    saved = db_service.get_calibration(exam)
    assert saved["examples"][0]["answer"] == "S1: lemon looks big due to water"
    assert client.get(f"/calibration/{exam}").json()["hall_ticket"] == "S1"


def test_stopping_the_calibration_keeps_marks_already_given(client):
    exam = "cal-stop"
    approved_exam(client, exam)
    db_service.save_grading_result(exam, "S1", marked_sheet("S1"))
    assert client.post(f"/calibration/{exam}/sheet/S1").status_code == 200

    assert client.delete(f"/calibration/{exam}").status_code == 200
    assert client.get(f"/calibration/{exam}").json() == {"active": False}
    assert db_service.get_grading_result(exam, "S1")["total_score"] == 3.0
    assert client.delete(f"/calibration/{exam}").status_code == 404


def test_marking_again_queues_only_untouched_sheets_with_a_stored_file(client):
    exam = "cal-remark"
    approved_exam(client, exam)
    for ticket, touched in (("S1", True), ("S2", False), ("S3", False), ("S4", True)):
        db_service.save_grading_result(exam, ticket, marked_sheet(ticket, teacher_touched=touched))
    sheet_store.save(exam, "S2", ".pdf", pdf(1))  # S3 has no stored copy
    assert client.post(f"/calibration/{exam}/sheet/S1").status_code == 200

    response = client.post(f"/calibration/{exam}/remark")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["queued"] == ["S2"] and body["missing"] == ["S3"] and body["kept"] == ["S4"]
    jobs = queue_manager.get_exam_jobs(exam)
    assert [job.hall_ticket for job in jobs] == ["S2"] and jobs[0].rubric_version == "1"
    sheet_store.delete(exam, "S2")


def test_marking_again_needs_a_calibration(client):
    approved_exam(client, "cal-none")
    assert client.post("/calibration/cal-none/remark").status_code == 404


# ------------------------------------------------- grading follows the calibration

def test_new_sheets_get_the_calibration_but_the_calibration_sheet_does_not():
    exam = f"cal-settings-{uuid.uuid4().hex[:6]}"
    db_service.save_calibration(exam, {"calibration_id": "c1", "hall_ticket": "S1", "rubric_version": "1",
                                       "examples": [{"question_number": 2, "answer": "x"}]})
    assert upload_route._marking_settings(exam, "1", "S2")["calibration"]["calibration_id"] == "c1"
    assert "calibration" not in upload_route._marking_settings(exam, "1", "S1")
    assert "calibration" not in upload_route._marking_settings(exam, "2", "S2")
    db_service.delete_calibration(exam)


def _job(exam, tmp_path, rubric):
    path = tmp_path / "sheet.pdf"
    data = pdf(1)
    path.write_bytes(data)
    return QueueJob(
        job_id=f"j-{uuid.uuid4().hex[:6]}", exam_id=exam, hall_ticket="S2", student_name="", file_path=str(path),
        file_ext=".pdf", mime_type="application/pdf", status="PROCESSING", rubric_version="1",
        rubric_data=rubric, rubric_total_marks=3, file_sha256=hashlib.sha256(data).hexdigest(),
    )


@pytest.mark.parametrize("teacher_touched,graded_again", [(False, True), (True, False)])
def test_a_sheet_is_graded_again_for_a_new_calibration_only_if_the_teacher_has_not_touched_it(
    client_for, monkeypatch, tmp_path, teacher_touched, graded_again,  # noqa: F811
):
    if not db_service.is_connected():
        pytest.skip("The Firestore emulator is not connected.")
    from services import gemini_sheet
    from tests.test_gemini_sheet import mcq, written

    exam = f"cal-regrade-{uuid.uuid4().hex[:6]}"
    rubric = [mcq().model_dump(), written().model_dump()]
    job = _job(exam, tmp_path, rubric)
    fake_client, fake = client_for(good_reply(mcq_answer(), answer()))
    monkeypatch.setattr(gemini_sheet, "get_client", lambda: fake_client)
    monkeypatch.setattr(upload_route.queue_manager, "update_job_stage", lambda *a, **k: None)
    monkeypatch.setattr(upload_route.queue_manager, "get_job", lambda job_id: job)
    before = marked_sheet("S2", teacher_touched=teacher_touched, source_sha256=job.file_sha256)
    db_service.save_grading_result(exam, "S2", before)
    db_service.save_calibration(exam, {"calibration_id": "c9", "hall_ticket": "S1", "rubric_version": "1",
                                       "examples": [{"question_number": 2, "sub_part": "", "answer": "x",
                                                     "teacher_marks": 2.0}]})

    result = upload_route._execute_grading_job(job)

    assert (len(fake.calls) == 1) is graded_again
    saved = db_service.get_grading_result(exam, "S2")
    if graded_again:
        assert result.calibration_id == "c9" and saved["calibrated_from"] == "S1"
    else:
        assert saved["questions"][1]["edit_reason"] == "Water acting as a lens is enough"
