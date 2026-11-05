from __future__ import annotations

import asyncio
import copy
import unittest
from unittest.mock import patch

from fastapi import HTTPException

import routes.review as review_module
from models.schemas import CriterionResult, OCRQuestionRegradeRequest, OCRRegionReviewRequest


class _FakeDocRef:
    def __init__(self, data):
        self.data = copy.deepcopy(data)
        self.exists = True

    def get(self):
        return self

    def to_dict(self):
        return copy.deepcopy(self.data)

    def set(self, data):
        self.data = copy.deepcopy(data)


class _FakeCollection:
    def __init__(self):
        self.document_ref = None
        self.logs = []

    def document(self, *_args):
        return self.document_ref or self

    def collection(self, *_args):
        return self

    def set(self, *_args, **_kwargs):
        return None

    def add(self, item):
        self.logs.append(item)


class _FakeDB:
    def __init__(self, result_ref):
        self.result_ref = result_ref
        self.collection_ref = _FakeCollection()

    def collection(self, name):
        if name == "exams":
            return self.collection_ref
        return self.collection_ref


def _result_data():
    return {
        "exam_id": "exam-1",
        "hall_ticket": "student-1",
        "max_total": 5.0,
        "total_score": 2.0,
        "status": "NEEDS_REVIEW",
        "finality_status": "PENDING_VALIDATION",
        "questions": [{
            "question_number": 1,
            "sub_part": "",
            "max_marks": 5.0,
            "final_score": 2.0,
            "routing": "FLAGGED",
            "teacher_confirmed": False,
            "regrade_required": False,
            "extracted_text": "original transcript",
        }],
        "ocr_pages": [{
            "page_number": 1,
            "status": "PROCESSED",
            "validation_status": "PENDING",
            "regions": [{
                "region_id": "region-1",
                "page_number": 1,
                "extracted_text": "wrod",
                "teacher_corrected_text": None,
                "validation_status": "PENDING",
                "teacher_question_number": None,
                "teacher_sub_part": "",
                "question_mapping_status": "UNRESOLVED",
            }],
        }],
    }


class TestTeacherOCRValidation(unittest.TestCase):
    def test_region_correction_preserves_original_and_blocks_finality(self):
        doc_ref = _FakeDocRef(_result_data())
        db = _FakeDB(doc_ref)
        request = OCRRegionReviewRequest(
            exam_id="exam-1",
            hall_ticket="student-1",
            page_number=1,
            region_id="region-1",
            action="CORRECT",
            corrected_text="word",
            question_number=1,
        )
        with (
            patch.object(review_module, "get_firestore", return_value=db),
            patch.object(review_module, "_result_ref", return_value=doc_ref),
        ):
            response = asyncio.run(review_module.review_ocr_region(request))

        region = doc_ref.data["ocr_pages"][0]["regions"][0]
        question = doc_ref.data["questions"][0]
        self.assertEqual(region["extracted_text"], "wrod")
        self.assertEqual(region["teacher_corrected_text"], "word")
        self.assertEqual(region["validation_status"], "CORRECTED")
        self.assertEqual(question["teacher_corrected_text"], "word")
        self.assertEqual(question["ocr_region_ids"], ["region-1"])
        self.assertTrue(question["regrade_required"])
        self.assertEqual(response["finality_status"], "PENDING_VALIDATION")

    def test_final_result_requires_all_regions_and_questions_validated(self):
        data = _result_data()
        question = data["questions"][0]
        question.update(routing="AUTO_ACCEPT", teacher_confirmed=True)
        data["ocr_pages"][0]["regions"][0]["validation_status"] = "ACCEPTED"
        data["ocr_pages"][0]["regions"][0]["question_mapping_status"] = "TEACHER_CONFIRMED"
        data["ocr_pages"][0]["regions"][0]["teacher_question_number"] = 1
        review_module._refresh_validation_status(data)
        self.assertEqual(data["status"], "COMPLETE")
        self.assertEqual(data["finality_status"], "FINAL")

    def test_regrade_sends_teacher_text_to_grader_and_preserves_ocr(self):
        initial = _result_data()
        initial["rubric_version"] = "1"
        question = initial["questions"][0]
        question.update({
            "question_type": "THEORY",
            "teacher_corrected_text": "teacher corrected answer",
            "ocr_region_ids": ["region-1"],
            "regrade_required": True,
        })
        region = initial["ocr_pages"][0]["regions"][0]
        region.update({
            "validation_status": "CORRECTED",
            "teacher_corrected_text": "teacher corrected answer",
            "teacher_question_number": 1,
            "question_mapping_status": "TEACHER_CONFIRMED",
        })
        doc_ref = _FakeDocRef(initial)
        db = _FakeDB(doc_ref)
        approved = {
            "approval_status": "APPROVED",
            "version": 1,
            "questions": [{
                "question_number": 1,
                "sub_part": "",
                "question_text": "State the concept.",
                "max_marks": 5.0,
                "question_type": "THEORY",
                "criteria": [{"description": "Correct concept", "max_marks": 5.0}],
            }],
        }
        criteria = [CriterionResult(
            criterion="Correct concept",
            verbatim_quote="teacher corrected answer",
            marks_awarded=3.5,
            rationale="Evidence matches the approved criterion.",
            quote_verified=True,
        )]
        request = OCRQuestionRegradeRequest(
            exam_id="exam-1",
            hall_ticket="student-1",
            question_number=1,
        )
        with (
            patch.object(review_module, "get_firestore", return_value=db),
            patch.object(review_module, "_result_ref", return_value=doc_ref),
            patch.object(review_module.db_service, "get_approved_rubric", return_value=approved),
            patch.object(
                review_module,
                "regrade_question_text",
                return_value={
                    "criteria": criteria,
                    "grader_score": 3.5,
                    "critic_score": 3.5,
                    "final_score": 3.5,
                    "c_ocr": 0.0,
                    "c_agree": 1.0,
                    "c_evid": 1.0,
                    "c_math": 1.0,
                    "c_final": 0.7,
                    "routing": "MANUAL",
                    "routing_reason": "Teacher score confirmation required.",
                    "model_used": "fake-model",
                },
            ) as regrade,
        ):
            response = asyncio.run(review_module.regrade_validated_ocr(request))

        self.assertEqual(regrade.call_args.args[1], "teacher corrected answer")
        updated = doc_ref.data["questions"][0]
        self.assertEqual(updated["extracted_text"], "original transcript")
        self.assertEqual(updated["grading_input_text"], "teacher corrected answer")
        self.assertFalse(updated["regrade_required"])
        self.assertFalse(updated["teacher_confirmed"])
        self.assertEqual(updated["suggested_score"], 3.5)
        self.assertEqual(response["finality_status"], "PENDING_VALIDATION")

    def test_exam_total_limit_applies_to_teacher_score_changes(self):
        data = {"max_total": 4.0}
        with self.assertRaises(HTTPException) as raised:
            review_module._validated_total_score(
                data,
                [
                    {"final_score": 2.5, "max_marks": 3.0},
                    {"final_score": 2.0, "max_marks": 2.0},
                ],
            )
        self.assertEqual(raised.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
