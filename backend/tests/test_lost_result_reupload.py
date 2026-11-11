"""A sheet whose saved marks were lost can be uploaded and graded again."""

from types import SimpleNamespace

from routes import upload


def test_a_finished_job_with_no_saved_marks_is_forgotten(monkeypatch):
    removed = []
    job = SimpleNamespace(hall_ticket="s1", status="NEEDS_REVIEW")
    monkeypatch.setattr(upload.queue_manager, "get_exam_jobs", lambda exam_id: [job])
    monkeypatch.setattr(upload.queue_manager, "remove_jobs", lambda e, h: removed.append((e, h)))
    monkeypatch.setattr(upload.db_service, "is_connected", lambda: True)
    monkeypatch.setattr(upload.db_service, "get_grading_result", lambda e, h: None)
    upload._forget_lost_result("e", "s1")
    assert removed == [("e", "s1")]


def test_a_job_with_saved_marks_or_still_running_is_kept(monkeypatch):
    removed = []
    jobs = [SimpleNamespace(hall_ticket="s1", status="NEEDS_REVIEW")]
    monkeypatch.setattr(upload.queue_manager, "get_exam_jobs", lambda exam_id: jobs)
    monkeypatch.setattr(upload.queue_manager, "remove_jobs", lambda e, h: removed.append((e, h)))
    monkeypatch.setattr(upload.db_service, "is_connected", lambda: True)
    monkeypatch.setattr(upload.db_service, "get_grading_result", lambda e, h: {"hall_ticket": h})
    upload._forget_lost_result("e", "s1")
    jobs[0].status = "PROCESSING"
    monkeypatch.setattr(upload.db_service, "get_grading_result", lambda e, h: None)
    upload._forget_lost_result("e", "s1")
    assert removed == []
