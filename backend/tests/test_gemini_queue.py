"""Queue behaviour with Gemini: order, pauses, restarts and no double payment."""

import json
import time
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest
from fastapi.testclient import TestClient

import config
import main
from routes import upload as upload_route
from services import gemini_sheet, queue_manager as queue_module
from services.db import db_service
from services.gemini_client import DailyLimitReached, GeminiCallFailed, GeminiClient, RunCapReached
from services.queue_manager import BatchQueueManager


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(queue_module, "QUEUE_FILE", tmp_path / "jobs.json")
    monkeypatch.setattr(queue_module, "SPOOL_DIR", tmp_path / "spool")
    made = BatchQueueManager()
    monkeypatch.setattr(made, "sync_to_firestore", lambda job: None)
    return made


def add(manager, ticket, exam="exam", **extra):
    job = manager.enqueue(
        exam_id=exam, hall_ticket=ticket, student_name="", file_bytes=ticket.encode(),
        file_ext=".pdf", mime_type="application/pdf", **extra,
    )
    return job


def run_next(manager, worker):
    """Run what the worker thread would run next. False when nothing may start."""
    job = manager._next_job()
    if job is None:
        return False
    manager._processor_callback = worker
    manager._run_job(job)
    return True


def result(status="COMPLETE"):
    return SimpleNamespace(model_dump=lambda: {}, status=status, total_score=1.0, max_total=1.0)


def test_oldest_goes_first_and_grade_one_now_goes_ahead(manager):
    first = add(manager, "a")
    time.sleep(0.01)
    add(manager, "b")
    time.sleep(0.01)
    now = add(manager, "c", priority=True)
    assert manager._next_job().job_id == now.job_id
    run_next(manager, lambda job: result())
    assert manager._next_job().job_id == first.job_id


def test_one_sheet_at_a_time_in_order(manager):
    order = []
    for ticket in "abc":
        add(manager, ticket)
        time.sleep(0.01)
    while run_next(manager, lambda job: (order.append(job.hall_ticket), result())[1]):
        pass
    assert order == ["a", "b", "c"]
    assert {job.status for job in manager.get_exam_jobs("exam")} == {"COMPLETE"}


def test_daily_limit_pauses_and_the_sheet_keeps_its_place_and_attempts(manager):
    job = add(manager, "a")

    def stop(_job):
        raise DailyLimitReached("Daily allowance used up.")

    run_next(manager, stop)
    again = manager.get_job(job.job_id)
    assert again.status == "PENDING" and again.attempt_count == 0
    assert again.current_stage == "waiting_for_quota"
    status = manager.pause_status()
    assert status["paused"] and not status["needs_resume"] and "Daily" in status["reason"]
    assert manager._next_job() is None
    assert manager._paused_until - time.time() <= 25 * 3600


def test_run_cap_waits_for_the_teacher_to_resume(manager):
    job = add(manager, "a")

    def stop(_job):
        raise RunCapReached("Run cap of 100 calls reached.")

    run_next(manager, stop)
    assert manager.pause_status()["needs_resume"]
    assert manager._next_job() is None
    manager.resume()
    assert manager._next_job().job_id == job.job_id


def test_overload_pauses_the_queue_then_fails_with_no_marks_after_three_tries(manager, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_OVERLOAD_PAUSE_SECONDS", 300)
    job = add(manager, "a")

    def busy(_job):
        raise GeminiCallFailed("Gave up: 503", transient=True)

    for attempt in (1, 2):
        run_next(manager, busy)
        current = manager.get_job(job.job_id)
        assert current.status == "PENDING" and current.attempt_count == attempt
        assert manager.pause_status()["paused"]
        assert manager._next_job() is None  # No calls spent on other sheets meanwhile.
        manager.resume()
    run_next(manager, busy)
    final = manager.get_job(job.job_id)
    assert final.status == "FAILED" and "No marks" in final.stage_detail
    assert not manager.pause_status()["paused"]
    assert manager.retry_job(job.job_id).status == "PENDING"


def test_permanent_error_fails_without_pausing(manager):
    job = add(manager, "a")

    def refused(_job):
        raise GeminiCallFailed("ClientError status=400")

    run_next(manager, refused)
    assert manager.get_job(job.job_id).status == "FAILED"
    assert not manager.pause_status()["paused"]


def test_restart_puts_a_cut_off_sheet_back_in_line(manager, tmp_path):
    job = add(manager, "a")
    manager.update_job_status(job.job_id, "PROCESSING", sync=False)
    restarted = BatchQueueManager()
    current = restarted.get_job(job.job_id)
    assert current.status == "PENDING" and "restart" in current.stage_detail
    assert Path(current.file_path).is_file()


def test_app_start_resumes_waiting_sheets(manager, monkeypatch):
    add(manager, "a")
    monkeypatch.setattr(upload_route, "queue_manager", manager)
    started = []
    monkeypatch.setattr(manager, "start_worker", lambda worker: started.append(worker))
    with TestClient(main.app):
        pass
    assert started == [upload_route._execute_grading_job]


def test_queue_files_are_not_in_the_system_temp_folder():
    assert config.QUEUE_DATA_DIR
    assert Path(queue_module.QUEUE_FILE).parent == Path(config.QUEUE_DATA_DIR)


# ------------------------------------------------- the job itself, on the emulator

def pdf_bytes():
    document = fitz.open()
    document.new_page().insert_text((50, 50), "sheet")
    return document.tobytes()


RUBRIC = {
    "question_number": 1, "question_text": "Explain", "max_marks": 2.0,
    "question_format": "SHORT_ANSWER", "section": "A",
    "criteria": [{"description": "Idea", "max_marks": 2.0}],
}
GOOD = {
    "readable": True,
    "answers": [{
        "question_number": 1, "number_source": "WRITTEN", "read_text": "my answer", "page_number": 1,
        "read_confidence": 0.95, "marks_awarded": 2.0, "reason": "Good",
        "evidence_quote": "my answer", "grading_confidence": 0.95,
    }],
}


@pytest.fixture
def gemini(tmp_path, monkeypatch, manager):
    if not db_service.is_connected():
        pytest.skip("The Firestore emulator is not connected.")
    monkeypatch.setattr(config, "GEMINI_RPM_LIMIT", 6000)
    calls = []

    def transport(model, contents, generation_config):
        calls.append(model)
        return SimpleNamespace(
            text=json.dumps(GOOD), parsed=None,
            usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=5, thoughts_token_count=0),
            candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name="STOP"))],
        )

    fake = GeminiClient(transport, usage_dir=tmp_path, sleep=lambda _s: None)
    monkeypatch.setattr(gemini_sheet, "get_client", lambda: fake)
    monkeypatch.setattr(upload_route, "get_client", lambda: fake)
    monkeypatch.setattr(upload_route, "queue_manager", manager)
    upload_route._batch_runs.clear()
    return calls


def graded_job(manager, exam, ticket="HT1"):
    return manager.enqueue(
        exam_id=exam, hall_ticket=ticket, student_name="", file_bytes=pdf_bytes(), file_ext=".pdf",
        mime_type="application/pdf", rubric_data=[RUBRIC], rubric_total_marks=2, rubric_version="1",
    )


def test_restart_after_saving_does_not_pay_twice_or_undo_teacher_edits(manager, gemini):
    exam = f"q-{time.time_ns()}"
    job = graded_job(manager, exam)
    first = upload_route._execute_grading_job(job)
    assert first.total_score == 2 and len(gemini) == 1

    saved = db_service.get_grading_result(exam, "HT1")
    saved["questions"][0]["final_score"] = 1.5  # A teacher changed the mark.
    saved["total_score"] = 1.5
    db_service.save_grading_result(exam, "HT1", saved)

    again = upload_route._execute_grading_job(job)
    assert len(gemini) == 1
    assert again.total_score == 1.5


def test_cap_covers_the_whole_batch_and_resume_starts_a_new_one(manager, gemini, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_RUN_CALL_CAP", 1)
    exam = f"q-{time.time_ns()}"
    first = graded_job(manager, exam, "HT1")
    second = graded_job(manager, exam, "HT2")
    upload_route._execute_grading_job(first)
    with pytest.raises(RunCapReached):
        upload_route._execute_grading_job(second)
    assert len(gemini) == 1

    monkeypatch.setattr(manager, "start_worker", lambda worker: None)
    client = TestClient(main.app)
    assert client.post(f"/queue/{exam}/resume").status_code == 200
    upload_route._execute_grading_job(second)
    assert len(gemini) == 2


def upload_many(client, exam, names, data=None):
    return client.post(
        "/upload",
        data={"exam_id": exam},
        files=[("files", (name, data or pdf_bytes() + name.encode(), "application/pdf")) for name in names],
    )


def test_bulk_upload_names_sheets_by_file_and_never_overwrites(manager, gemini, monkeypatch):
    monkeypatch.setattr(manager, "start_worker", lambda worker: None)
    monkeypatch.setattr(
        upload_route, "_load_rubric_bundle",
        lambda exam: ([upload_route.QuestionRubric(**RUBRIC)], 2.0, "1", "print"),
    )
    client = TestClient(main.app)
    exam = f"q-{time.time_ns()}"
    body = upload_many(client, exam, ["Student_7.pdf", "Student_8.pdf"]).json()
    assert body["queued"] == 2 and body["conflicts"] == []
    assert {j.hall_ticket for j in manager.get_exam_jobs(exam)} == {"Student_7", "Student_8"}

    # The same names with different files must not replace those students' results.
    second = upload_many(client, exam, ["Student_7.pdf", "Student_9.pdf"], data=pdf_bytes() + b"new").json()
    assert second["queued"] == 1 and second["conflicts"] == ["Student_7.pdf"]
    assert "different sheet" in second["message"]
    status = client.get(f"/queue/{exam}").json()
    assert status["total_jobs"] == 3 and status["paused"] is False
