from __future__ import annotations

import json
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from services.math_agent import expected_expression_from_rubric, verify_math
from models.schemas import RubricCriterion
from services.ocr import extract_text
from services.router import NoSuitableModelError
import services.ocr as ocr_module
import services.classifier as classifier_module
import services.queue_manager as queue_module
import services.router as router_module
import routes.upload as upload_module
from routes.upload import _load_rubric_bundle, _validate_answer_sheet_bytes
from models.schemas import OCRPageResult, OCRRegion, QuestionRubric
from PIL import Image
from pipeline import _submission_source_hash


class TestEvaluationJobPersistence(unittest.TestCase):
    def test_submission_cache_identity_includes_all_pages_and_student_scope(self):
        two_pages = _submission_source_hash("exam", "student-1", [b"page-1", b"page-2"])
        changed_second_page = _submission_source_hash("exam", "student-1", [b"page-1", b"changed"])
        different_student = _submission_source_hash("exam", "student-2", [b"page-1", b"page-2"])
        self.assertNotEqual(two_pages, changed_second_page)
        self.assertNotEqual(two_pages, different_student)
        self.assertEqual(_submission_source_hash("exam", "student-1", None), "")

    def test_duplicate_submission_reuses_job_and_failed_job_can_retry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(queue_module, "QUEUE_FILE", Path(temp_dir) / "jobs.json"),
                patch.object(queue_module, "SPOOL_DIR", Path(temp_dir) / "spool"),
            ):
                manager = queue_module.BatchQueueManager()
                with patch.object(manager, "sync_to_firestore"):
                    job = manager.enqueue(
                        exam_id="exam",
                        hall_ticket="student-1",
                        student_name="Student",
                        file_bytes=b"answer sheet",
                        file_ext=".pdf",
                        mime_type="application/pdf",
                        rubric_fingerprint="rubric-v1",
                    )
                    duplicate = manager.enqueue(
                        exam_id="exam",
                        hall_ticket="student-1",
                        student_name="Student",
                        file_bytes=b"answer sheet",
                        file_ext=".pdf",
                        mime_type="application/pdf",
                        rubric_fingerprint="rubric-v1",
                    )
                    self.assertEqual(job.job_id, duplicate.job_id)

                    manager.update_job_status(job.job_id, "TIMED_OUT", error_message="model timeout")
                    retried = manager.retry_job(job.job_id)
                    self.assertEqual(retried.status, "PENDING")
                    self.assertEqual(retried.attempt_count, 0)
                    self.assertTrue(Path(retried.file_path).is_file())

    def test_missing_model_capability_becomes_review_without_zero_marks(self):
        class StopIdleLoop(Exception):
            pass

        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(queue_module, "QUEUE_FILE", Path(temp_dir) / "jobs.json"),
                patch.object(queue_module, "SPOOL_DIR", Path(temp_dir) / "spool"),
            ):
                manager = queue_module.BatchQueueManager()
                with patch.object(manager, "sync_to_firestore"):
                    job = manager.enqueue(
                        exam_id="exam",
                        hall_ticket="student-review",
                        student_name="Student",
                        file_bytes=b"answer sheet",
                        file_ext=".pdf",
                        mime_type="application/pdf",
                    )
                    manager._processor_callback = lambda _job: (_ for _ in ()).throw(
                        NoSuitableModelError("No compatible model; teacher validation is required.")
                    )
                    with patch.object(queue_module.time, "sleep", side_effect=StopIdleLoop):
                        with self.assertRaises(StopIdleLoop):
                            manager._process_loop()

                updated = manager.get_job(job.job_id)
                self.assertEqual(updated.status, "NEEDS_REVIEW")
                self.assertEqual(updated.error_code, "MODEL_REVIEW_REQUIRED")
                self.assertEqual(updated.current_stage, "teacher_validation")
                self.assertIsNone(updated.result)
                self.assertEqual(updated.score, 0.0)
                self.assertEqual(updated.max_score, 0.0)


class TestOcrReuseAndFailure(unittest.TestCase):
    def test_multpage_ocr_is_cached_by_source_hash(self):
        responses = [
            SimpleNamespace(text=json.dumps({"answers": [{
                "question_number": question,
                "sub_part": "a",
                "extracted_text": f"answer-{question}",
                "illegible_regions": [],
            }]}))
            for question in (1, 2)
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(ocr_module, "_OCR_CACHE_DIR", Path(temp_dir)),
                patch.object(ocr_module, "gemini_generate_with_retry", side_effect=responses) as call,
            ):
                first = extract_text(
                    ["page-one", "page-two"],
                    source_hash="student-sheet-hash",
                )
                second = extract_text(
                    ["page-one", "page-two"],
                    source_hash="student-sheet-hash",
                )
        self.assertEqual(first, second)
        self.assertEqual(call.call_count, 2)
        self.assertEqual(first[0][(1, "A")]["extracted_text"], "answer-1")
        self.assertEqual(first[0][(2, "A")]["extracted_text"], "answer-2")

    def test_ocr_timeout_is_not_reported_as_empty_success(self):
        with patch.object(
            ocr_module,
            "gemini_generate_with_retry",
            side_effect=TimeoutError("model timed out"),
        ):
            with self.assertRaisesRegex(TimeoutError, "timed out"):
                extract_text(["page"], source_hash="")

    def test_pagewise_ocr_keeps_page_hashes_and_previous_question_context(self):
        outputs = [
            {"answers": [{
                "question_number": 5,
                "sub_part": "ii",
                "extracted_text": "last answer on first page",
                "illegible_regions": [],
            }]},
            {"answers": [{
                "question_number": 0,
                "sub_part": "",
                "extracted_text": "continued working",
                "illegible_regions": [],
            }, {
                "question_number": 6,
                "sub_part": "",
                "extracted_text": "next numbered answer",
                "illegible_regions": [],
            }]},
        ]
        calls = []

        def respond(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(text=json.dumps(outputs[len(calls) - 1]))

        regions = [OCRPageResult(
            page_number=1,
            image_sha256="region-page-hash",
            regions=[OCRRegion(region_id="box-1", page_number=1)],
        )]
        with patch.object(ocr_module, "gemini_generate_with_retry", side_effect=respond):
            result, confidence = extract_text(
                [b"page-one", b"page-two"],
                source_hash="",
                page_regions=regions,
            )

        self.assertEqual(len(calls), 2)
        self.assertIn("Process source page 2 of 2", calls[1]["contents"][0])
        self.assertIn("last clearly numbered answer was Q5II", calls[1]["contents"][0])
        self.assertEqual(result[(5, "II")]["page_references"][0]["page_number"], 1)
        self.assertEqual(result[(5, "II")]["page_references"][0]["source_region_ids"], ["box-1"])
        unresolved_key = next(key for key in result if key[0] == 0)
        self.assertEqual(result[unresolved_key]["mapping_status"], "AMBIGUOUS_PAGE_CONTINUATION")
        self.assertEqual(result[unresolved_key]["candidate_question_number"], 5)
        self.assertEqual(result[(6, "")]["page_references"][0]["page_number"], 2)
        self.assertEqual(confidence, 0.0)

    def test_question_regression_and_duplicate_answer_are_preserved_for_review(self):
        responses = [
            SimpleNamespace(text=json.dumps({"answers": [{
                "question_number": 5, "sub_part": "a", "extracted_text": "five", "illegible_regions": [],
            }]})),
            SimpleNamespace(text=json.dumps({"answers": [{
                "question_number": 1, "sub_part": "a", "extracted_text": "reset", "illegible_regions": [],
            }, {
                "question_number": 5, "sub_part": "a", "extracted_text": "repeat", "illegible_regions": [],
            }]})),
        ]
        with patch.object(ocr_module, "gemini_generate_with_retry", side_effect=responses):
            result, _ = extract_text([b"page-one", b"page-two"], source_hash="")

        # An out-of-turn number is fine by itself; scanned pages are not in answer order.
        self.assertEqual(result[(1, "A")]["mapping_status"], "MAPPED")
        self.assertEqual(result[(5, "A")]["mapping_status"], "AMBIGUOUS_DUPLICATE")
        unresolved = [value for key, value in result.items() if key[0] == 0]
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(unresolved[0]["detected_question_number"], 5)
        self.assertEqual(unresolved[0]["extracted_text"], "repeat")

    def test_unparseable_question_number_is_not_defaulted_to_question_one(self):
        response = SimpleNamespace(text=json.dumps({"answers": [{
            "question_number": "unclear",
            "sub_part": "",
            "extracted_text": "uncertain answer",
            "illegible_regions": [],
        }]}))
        with patch.object(ocr_module, "gemini_generate_with_retry", return_value=response):
            result, _ = extract_text([b"page"], source_hash="")
        key = next(iter(result))
        self.assertEqual(key[0], 0)
        self.assertEqual(result[key]["mapping_status"], "UNRESOLVED")


class TestSympyVerification(unittest.TestCase):
    def test_only_explicit_rubric_answers_are_used(self):
        rubric = [
            RubricCriterion(description="Show the working", max_marks=2),
            RubricCriterion(description="Expected answer: 1/2", max_marks=1),
        ]
        self.assertEqual(expected_expression_from_rubric(rubric), "1/2")
        self.assertIsNone(expected_expression_from_rubric(rubric[:1]))

    def test_symbolic_equivalence_and_mismatch(self):
        correct = verify_math("x = 0.5", "1/2")
        incorrect = verify_math("x = 3", "1/2")
        self.assertTrue(correct.symbolic_correct)
        self.assertFalse(incorrect.symbolic_correct)


class TestQuestionClassification(unittest.TestCase):
    def test_reuses_approved_types_without_model_inference(self):
        rubrics = [
            QuestionRubric(
                question_number=1,
                question_text="Explain the process",
                max_marks=2,
                question_type="THEORY",
                criteria=[],
            ),
        ]
        with patch.object(classifier_module.model_router, "generate") as generate:
            result = classifier_module.classify_questions(rubrics)
        generate.assert_not_called()
        self.assertEqual(result, {0: "THEORY"})

    def test_accepts_top_level_structured_classification_list(self):
        response = SimpleNamespace(
            parsed_json=[
                {"index": 0, "question_type": "NUMERICAL"},
                {"index": 1, "question_type": "THEORY"},
            ],
            text="",
        )
        rubrics = [
            QuestionRubric(question_number=1, question_text="Calculate 2 + 2", max_marks=1, criteria=[]),
            QuestionRubric(question_number=2, question_text="Explain photosynthesis", max_marks=1, criteria=[]),
        ]
        with patch.object(classifier_module.model_router, "generate", return_value=response):
            result = classifier_module.classify_questions(rubrics)
        self.assertEqual(result, {0: "NUMERICAL", 1: "THEORY"})

    def test_accepts_classification_list_under_alternate_key(self):
        response = SimpleNamespace(
            parsed_json={
                "results": [{"index": 0, "question_type": "DIAGRAM"}],
            },
            text="",
        )
        rubric = QuestionRubric(
            question_number=1, question_text="Draw a circuit", max_marks=1, criteria=[]
        )
        with patch.object(classifier_module.model_router, "generate", return_value=response):
            result = classifier_module.classify_questions([rubric])
        self.assertEqual(result, {0: "DIAGRAM"})


class TestDocumentProcessing(unittest.TestCase):
    def test_all_pdf_pages_are_rendered_and_cached(self):
        try:
            import fitz
        except ImportError:
            self.skipTest("PyMuPDF is not installed.")

        document = fitz.open()
        for _ in range(3):
            document.new_page()
        pdf_bytes = document.tobytes()
        document.close()
        self.assertEqual(_validate_answer_sheet_bytes(pdf_bytes, ".pdf"), 3)

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(router_module, "_PDF_IMAGE_CACHE_DIR", Path(temp_dir)):
                first = router_module.model_router.ollama.render_pdf_to_images(
                    pdf_bytes, dpi=90, job_id="multi-page-test"
                )
                second = router_module.model_router.ollama.render_pdf_to_images(
                    pdf_bytes, dpi=90, job_id="multi-page-test"
                )
        self.assertEqual(len(first), 3)
        self.assertEqual(first, second)

    def test_low_quality_grayscale_scan_is_valid_input(self):
        buffer = io.BytesIO()
        Image.new("L", (24, 24), color=220).save(buffer, format="PNG")
        self.assertEqual(_validate_answer_sheet_bytes(buffer.getvalue(), ".png"), 1)


class TestApprovedRubricValidation(unittest.TestCase):
    def test_inconsistent_approved_total_blocks_evaluation(self):
        incomplete_rubric = {
            "total_marks": 20,
            "version": 1,
            "questions": [
                {
                    "question_number": number,
                    "question_text": f"Question {number}",
                    "max_marks": 2,
                    "criteria": [{"description": "Criterion", "max_marks": 2}],
                }
                for number in range(1, 9)
            ],
        }
        with (
            patch.object(upload_module.db_service, "is_connected", return_value=True),
            patch.object(
                upload_module.db_service,
                "get_approved_rubric",
                return_value=incomplete_rubric,
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                _load_rubric_bundle("exam-with-mismatch")
        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("structure totals 16", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
