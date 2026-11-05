"""
AutoGrade — Central Firestore Database Service Layer

Provides structured, unified CRUD operations for AutoGrade Firestore collections:
  - exams/{exam_id}
  - rubrics/{rubric_id}
  - submissions/{submission_id}
  - grading_results/{result_id}

Also maintains backward-compatibility references for existing UI listeners:
  - exams/{exam_id}/rubric/approved
  - exams/{exam_id}/results/{hall_ticket}
"""

from __future__ import annotations

import time
import uuid
from typing import Any
from google.cloud import firestore
from google.cloud.firestore import Client

from config import get_firestore, get_storage_bucket
from models.schemas import QuestionRubric, PaperResult


class DatabaseService:
    """Centralized database service using Firebase Admin SDK."""

    @property
    def db(self) -> Client | None:
        """Get the active Firestore client."""
        return get_firestore()

    def get_db(self) -> Client | None:
        """Get the active Firestore client (method form)."""
        return self.db

    def is_connected(self) -> bool:
        """Check if Firestore client is initialized and accessible."""
        return self.db is not None

    # =========================================================
    # EXAM OPERATIONS
    # =========================================================

    def save_exam(
        self,
        exam_id: str,
        subject: str = "",
        exam_name: str = "",
        total_marks: float = 0.0,
        status: str = "CREATED",
        teacher_id: str = "default_teacher",
    ) -> dict[str, Any]:
        """Create or update an examination record."""
        client = self.db
        if not client:
            raise RuntimeError("Firestore is unavailable.")

        doc_ref = client.collection("exams").document(exam_id)
        now = time.time()

        existing = doc_ref.get()
        created_at = existing.to_dict().get("created_at", now) if existing.exists else now

        data = {
            "exam_id": exam_id,
            "subject": subject or exam_id,
            "exam_name": exam_name or exam_id,
            "total_marks": total_marks,
            "status": status,
            "teacher_id": teacher_id,
            "created_at": created_at,
            "updated_at": now,
        }
        doc_ref.set(data, merge=True)
        return data

    def get_exam(self, exam_id: str) -> dict[str, Any] | None:
        """Fetch exam document by ID."""
        client = self.db
        if not client:
            return None
        doc = client.collection("exams").document(exam_id).get()
        return doc.to_dict() if doc.exists else None

    def list_exams(self) -> list[dict[str, Any]]:
        """List all exams in the database."""
        client = self.db
        if not client:
            return []
        docs = client.collection("exams").order_by("created_at").stream()
        return [doc.to_dict() for doc in docs if doc.to_dict()]

    # =========================================================
    # QUESTION PAPER OPERATIONS
    # =========================================================

    def _question_paper_ref(self, client: Client, exam_id: str):
        return (
            client.collection("exams")
            .document(exam_id)
            .collection("question_paper")
            .document("current")
        )

    def get_question_paper(self, exam_id: str) -> dict[str, Any] | None:
        """Fetch the parsed question paper saved for an exam."""
        client = self.db
        if not client:
            return None
        doc = self._question_paper_ref(client, exam_id).get()
        return doc.to_dict() if doc.exists else None

    def save_question_paper(
        self,
        exam_id: str,
        paper: dict[str, Any],
        replace: bool = False,
        teacher_id: str = "default_teacher",
    ) -> dict[str, Any]:
        """
        Save an exam's parsed paper and exam record in one transaction, so a
        failure can never leave a partial paper. Each exam has one paper.

        Returns {"outcome": created | replaced | duplicate | conflict, "paper": dict}.
        """
        client = self.db
        if not client:
            raise RuntimeError("Firestore is unavailable.")

        exam_ref = client.collection("exams").document(exam_id)
        paper_ref = self._question_paper_ref(client, exam_id)

        @firestore.transactional
        def _write(transaction) -> dict[str, Any]:
            paper_doc = paper_ref.get(transaction=transaction)
            exam_doc = exam_ref.get(transaction=transaction)
            current = paper_doc.to_dict() if paper_doc.exists else None
            exam_data = exam_doc.to_dict() if exam_doc.exists else {}
            same_file = bool(current) and current.get("doc_fingerprint") == paper.get("doc_fingerprint")

            if current and not replace:
                return {"outcome": "duplicate" if same_file else "conflict", "paper": current}

            now = time.time()
            saved = {
                **paper,
                "exam_id": exam_id,
                "created_at": current.get("created_at", now) if current else now,
                "updated_at": now,
            }
            transaction.set(paper_ref, saved)
            transaction.set(exam_ref, {
                "exam_id": exam_id,
                "subject": exam_data.get("subject", exam_id),
                "exam_name": exam_data.get("exam_name", exam_id),
                "teacher_id": exam_data.get("teacher_id", teacher_id),
                "created_at": exam_data.get("created_at", now),
                "updated_at": now,
                "total_marks": paper.get("total_marks", 0.0),
                # The same file again must not undo an approval.
                "status": exam_data.get("status", "PROPOSED") if same_file else "PROPOSED",
                "question_paper_fingerprint": paper.get("doc_fingerprint", ""),
            }, merge=True)
            return {"outcome": "replaced" if current else "created", "paper": saved}

        return _write(client.transaction())

    # =========================================================
    # RUBRIC OPERATIONS
    # =========================================================

    def save_approved_rubric(
        self,
        exam_id: str,
        questions: list[dict[str, Any] | QuestionRubric],
        teacher_id: str = "default_teacher",
        doc_fingerprint: str = "",
        gen_fingerprint: str = "",
        total_marks: float | None = None,
    ) -> dict[str, Any]:
        """
        Persist a teacher-approved grading rubric.
        
        1. Stores versioned document under rubrics/{rubric_id}
        2. Preserves active pointer under exams/{exam_id}/rubric/approved
        3. Updates exams/{exam_id} with approved_rubric_id and CONFIGURED status
        """
        client = self.db
        if not client:
            raise RuntimeError("Firestore is unavailable.")

        # Serialize questions to dicts
        serialized_questions = []
        naive_sum = 0.0
        for q in questions:
            if hasattr(q, "model_dump"):
                q_dict = q.model_dump()
            elif isinstance(q, dict):
                q_dict = q
            else:
                q_dict = dict(q)
            serialized_questions.append(q_dict)
            naive_sum += float(q_dict.get("max_marks", 0.0))

        # Reconcile the approved maximum only from explicit question, choice, and
        # attempt metadata. Never normalize a discrepancy to the declared total.
        from services.marks_validator import validate_and_reconcile_exam_structure
        struct = validate_and_reconcile_exam_structure({
            "questions": serialized_questions,
            "declared_maximum_marks": total_marks,
        })
        if struct.validation_status == "DISCREPANCY":
            raise ValueError(
                f"Rubric maximum mismatch: declared {struct.declared_maximum_marks:g}, "
                f"but question/attempt structure totals {struct.calculated_maximum_marks:g}."
            )
        total_exam_marks = (
            float(total_marks)
            if total_marks is not None and float(total_marks) > 0
            else struct.calculated_maximum_marks or naive_sum
        )

        # Check existing version count to determine version number
        rubrics_coll = client.collection("rubrics")
        existing_versions = list(
            rubrics_coll.where("exam_id", "==", exam_id).stream()
        )
        # Approving the same rubric again keeps the existing version.
        versions = [doc.to_dict() for doc in existing_versions]
        latest = max(versions, key=lambda doc: doc.get("version", 0), default=None)
        if (
            latest
            and latest.get("questions") == serialized_questions
            and latest.get("total_marks") == total_exam_marks
            and latest.get("doc_fingerprint", "") == doc_fingerprint
        ):
            return latest

        version = len(existing_versions) + 1
        rubric_id = f"{exam_id}_v{version}"
        now = time.time()
        exam_ref = client.collection("exams").document(exam_id)
        exam_doc = exam_ref.get()

        rubric_data = {
            "rubric_id": rubric_id,
            "exam_id": exam_id,
            "version": version,
            "teacher_id": teacher_id,
            "approval_status": "APPROVED",
            "questions": serialized_questions,
            "total_marks": total_exam_marks,
            "approved_at": now,
            "created_at": now,
            "doc_fingerprint": doc_fingerprint,
            "gen_fingerprint": gen_fingerprint,
        }

        # All three writes succeed or fail together.
        batch = client.batch()

        # 1. Save top-level versioned rubric document
        batch.set(client.collection("rubrics").document(rubric_id), rubric_data)

        # 2. Maintain active path exams/{exam_id}/rubric/approved
        # Use full rubric_data (including rubric_id, version, approval_status)
        # so get_approved_rubric always returns a complete document.
        sub_data = {
            "exam_id": exam_id,
            "rubric_id": rubric_id,
            "version": version,
            "approval_status": "APPROVED",
            "approved": True,
            "approved_at": now,
            "created_at": now,
            "questions": serialized_questions,
            "total_marks": total_exam_marks,
            "doc_fingerprint": doc_fingerprint,
            "gen_fingerprint": gen_fingerprint,
        }
        batch.set(exam_ref.collection("rubric").document("approved"), sub_data)

        # 3. Update exam document with approved status
        # created_at is required: list_exams orders by it and skips documents without it.
        batch.set(exam_ref, {
            "exam_id": exam_id,
            "created_at": (exam_doc.to_dict() or {}).get("created_at", now) if exam_doc.exists else now,
            "status": "CONFIGURED",
            "approved_rubric_id": rubric_id,
            "rubric_version": version,
            "total_marks": total_exam_marks,
            "updated_at": now,
        }, merge=True)
        batch.commit()

        print(f"[DB] Approved rubric saved: {rubric_id} (Version {version}, {len(serialized_questions)} questions)")
        return rubric_data

    def get_approved_rubric(self, exam_id: str) -> dict[str, Any] | None:
        """
        Fetch the active approved rubric for an exam.

        Priority order:
        1. exams/{exam_id}/rubric/approved   (fastest, always written on approve)
        2. rubrics/ collection via exam pointer  (fallback)

        Handles legacy documents that are missing rubric_id / version fields.
        """
        client = self.db
        if not client:
            return None

        # 1. Direct sub-collection path (written by save_approved_rubric)
        approved_doc = (
            client.collection("exams")
            .document(exam_id)
            .collection("rubric")
            .document("approved")
            .get()
        )
        if approved_doc.exists:
            data = approved_doc.to_dict()
            # Validate it actually belongs to this exam and has questions
            if data.get("exam_id") == exam_id and data.get("questions"):
                # Backfill missing fields for legacy documents
                if not data.get("rubric_id"):
                    data["rubric_id"] = f"{exam_id}_v1"
                if not data.get("version"):
                    data["version"] = 1
                if not data.get("approval_status"):
                    data["approval_status"] = "APPROVED"
                if not data.get("total_marks") and data.get("questions"):
                    data["total_marks"] = sum(
                        float(q.get("max_marks", 0)) for q in data["questions"]
                    )
                return data

        # 2. Check exam document pointer → top-level rubrics collection
        exam_doc = client.collection("exams").document(exam_id).get()
        if exam_doc.exists:
            rubric_id = exam_doc.to_dict().get("approved_rubric_id")
            if rubric_id:
                r_doc = client.collection("rubrics").document(rubric_id).get()
                if r_doc.exists:
                    return r_doc.to_dict()

        # 3. Last resort: query rubrics collection directly (most recent APPROVED)
        try:
            rubric_query = (
                client.collection("rubrics")
                .where("exam_id", "==", exam_id)
                .where("approval_status", "==", "APPROVED")
                .order_by("version", direction="DESCENDING")
                .limit(1)
                .stream()
            )
            for doc in rubric_query:
                return doc.to_dict()
        except Exception as e:
            print(f"[DB] Rubric query fallback error for exam '{exam_id}': {e}")

        return None

    # =========================================================
    # SUBMISSION OPERATIONS
    # =========================================================

    def save_submission(
        self,
        exam_id: str,
        student_id: str,
        rubric_id: str = "",
        file_reference: str = "",
        status: str = "RECEIVED",
    ) -> dict[str, Any]:
        """Record an answer sheet submission record in submissions collection."""
        client = self.db
        if not client:
            raise RuntimeError("Firestore is unavailable.")

        submission_id = f"{exam_id}_{student_id}"
        now = time.time()
        sub_data = {
            "submission_id": submission_id,
            "exam_id": exam_id,
            "rubric_id": rubric_id,
            "student_id": student_id,
            "hall_ticket": student_id,
            "status": status,
            "uploaded_at": now,
            "file_reference": file_reference,
            "updated_at": now,
        }
        client.collection("submissions").document(submission_id).set(sub_data, merge=True)
        return sub_data

    # =========================================================
    # GRADING RESULT OPERATIONS
    # =========================================================

    def save_grading_result(
        self,
        exam_id: str,
        hall_ticket: str,
        paper_result: PaperResult | dict[str, Any],
        rubric_id: str = "",
    ) -> dict[str, Any]:
        """
        Persist complete evaluation results.
        Saves to:
          1. grading_results/{result_id}
          2. exams/{exam_id}/results/{hall_ticket} (for live UI listener)
        """
        client = self.db
        if not client:
            raise RuntimeError("Firestore is unavailable.")

        data = paper_result.model_dump() if hasattr(paper_result, "model_dump") else paper_result
        now = time.time()
        result_id = f"{exam_id}_{hall_ticket}"

        full_doc = {
            "result_id": result_id,
            "submission_id": f"{exam_id}_{hall_ticket}",
            "exam_id": exam_id,
            "hall_ticket": hall_ticket,
            "rubric_id": rubric_id,
            "created_at": now,
            "updated_at": now,
            **data,
        }

        # 1. Top-level grading_results collection
        client.collection("grading_results").document(result_id).set(full_doc)

        # 2. Existing nested path for frontend live query
        client.collection("exams").document(exam_id).collection("results").document(hall_ticket).set(full_doc)

        # 3. Update submission status to EVALUATED
        client.collection("submissions").document(f"{exam_id}_{hall_ticket}").set({
            "status": (
                "NEEDS_REVIEW"
                if data.get("status") == "NEEDS_REVIEW"
                else "EVALUATED"
            ),
            "score": data.get("total_score", 0.0),
            "max_score": data.get("max_total", 0.0),
            "updated_at": now,
        }, merge=True)

        print(f"[DB] Graded paper persisted: {result_id} (Score: {data.get('total_score')}/{data.get('max_total')})")
        return full_doc

    def get_grading_result(self, exam_id: str, hall_ticket: str) -> dict[str, Any] | None:
        """Fetch grading result for one student."""
        client = self.db
        if not client:
            return None
        doc = (
            client.collection("exams")
            .document(exam_id)
            .collection("results")
            .document(hall_ticket)
            .get()
        )
        if doc.exists:
            return doc.to_dict()
        
        # Fallback to grading_results collection
        res_doc = client.collection("grading_results").document(f"{exam_id}_{hall_ticket}").get()
        return res_doc.to_dict() if res_doc.exists else None

    def list_exam_results(self, exam_id: str) -> list[dict[str, Any]]:
        """List all graded results for an exam."""
        client = self.db
        if not client:
            return []
        docs = (
            client.collection("exams")
            .document(exam_id)
            .collection("results")
            .stream()
        )
        return [doc.to_dict() for doc in docs if doc.to_dict()]


# Singleton instance
db_service = DatabaseService()
