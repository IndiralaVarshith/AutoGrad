"""The queue's duplicate and retry rules, and the checks made before grading starts."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image

import routes.upload as upload_module
import services.queue_manager as queue_module
from routes.upload import _load_rubric_bundle, _validate_answer_sheet_bytes


def _enqueue(manager):
    return manager.enqueue(
        exam_id="exam", hall_ticket="student-1", student_name="Student",
        file_bytes=b"answer sheet", file_ext=".pdf", mime_type="application/pdf",
        rubric_fingerprint="rubric-v1",
    )


class TestEvaluationJobPersistence(unittest.TestCase):
    def test_duplicate_submission_reuses_job_and_failed_job_can_retry(self):
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch.object(queue_module, "QUEUE_FILE", Path(temp_dir) / "jobs.json"), \
                patch.object(queue_module, "SPOOL_DIR", Path(temp_dir) / "spool"):
            manager = queue_module.BatchQueueManager()
            with patch.object(manager, "sync_to_firestore"):
                job = _enqueue(manager)
                self.assertEqual(job.job_id, _enqueue(manager).job_id)
                manager.update_job_status(job.job_id, "TIMED_OUT", error_message="model")
                retried = manager.retry_job(job.job_id)
                self.assertEqual(retried.status, "PENDING")
                self.assertEqual(retried.attempt_count, 0)
                self.assertTrue(Path(retried.file_path).is_file())


class TestDocumentProcessing(unittest.TestCase):
    def test_low_quality_grayscale_scan_is_valid_input(self):
        output = io.BytesIO()
        Image.new("L", (120, 80), color=170).save(output, format="JPEG", quality=8)
        self.assertEqual(_validate_answer_sheet_bytes(output.getvalue(), ".jpg"), 1)


class TestApprovedRubricValidation(unittest.TestCase):
    def _rubric(self, total, count=8):
        return {
            "total_marks": total,
            "version": 1,
            "questions": [
                {
                    "question_number": number,
                    "question_text": f"Question {number}",
                    "max_marks": 2,
                    "criteria": [{"description": "Criterion", "max_marks": 2}],
                }
                for number in range(1, count + 1)
            ],
        }

    def _load(self, rubric):
        with patch.object(upload_module.db_service, "is_connected", return_value=True), \
                patch.object(upload_module.db_service, "get_approved_rubric", return_value=rubric):
            return _load_rubric_bundle("exam")

    def test_inconsistent_approved_total_blocks_evaluation(self):
        with self.assertRaises(HTTPException) as raised:
            self._load(self._rubric(20))
        self.assertEqual(raised.exception.status_code, 409)

    def test_every_rubric_field_reaches_grading(self):
        rubric = self._rubric(2, count=2)
        rubric["questions"][0].update(choice_group="g", choice_alternative="left", marks_evidence="[2]")
        rubric["questions"][1].update(choice_group="g", choice_alternative="right")
        questions, total, _, _ = self._load(rubric)
        self.assertEqual(total, 2)
        self.assertEqual(questions[0].choice_alternative, "left")
        self.assertEqual(questions[0].marks_evidence, "[2]")
