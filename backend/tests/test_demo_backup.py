"""The demo's safety net: a saved exam comes back with no Gemini call."""

import importlib.util
import time
from pathlib import Path

import pytest

from models.schemas import PaperResult, QuestionGrade
from services import sheet_store
from services.db import db_service

_SPEC = importlib.util.spec_from_file_location(
    "demo_backup", Path(__file__).resolve().parents[1] / "scripts" / "demo_backup.py"
)
script = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(script)

QUESTION = {
    "question_number": 1, "question_text": "Explain", "max_marks": 2.0,
    "criteria": [{"description": "Idea", "max_marks": 2.0}],
}


@pytest.fixture
def exam():
    if not db_service.is_connected():
        pytest.skip("The Firestore emulator is not connected.")
    name = f"backup-{time.time_ns()}"
    db_service.save_approved_rubric(exam_id=name, questions=[QUESTION], teacher_confirmed=True, checked_total=2.0)
    db_service.save_grading_result(name, "Sheet_1", PaperResult(
        hall_ticket="Sheet_1", exam_id=name, max_total=2.0, flagged_count=1, status="NEEDS_REVIEW",
        questions=[QuestionGrade(question_number=1, max_marks=2.0, routing="FLAGGED", suggested_score=1.0)],
    ))
    return name


def test_save_then_restore_brings_back_results_rubric_and_pages(exam, tmp_path):
    script.save(exam, tmp_path)
    # A teacher's changes after the backup are undone by restoring.
    changed = db_service.get_grading_result(exam, "Sheet_1")
    changed["questions"][0]["routing"] = "AUTO_ACCEPT"
    db_service.save_grading_result(exam, "Sheet_1", changed)

    sheets = tmp_path / "sheets"
    sheets.mkdir()
    (sheets / "Sheet_1.pdf").write_bytes(b"pdf bytes")
    assert script.restore(exam, tmp_path, sheets) == ["Sheet_1"]

    restored = db_service.get_grading_result(exam, "Sheet_1")
    assert restored["questions"][0]["routing"] == "FLAGGED"
    assert restored["questions"][0]["suggested_score"] == 1.0
    assert db_service.get_approved_rubric(exam)["total_marks"] == 2.0
    assert sheet_store.load(exam, "Sheet_1") == (b"pdf bytes", ".pdf")


def test_nothing_to_save_or_restore_says_so(tmp_path):
    if not db_service.is_connected():
        pytest.skip("The Firestore emulator is not connected.")
    with pytest.raises(SystemExit, match="Nothing to save"):
        script.save("no-such-exam", tmp_path)
    with pytest.raises(SystemExit, match="No backup"):
        script.restore("no-such-exam", tmp_path)


def test_real_firestore_is_refused(monkeypatch):
    monkeypatch.setenv("FIRESTORE_EMULATOR_HOST", "firestore.googleapis.com:443")
    with pytest.raises(SystemExit, match="not a local"):
        script.require_emulator()
