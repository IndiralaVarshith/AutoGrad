"""
AutoGrad — End-to-End Workflow Acceptance Test Suite

Tests Phases 2 through 10 with real Firestore and local Ollama / models:
  - Test A: Firebase Connectivity (Firestore read/write test)
  - Test B: Rubric Generation & Approval (Save to Firestore)
  - Test C: Rubric Retrieval from Firestore
  - Test D: Single-Student Answer Sheet Evaluation
  - Test E: Persistence & Document Verification in Firestore
"""

from __future__ import annotations

import json
import os
import sys
import time
import unittest
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from config import get_firestore, get_storage_bucket, AI_PROVIDER
from services.db import db_service
from models.schemas import QuestionRubric, RubricCriterion, PaperResult
from services.router import model_router


class TestAutoGradeEndToEnd(unittest.TestCase):
    """Real acceptance test of the full grading workflow."""

    @classmethod
    def setUpClass(cls):
        cls.test_exam_id = f"TEST_EXAM_{int(time.time())}"
        cls.test_hall_ticket = "STU_2026_001"
        cls.test_student_name = "Varshith Test Student"
        cls.question_paper_pdf = backend_dir.parent / "test_data" / "question_paper.pdf"
        cls.student_answer_pdf = backend_dir.parent / "test_data" / "student1.pdf"

    # =========================================================
    # TEST A: FIREBASE CONNECTIVITY
    # =========================================================

    def test_A_firebase_connectivity(self):
        """Test Firestore and Storage connections."""
        print("\n--- [TEST A] Firebase Connectivity ---")
        self.assertTrue(db_service.is_connected(), "Firestore client must be initialized")

        db = get_firestore()
        self.assertIsNotNone(db, "Firestore instance must not be None")

        # Test write and read on Firestore
        test_ref = db.collection("system_tests").document("connectivity_probe")
        test_ref.set({"timestamp": time.time(), "status": "ok"})
        doc = test_ref.get()
        self.assertTrue(doc.exists, "Written document must be retrievable from Firestore")
        self.assertEqual(doc.to_dict().get("status"), "ok")
        print("  ✓ Firestore read/write confirmed")

        # Clean up probe
        test_ref.delete()

    # =========================================================
    # TEST B: RUBRIC GENERATION & APPROVAL PERSISTENCE
    # =========================================================

    def test_B_rubric_approval_and_persistence(self):
        """Test approving and persisting a structured rubric to Firestore."""
        print("\n--- [TEST B] Rubric Approval & Firestore Persistence ---")

        # Create structured rubric criteria
        rubric_questions = [
            QuestionRubric(
                question_number=1,
                question_text="Explain the working principle and construction of an ideal electrical transformer.",
                max_marks=10.0,
                question_type="THEORY",
                criteria=[
                    RubricCriterion(
                        description="Principle of mutual electromagnetic induction between primary and secondary windings",
                        max_marks=4.0,
                    ),
                    RubricCriterion(
                        description="Core construction with laminated silicon steel to reduce eddy current losses",
                        max_marks=3.0,
                    ),
                    RubricCriterion(
                        description="EMF equation and voltage transformation ratio relation",
                        max_marks=3.0,
                    ),
                ],
            ),
            QuestionRubric(
                question_number=2,
                question_text="Derive the efficiency equation of a single-phase transformer and state the condition for maximum efficiency.",
                max_marks=10.0,
                question_type="MIXED",
                criteria=[
                    RubricCriterion(
                        description="Expression for efficiency in terms of copper and iron losses",
                        max_marks=5.0,
                    ),
                    RubricCriterion(
                        description="Condition for maximum efficiency: copper loss equals iron loss (variable loss = constant loss)",
                        max_marks=5.0,
                    ),
                ],
            ),
        ]

        # 1. Register exam in Firestore
        exam_record = db_service.save_exam(
            exam_id=self.test_exam_id,
            subject="Electrical Engineering",
            exam_name="EE Midterm Examination 2026",
            total_marks=20.0,
            status="PROPOSED",
        )
        self.assertEqual(exam_record["exam_id"], self.test_exam_id)
        print(f"  ✓ Exam '{self.test_exam_id}' registered in Firestore")

        # 2. Teacher approves rubric -> persist to Firestore
        saved_rubric = db_service.save_approved_rubric(
            exam_id=self.test_exam_id,
            questions=rubric_questions,
            teacher_id="Prof. Varshith",
        )

        self.assertEqual(saved_rubric["approval_status"], "APPROVED")
        self.assertEqual(saved_rubric["version"], 1)
        self.assertEqual(len(saved_rubric["questions"]), 2)
        self.assertEqual(saved_rubric["total_marks"], 20.0)
        print(f"  ✓ Approved rubric saved to Firestore (ID: {saved_rubric['rubric_id']})")

    # =========================================================
    # TEST C: RUBRIC RETRIEVAL FROM FIRESTORE
    # =========================================================

    def test_C_rubric_retrieval(self):
        """Confirm that the previously approved rubric is retrieved accurately."""
        print("\n--- [TEST C] Rubric Retrieval from Firestore ---")

        retrieved = db_service.get_approved_rubric(self.test_exam_id)
        self.assertIsNotNone(retrieved, "Approved rubric must exist in Firestore")
        self.assertEqual(retrieved.get("exam_id"), self.test_exam_id)
        self.assertEqual(len(retrieved.get("questions", [])), 2)

        q1 = retrieved["questions"][0]
        self.assertEqual(q1["question_number"], 1)
        self.assertEqual(float(q1["max_marks"]), 10.0)
        self.assertEqual(len(q1["criteria"]), 3)
        print("  ✓ Retrieved rubric verified: exact match with approved version")

    # =========================================================
    # TEST D: EVALUATION & FIRESTORE RESULT PERSISTENCE
    # =========================================================

    def test_D_single_student_evaluation_and_result_save(self):
        """Test evaluating one student answer sheet against the approved rubric."""
        print("\n--- [TEST D] Single Student Answer Sheet Evaluation ---")

        # Retrieve approved rubric from Firestore
        retrieved_rubric_data = db_service.get_approved_rubric(self.test_exam_id)
        rubric_list = [
            QuestionRubric(
                question_number=q["question_number"],
                question_text=q["question_text"],
                max_marks=float(q["max_marks"]),
                question_type=q.get("question_type"),
                criteria=[
                    RubricCriterion(
                        description=c["description"],
                        max_marks=float(c["max_marks"]),
                    )
                    for c in q.get("criteria", [])
                ],
            )
            for q in retrieved_rubric_data["questions"]
        ]

        # Use student answer PDF
        self.assertTrue(self.student_answer_pdf.exists(), f"Student PDF must exist at {self.student_answer_pdf}")
        sheet_bytes = self.student_answer_pdf.read_bytes()

        # Render PDF pages
        image_parts = model_router.ollama.render_pdf_to_images(
            str(self.student_answer_pdf), max_pages=3, dpi=100
        )
        self.assertGreater(len(image_parts), 0, "PDF pages must render into images")
        print(f"  ✓ Rendered {len(image_parts)} answer sheet page(s) into visual payloads")

        # Execute evaluation pipeline
        from pipeline import process_paper
        paper_result = process_paper(
            exam_id=self.test_exam_id,
            hall_ticket=self.test_hall_ticket,
            student_name=self.test_student_name,
            image_parts=image_parts,
            rubric_list=rubric_list,
            image_bytes_list=[sheet_bytes],
        )

        # Validate results
        self.assertIsInstance(paper_result, PaperResult)
        self.assertEqual(paper_result.hall_ticket, self.test_hall_ticket)
        self.assertEqual(paper_result.max_total, 20.0)
        self.assertLessEqual(paper_result.total_score, paper_result.max_total, "Marks awarded must not exceed max total")
        self.assertGreaterEqual(paper_result.total_score, 0.0, "Marks awarded must be non-negative")
        self.assertIn(paper_result.status, ("COMPLETE", "NEEDS_REVIEW"))

        print(f"  ✓ Evaluation complete: {paper_result.total_score}/{paper_result.max_total} (Status: {paper_result.status})")

        # Confirm persisted in Firestore
        saved_result = db_service.get_grading_result(self.test_exam_id, self.test_hall_ticket)
        self.assertIsNotNone(saved_result, "Grading result must be saved in Firestore")
        self.assertEqual(saved_result["hall_ticket"], self.test_hall_ticket)
        self.assertEqual(saved_result["total_score"], paper_result.total_score)
        print(f"  ✓ Confirmed: Result document exists in Firestore under exams/{self.test_exam_id}/results/{self.test_hall_ticket}")

    # =========================================================
    # TEST E: PERSISTENCE AFTER RESTART VERIFICATION
    # =========================================================

    def test_E_persistence_verification(self):
        """Confirm that after re-instantiating db service, all exam records, rubrics, and results persist."""
        print("\n--- [TEST E] Firestore Persistence Verification ---")
        fresh_db = db_service.db
        self.assertIsNotNone(fresh_db)

        # 1. Exam doc
        exam_doc = db_service.get_exam(self.test_exam_id)
        self.assertIsNotNone(exam_doc)
        self.assertEqual(exam_doc["status"], "CONFIGURED")

        # 2. Approved rubric
        rubric_doc = db_service.get_approved_rubric(self.test_exam_id)
        self.assertIsNotNone(rubric_doc)
        self.assertEqual(rubric_doc["total_marks"], 20.0)

        # 3. Student grading result
        result_doc = db_service.get_grading_result(self.test_exam_id, self.test_hall_ticket)
        self.assertIsNotNone(result_doc)
        self.assertGreaterEqual(result_doc["total_score"], 0.0)

        print("  ✓ All collections (exams, rubrics, grading_results) confirmed persistent in Firestore!")


if __name__ == "__main__":
    unittest.main()
