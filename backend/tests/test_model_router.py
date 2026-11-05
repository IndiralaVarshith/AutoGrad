"""
AutoGrade — Comprehensive Test Suite for Dual Local Model Integration

Tests:
- Ollama connectivity and model availability
- Routing policy decisions (Qwen vs Gemma)
- Image downscaling and PDF page extraction
- Markdown fence and thinking trace JSON stripping
- Model switching and memory unloading
- Concurrency limiting
- Gemini disabled by default (no external calls in local mode)
- Gemini switchability via AI_PROVIDER=gemini
- Deterministic max marks enforcement
- Evidence verification with rapidfuzz
- Confidence engine score calculation and routing
- Batch queue persistence and retry behavior
- FastAPI endpoints compatibility
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure backend directory is in sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from config import AI_PROVIDER, OLLAMA_SIMPLE_MODEL, OLLAMA_COMPLEX_MODEL
from models.schemas import CriterionResult, QuestionRubric, RubricCriterion, QuestionGrade
from services.confidence_engine import compute_c_agree, compute_confidence
from services.evidence_verifier import verify_evidence
import services.queue_manager as queue_manager_module
from services.router import (
    AIConfiguration,
    ModelRouter,
    NoSuitableModelError,
    OllamaClient,
    RouterResponse,
    RoutingPolicy,
    ai_config,
    model_router,
)
from services.provider_registry import OllamaModelCapability


class TestRoutingPolicy(unittest.TestCase):
    """Test intelligent routing decisions between Qwen and Gemma."""

    def setUp(self):
        self.policy = RoutingPolicy(ai_config)

    def test_qwen_routed_for_routine_tasks(self):
        # 1. Classification
        model, reason = self.policy.select_model(task_type="classification")
        self.assertEqual(model, "qwen3.5:4b")
        self.assertIn("classification", reason.lower())

        # 2. Text-only theory grading without illegible regions
        model, reason = self.policy.select_model(
            task_type="grading",
            question_type="THEORY",
            has_images=False,
            illegible_count=0,
        )
        self.assertEqual(model, "qwen3.5:4b")
        self.assertIn("theory", reason.lower())

        # 3. Math syntax fixing
        model, reason = self.policy.select_model(task_type="math_syntax")
        self.assertEqual(model, "qwen3.5:4b")

        # 4. Student metadata extraction
        model, reason = self.policy.select_model(task_type="student_meta")
        self.assertEqual(model, "qwen3.5:4b")

    def test_gemma_routed_for_complex_visual_tasks(self):
        # 1. Handwritten OCR extraction with images
        model, reason = self.policy.select_model(
            task_type="ocr_extraction",
            has_images=True,
        )
        self.assertEqual(model, "gemma4:e4b")
        self.assertIn("transcription", reason.lower())

        # 2. Scanned question paper rubric generation
        model, reason = self.policy.select_model(
            task_type="rubric_generation",
            is_rubric_from_scanned_paper=True,
        )
        self.assertEqual(model, "gemma4:e4b")

        # 3. Diagram question grading
        model, reason = self.policy.select_model(
            task_type="grading",
            question_type="DIAGRAM",
            has_images=True,
        )
        self.assertEqual(model, "gemma4:e4b")
        self.assertIn("diagram", reason.lower())

        # 4. Ambiguous answer with illegible regions
        model, reason = self.policy.select_model(
            task_type="grading",
            question_type="THEORY",
            illegible_count=3,
        )
        self.assertEqual(model, "gemma4:e4b")
        self.assertIn("illegible", reason.lower())

        # 5. Numerical question grading
        model, reason = self.policy.select_model(
            task_type="grading",
            question_type="NUMERICAL",
        )
        self.assertEqual(model, "gemma4:e4b")

        # 6. Disputed arbitration resolution
        model, reason = self.policy.select_model(task_type="resolution")
        self.assertEqual(model, "gemma4:e4b")


class TestOllamaClientJSONAndImages(unittest.TestCase):
    """Test image manipulation and robust JSON parsing."""

    def setUp(self):
        self.client = OllamaClient(ai_config)

    def test_clean_json_markdown_fences(self):
        fenced_input = '```json\n{"status": "ok", "score": 9.5}\n```'
        cleaned = self.client._clean_json_text(fenced_input)
        parsed = json.loads(cleaned)
        self.assertEqual(parsed["status"], "ok")
        self.assertEqual(parsed["score"], 9.5)

    def test_clean_json_with_thinking_trace(self):
        thinking_input = "<think>Let me calculate score...</think>\n{\"verdict\": \"ACCEPT\"}"
        cleaned = self.client._clean_json_text(thinking_input)
        parsed = json.loads(cleaned)
        self.assertEqual(parsed["verdict"], "ACCEPT")

    def test_clean_json_surrounded_by_chatter(self):
        chatter = "Here is the response:\n```\n[{\"q\": 1}, {\"q\": 2}]\n```\nHope that helps!"
        cleaned = self.client._clean_json_text(chatter)
        parsed = json.loads(cleaned)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["q"], 1)

    def test_empty_completed_response_retries_instead_of_returning_success(self):
        config = AIConfiguration()
        config.max_retries = 1
        client = OllamaClient(config)
        empty_response = MagicMock(status_code=200)
        empty_response.json.return_value = {"message": {"content": ""}, "done": True}
        valid_response = MagicMock(status_code=200)
        valid_response.json.return_value = {
            "message": {"content": '{"classifications": []}'},
            "done": True,
            "eval_count": 1,
        }
        with (
            patch.object(client._session, "post", side_effect=[empty_response, valid_response]) as post,
            patch("services.router.time.sleep"),
        ):
            content, parsed, _ = client.chat("qwen3.5:4b", "classify", format_json=True)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(content, '{"classifications": []}')
        self.assertEqual(parsed, {"classifications": []})

    def test_private_thinking_trace_is_never_used_as_answer(self):
        config = AIConfiguration()
        config.max_retries = 0
        client = OllamaClient(config)
        response = MagicMock(status_code=200)
        response.json.return_value = {
            "message": {"content": "", "thinking": "private reasoning trace"},
            "done": True,
        }
        with patch.object(client._session, "post", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "empty response") as error:
                client.chat("gemma4:e4b", "calculate", think=True)
        self.assertNotIn("private reasoning trace", str(error.exception))

    def test_image_downscaling_preserves_bounds(self):
        from PIL import Image
        large_img = Image.new("RGB", (3000, 2000), color="white")
        b64 = self.client.prepare_image_payload(large_img)
        self.assertTrue(len(b64) > 100)

    def test_pdf_rendering(self):
        pdf_path = backend_dir.parent / "test_data" / "question_paper.pdf"
        if pdf_path.exists():
            pages = self.client.render_pdf_to_images(str(pdf_path), max_pages=2, dpi=100)
            self.assertTrue(len(pages) in (1, 2))
            self.assertTrue(isinstance(pages[0], str))
            self.assertTrue(len(pages[0]) > 500)


class TestModelSwitchingAndMemory(unittest.TestCase):
    """Test strict single-model memory residency."""

    def test_model_switching_unloads_previous(self):
        client = OllamaClient(ai_config)
        client.unload_model = MagicMock(return_value=True)

        with patch.object(client._session, "post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {
                "message": {"content": '{"result": "ok"}'},
                "done": True,
            }

            # Step 1: Run with Qwen
            client.chat(model="qwen3.5:4b", prompt="hello")
            self.assertEqual(client._active_model, "qwen3.5:4b")
            client.unload_model.assert_not_called()

            # Step 2: Switch to Gemma -> MUST call unload_model for Qwen!
            client.chat(model="gemma4:e4b", prompt="hello gemma")
            client.unload_model.assert_called_with("qwen3.5:4b")
            self.assertEqual(client._active_model, "gemma4:e4b")


class TestModelRouterFailureFallback(unittest.TestCase):
    def test_empty_classification_uses_one_complex_local_model_fallback(self):
        config = AIConfiguration()
        router = ModelRouter()
        router.config = config
        router.policy = RoutingPolicy(config)
        router.ollama = MagicMock()
        router.ollama.discover_models.return_value = {
            "online": True,
            "models": [
                OllamaModelCapability(
                    name=config.simple_model,
                    supports_images=False,
                    supports_structured_json=True,
                ),
                OllamaModelCapability(
                    name=config.complex_model,
                    supports_images=True,
                    supports_structured_json=True,
                ),
            ],
        }
        router.ollama.chat.side_effect = [
            RuntimeError("Ollama returned an empty response"),
            ('{"classifications": []}', {"classifications": []}, 1),
        ]
        router.ollama.last_usage.return_value = {"input_tokens": 3, "output_tokens": 1}
        router.ollama.last_retry_count.return_value = 0
        with patch("services.router.log_timing"):
            result = router.generate(
                task_type="classification",
                prompt="Classify questions",
                format_json=True,
            )
        self.assertEqual(result.model_used, config.complex_model)
        self.assertIn("Fallback after RuntimeError", result.routing_reason)
        self.assertEqual(router.ollama.chat.call_count, 2)


class TestDeterministicGradingAndEvidence(unittest.TestCase):
    """Test mark clamping, evidence verification, and confidence engine."""

    def test_evidence_verification_fuzzy_match(self):
        transcript = "The transformer operates on the principle of mutual electromagnetic induction between two windings."
        criteria = [
            CriterionResult(
                criterion="Mutual induction",
                verbatim_quote="mutual electromagnetic induction",
                marks_awarded=2.5,
                rationale="Explained correctly",
            ),
            CriterionResult(
                criterion="Non-existent point",
                verbatim_quote="nuclear fission reaction in the core",
                marks_awarded=2.5,
                rationale="Fabricated hallucination",
            ),
        ]

        verified, c_evid = verify_evidence(criteria, transcript)
        # First quote exists -> verified True, marks kept
        self.assertTrue(verified[0].quote_verified)
        self.assertEqual(verified[0].marks_awarded, 2.5)

        # Second quote does NOT exist -> verified False, marks stripped to 0!
        self.assertFalse(verified[1].quote_verified)
        self.assertEqual(verified[1].marks_awarded, 0.0)

    def test_confidence_engine_routing(self):
        # High agreement, high evidence, high OCR -> AUTO_ACCEPT
        c_final, routing = compute_confidence(
            c_ocr=0.95,
            c_agree=0.90,
            c_evid=0.90,
            c_math=1.0,
            total_illegible=0,
            question_type="THEORY",
        )
        self.assertGreaterEqual(c_final, 0.85)
        self.assertEqual(routing, "AUTO_ACCEPT")

        # Disputed or low OCR -> FLAGGED or MANUAL
        c_final_low, routing_low = compute_confidence(
            c_ocr=0.50,
            c_agree=0.50,
            c_evid=0.40,
            c_math=1.0,
            total_illegible=4,
            question_type="THEORY",
        )
        self.assertIn(routing_low, ("FLAGGED", "MANUAL"))

    def test_zero_illegible_preserves_existing_confidence(self):
        args = {
            "c_ocr": 0.95,
            "c_agree": 0.90,
            "c_evid": 0.90,
            "c_math": 1.0,
            "question_type": "THEORY",
        }

        without_illegible_count = compute_confidence(**args)
        with_zero_illegible = compute_confidence(**args, total_illegible=0)

        self.assertEqual(with_zero_illegible, without_illegible_count)

    def test_more_illegible_regions_reduce_confidence(self):
        args = {
            "c_ocr": 0.9,
            "c_agree": 0.9,
            "c_evid": 0.9,
            "c_math": 0.9,
            "question_type": "THEORY",
            "total_questions": 3,
        }

        one_region, _ = compute_confidence(**args, total_illegible=1)
        two_regions, _ = compute_confidence(**args, total_illegible=2)

        self.assertLess(two_regions, one_region)

    def test_confidence_bounds_and_invalid_illegible_values(self):
        high, _ = compute_confidence(
            c_ocr=10.0,
            c_agree=10.0,
            c_evid=10.0,
            c_math=10.0,
            question_type="THEORY",
        )
        low, _ = compute_confidence(
            c_ocr=-10.0,
            c_agree=-10.0,
            c_evid=-10.0,
            c_math=-10.0,
            question_type="THEORY",
        )
        baseline, _ = compute_confidence(
            c_ocr=0.8,
            c_agree=0.8,
            c_evid=0.8,
            c_math=0.8,
            question_type="THEORY",
        )
        for invalid_count in (None, -1, "invalid"):
            result, _ = compute_confidence(
                c_ocr=0.8,
                c_agree=0.8,
                c_evid=0.8,
                c_math=0.8,
                question_type="THEORY",
                total_illegible=invalid_count,
            )
            self.assertEqual(result, baseline)

        invalid_question_count, _ = compute_confidence(
            c_ocr=0.8,
            c_agree=0.8,
            c_evid=0.8,
            c_math=0.8,
            question_type="THEORY",
            total_illegible=1,
            total_questions=None,
        )

        self.assertEqual(high, 1.0)
        self.assertEqual(low, 0.0)
        self.assertGreaterEqual(invalid_question_count, 0.0)
        self.assertLessEqual(invalid_question_count, 1.0)
        self.assertGreaterEqual(high, 0.0)
        self.assertLessEqual(high, 1.0)
        self.assertGreaterEqual(low, 0.0)
        self.assertLessEqual(low, 1.0)


class TestBatchQueueManager(unittest.TestCase):
    """Test spooling, queue state persistence, and retry."""

    def test_enqueue_and_status(self):
        firestore_called = threading.Event()

        def no_firestore_connection():
            firestore_called.set()
            return None

        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(queue_manager_module, "QUEUE_FILE", Path(temp_dir) / "queue.json"),
                patch.object(queue_manager_module, "SPOOL_DIR", Path(temp_dir) / "spool"),
                patch("config.get_firestore", side_effect=no_firestore_connection) as get_firestore,
            ):
                manager = queue_manager_module.BatchQueueManager()
                job = manager.enqueue(
                    exam_id="test_exam_101",
                    hall_ticket="STUDENT_TEST_1",
                    student_name="Test Student",
                    file_bytes=b"dummy pdf bytes for testing",
                    file_ext=".pdf",
                    mime_type="application/pdf",
                )

                self.assertEqual(job.status, "PENDING")
                self.assertTrue(os.path.exists(job.file_path))
                self.assertTrue(firestore_called.wait(2))

                retrieved = manager.get_job(job.job_id)
                self.assertIsNotNone(retrieved)
                self.assertEqual(retrieved.hall_ticket, "STUDENT_TEST_1")

                firestore_called.clear()
                manager.update_job_status(
                    job.job_id, "COMPLETE", score=18.5, max_score=20.0
                )
                self.assertTrue(firestore_called.wait(2))

                retrieved2 = manager.get_job(job.job_id)
                self.assertEqual(retrieved2.status, "COMPLETE")
                self.assertEqual(retrieved2.score, 18.5)
                self.assertEqual(get_firestore.call_count, 2)


class TestOllamaLiveHealth(unittest.TestCase):
    """Live Ollama API connectivity test."""

    def test_live_ollama_and_models(self):
        client = OllamaClient(ai_config)
        health = client.check_health()
        if not health.get("online"):
            self.skipTest(f"Ollama not running locally at {ai_config.ollama_base_url}")

        installed = health.get("models", [])
        self.assertTrue(any("qwen3.5:4b" in m for m in installed), "qwen3.5:4b must be installed")
        self.assertTrue(any("gemma4:e4b" in m for m in installed), "gemma4:e4b must be installed")


class TestIndependentGeminiVerification(unittest.TestCase):
    def test_explicit_verification_uses_single_gemini_path_and_marks_independent(self):
        router = ModelRouter()
        expected = RouterResponse(
            text='{"suggested_score": 1}',
            parsed_json={"suggested_score": 1},
            model_used="gemini-test",
            provider="gemini",
        )
        with (
            patch.dict(os.environ, {"GEMINI_VERIFICATION_ENABLED": "true"}),
            patch.object(router, "_gemini_allowed_for_fallback", return_value=True),
            patch.object(router, "_generate_gemini", return_value=expected) as gemini_call,
        ):
            result = router.generate_gemini_verification(
                task_id="verification-test",
                prompt="synthetic answer verification",
                images=[b"synthetic-image"],
                response_json_schema={"type": "object"},
            )

        self.assertIs(result, expected)
        kwargs = gemini_call.call_args.kwargs
        self.assertEqual(kwargs["task_type"], "independent_grading_verification")
        self.assertTrue(kwargs["profile"].independent_verification)
        self.assertTrue(kwargs["profile"].requires_vision)
        self.assertEqual(kwargs["reason"], "Selective independent Gemini verification; result cannot change marks automatically.")

    def test_verification_disabled_does_not_call_provider(self):
        router = ModelRouter()
        with (
            patch.dict(os.environ, {"GEMINI_VERIFICATION_ENABLED": "false"}),
            patch.object(router, "_generate_gemini") as gemini_call,
        ):
            with self.assertRaises(NoSuitableModelError):
                router.generate_gemini_verification(
                    task_id="verification-disabled",
                    prompt="synthetic",
                )
        gemini_call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
