from __future__ import annotations

import io
import unittest

from PIL import Image
from unittest.mock import patch

import pipeline as pipeline_module
from models.schemas import (
    CriterionResult,
    CriticVerdict,
    OCRPageResult,
    OCRRegion,
    QuestionRubric,
    RubricCriterion,
)
from services.router import RouterResponse


class TestPipelineUncalibratedOCR(unittest.TestCase):
    def test_gemini_verification_is_triggered_only_for_risk_or_stable_sample(self):
        risk = pipeline_module._gemini_verification_reason(
            critic_verdict="REJECT",
            force_flag=False,
            illegible_regions=[],
            math_requires_manual_review=False,
            c_evid=1.0,
            exam_id="exam",
            hall_ticket="student",
            question_number=1,
            sub_part="",
        )
        self.assertEqual(risk, "local_critic_disagreement")

        with patch.dict("os.environ", {"GEMINI_VERIFICATION_SAMPLE_RATE": "1"}):
            first = pipeline_module._gemini_verification_reason(
                critic_verdict="ACCEPT",
                force_flag=False,
                illegible_regions=[],
                math_requires_manual_review=False,
                c_evid=1.0,
                exam_id="exam",
                hall_ticket="student",
                question_number=1,
                sub_part="",
            )
        self.assertEqual(first, "routine_quality_sample")

    def test_independent_disagreement_never_replaces_local_score(self):
        status, reason = pipeline_module._compare_independent_verification(
            {
                "transcription": "a very different answer that is long enough to compare",
                "addresses_question": False,
                "evidence_supported": False,
                "missed_relevant_content": True,
                "suggested_score": 5,
                "reason": "Different reading",
            },
            local_score=1.0,
            local_transcription="local answer transcription is definitely different",
            max_marks=5,
        )
        self.assertEqual(status, "DISAGREEMENT")
        self.assertIn("unsupported", reason)

    def test_regrade_uses_teacher_validated_text_and_stays_manual(self):
        rubric = QuestionRubric(
            question_number=1,
            question_text="State the concept.",
            max_marks=1.0,
            question_type="THEORY",
            criteria=[RubricCriterion(description="Correct concept", max_marks=1.0)],
        )
        criteria = [CriterionResult(
            criterion="Correct concept",
            verbatim_quote="validated wording",
            marks_awarded=1.0,
            rationale="The answer states the concept.",
        )]
        with (
            patch.object(
                pipeline_module,
                "grade_answer",
                return_value=(criteria, 1.0, "fake-model", "test route"),
            ) as grade,
            patch.object(
                pipeline_module,
                "critique_grade",
                return_value=CriticVerdict(verdict="ACCEPT", suggested_score=1.0),
            ),
            patch.object(pipeline_module, "verify_evidence", return_value=(criteria, 1.0)),
        ):
            result = pipeline_module.regrade_question_text(
                rubric,
                "teacher validated wording",
                question_type="THEORY",
            )

        self.assertEqual(grade.call_args.kwargs["extracted_text"], "teacher validated wording")
        self.assertEqual(result["c_ocr"], 0.0)
        self.assertEqual(result["routing"], "MANUAL")
        self.assertEqual(result["final_score"], 1.0)

    def test_uncertain_page_mapping_never_reaches_grading(self):
        rubric = QuestionRubric(
            question_number=1,
            question_text="State the concept.",
            max_marks=1.0,
            criteria=[RubricCriterion(description="Correct concept", max_marks=1.0)],
        )
        with (
            patch.object(pipeline_module, "extract_page_regions", return_value=[]),
            patch.object(
                pipeline_module,
                "extract_text",
                return_value=({(1, ""): {
                    "extracted_text": "possible continuation",
                    "illegible_regions": ["Question number regressed across pages."],
                    "mapping_status": "AMBIGUOUS_PAGE_ORDER",
                    "page_references": [{"page_number": 2, "image_sha256": "page-hash"}],
                }}, 0.0),
            ),
            patch.object(pipeline_module, "classify_questions", return_value=["THEORY"]),
            patch.object(pipeline_module, "verify_evidence", return_value=([], 0.0)),
            patch.object(pipeline_module, "grade_answer") as grade,
            patch.object(pipeline_module, "_save_to_firestore"),
        ):
            result = pipeline_module.process_paper(
                exam_id="exam",
                hall_ticket="student",
                student_name="",
                image_parts=[b"page-image"],
                rubric_list=[rubric],
                image_bytes_list=[b"source-file"],
            )

        grade.assert_not_called()
        self.assertEqual(result.questions[0].routing, "MANUAL")
        self.assertEqual(result.questions[0].final_score, 0.0)
        self.assertEqual(result.questions[0].source_page_references[0]["page_number"], 2)

    def test_agreed_verified_answer_is_accepted_but_paper_stays_provisional(self):
        rubric = QuestionRubric(
            question_number=1,
            question_text="State the concept.",
            max_marks=1.0,
            criteria=[RubricCriterion(description="Correct concept", max_marks=1.0)],
        )
        criteria = [CriterionResult(
            criterion="Correct concept",
            verbatim_quote="the concept",
            marks_awarded=1.0,
            rationale="The answer states the concept.",
        )]
        page = OCRPageResult(
            page_number=1,
            width=100,
            height=100,
            status="PROCESSED",
            regions=[OCRRegion(
                region_id="region-1",
                page_number=1,
                bbox=[0.1, 0.1, 0.8, 0.3],
                extracted_text="the concept",
                uncertainty_class="UNCALIBRATED",
                uncertainty_reason="Not calibrated",
            )],
        )

        with (
            patch.object(pipeline_module, "extract_page_regions", return_value=[page]),
            patch.object(
                pipeline_module,
                "extract_text",
                return_value=({(1, ""): {"extracted_text": "the concept", "illegible_regions": []}}, 0.0),
            ),
            patch.object(pipeline_module, "classify_questions", return_value=["THEORY"]),
            patch.object(
                pipeline_module,
                "grade_answer",
                return_value=(criteria, 1.0, "fake-model", "test"),
            ),
            patch.object(
                pipeline_module,
                "critique_grade",
                return_value=CriticVerdict(verdict="ACCEPT", suggested_score=1.0),
            ),
            patch.object(pipeline_module, "verify_evidence", return_value=(criteria, 1.0)),
            patch.object(pipeline_module, "_save_to_firestore"),
            patch.object(pipeline_module.db_service, "is_connected", return_value=True),
        ):
            result = pipeline_module.process_paper(
                exam_id="exam",
                hall_ticket="student",
                student_name="",
                image_parts=["page-image"],
                rubric_list=[rubric],
                image_bytes_list=[b"source-file"],
            )

        self.assertEqual(result.ocr_pages[0].regions[0].region_id, "region-1")
        self.assertEqual(result.questions[0].ocr_confidence_class, "UNCALIBRATED")
        self.assertEqual(result.questions[0].routing, "AUTO_ACCEPT")
        self.assertEqual(result.total_score, 1.0)
        self.assertEqual(result.finality_status, "PENDING_VALIDATION")

    def test_gemini_disagreement_is_preserved_separately_and_cannot_change_score(self):
        image = Image.new("RGB", (500, 250), "white")
        output = io.BytesIO()
        image.save(output, format="PNG")
        page_bytes = output.getvalue()
        rubric = QuestionRubric(
            question_number=1,
            question_text="State the concept.",
            max_marks=1.0,
            criteria=[RubricCriterion(description="Correct concept", max_marks=1.0)],
        )
        criteria = [CriterionResult(
            criterion="Correct concept",
            verbatim_quote="student answer states concept",
            marks_awarded=1.0,
            rationale="Local evidence supports the concept.",
            quote_verified=True,
            evidence_region_ids=["region-1"],
        )]
        gemini = RouterResponse(
            text="{}",
            parsed_json={
                "transcription": "This is a substantially different independent visual reading.",
                "addresses_question": False,
                "evidence_supported": False,
                "missed_relevant_content": True,
                "suggested_score": 0.0,
                "reason": "The image does not support the local claim.",
            },
            model_used="gemini-3.1-pro-preview",
            credential_id="gemini-legacy",
            duration_seconds=1.2,
            provider="gemini",
        )
        page = OCRPageResult(
            page_number=1,
            width=500,
            height=250,
            status="PROCESSED",
            image_sha256="page-sha256",
            regions=[OCRRegion(
                region_id="region-1",
                page_number=1,
                bbox=[0.1, 0.2, 0.8, 0.5],
                extracted_text="student answer states concept",
            )],
        )
        with (
            patch.dict("os.environ", {
                "GEMINI_VERIFICATION_ENABLED": "true",
                "GEMINI_VERIFICATION_SAMPLE_RATE": "0",
            }),
            patch.object(pipeline_module, "extract_page_regions", return_value=[page]),
            patch.object(
                pipeline_module,
                "extract_text",
                return_value=({(1, ""): {
                    "extracted_text": "student answer states concept",
                    "illegible_regions": [],
                }}, 0.0),
            ),
            patch.object(pipeline_module, "classify_questions", return_value=["THEORY"]),
            patch.object(pipeline_module, "grade_answer", return_value=(criteria, 1.0, "local", "local route")),
            patch.object(
                pipeline_module,
                "critique_grade",
                return_value=CriticVerdict(verdict="REJECT", suggested_score=0.0),
            ),
            patch.object(pipeline_module, "resolve", return_value=(criteria, 1.0, False)),
            patch.object(pipeline_module, "verify_evidence", return_value=(criteria, 1.0)),
            patch.object(pipeline_module, "_save_to_firestore"),
            patch.object(pipeline_module.model_router, "generate_gemini_verification", return_value=gemini) as verify,
        ):
            result = pipeline_module.process_paper(
                exam_id="exam",
                hall_ticket="student",
                student_name="",
                image_parts=[page_bytes],
                rubric_list=[rubric],
                image_bytes_list=[b"source-file"],
            )

        question = result.questions[0]
        verify.assert_called_once()
        self.assertTrue(verify.call_args.kwargs["images"])
        self.assertEqual(question.final_score, 0.0)
        self.assertEqual(question.suggested_score, 1.0)
        self.assertEqual(question.verification_status, "DISAGREEMENT")
        self.assertEqual(question.independent_verification["suggested_score"], 0.0)
        self.assertEqual(question.independent_verification["local_score_preserved"], 1.0)
        self.assertEqual(question.routing, "MANUAL")


if __name__ == "__main__":
    unittest.main()
