"""
AutoGrade — Upload Routes

POST /upload — Bulk answer sheet upload + queue processing
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fastapi import (
    APIRouter, UploadFile, File, Form, HTTPException, BackgroundTasks
)

from config import get_gemini_client, get_firestore, get_storage_bucket
from models.schemas import QuestionRubric, RubricCriterion, StudentMeta
from pipeline import process_paper


router = APIRouter(tags=["Upload"])


ALLOWED_MIME = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}


# =========================================================
# POST /upload
# =========================================================

@router.post("/upload")
async def upload_answer_sheets(
    background_tasks: BackgroundTasks,
    exam_id: str = Form(...),
    files: list[UploadFile] = File(...),
    student_meta: str = Form("[]"),
):
    """
    Upload one or more answer sheet images/PDFs.
    Stores to Firebase Storage, queues grading in background.
    """

    # Parse student metadata
    try:
        meta_list = json.loads(student_meta)
    except json.JSONDecodeError:
        meta_list = []

    # Load approved rubric from Firestore (or accept from meta)
    rubric_list = _load_rubric(exam_id)

    queued = 0

    for idx, file in enumerate(files):
        if not file.filename:
            continue

        ext = Path(file.filename).suffix.lower()
        mime = ALLOWED_MIME.get(ext)
        if not mime:
            continue

        file_bytes = await file.read()
        if not file_bytes:
            continue

        # Determine student info
        if idx < len(meta_list) and isinstance(meta_list[idx], dict):
            hall_ticket = meta_list[idx].get("hall_ticket", f"UNKNOWN_{idx}")
            name = meta_list[idx].get("name", "")
        else:
            hall_ticket = f"STUDENT_{idx + 1}"
            name = ""

        # Store to Firebase Storage
        _store_to_firebase(exam_id, hall_ticket, file.filename, file_bytes)

        # Update queue status in Firestore
        _update_queue(exam_id, hall_ticket, "PROCESSING")

        # Queue background processing
        background_tasks.add_task(
            _process_in_background,
            exam_id=exam_id,
            hall_ticket=hall_ticket,
            student_name=name,
            file_bytes=file_bytes,
            mime_type=mime,
            file_ext=ext,
            rubric_list=rubric_list,
        )

        queued += 1

    return {
        "queued": queued,
        "exam_id": exam_id,
    }


# =========================================================
# BACKGROUND PROCESSING
# =========================================================

async def _process_in_background(
    exam_id: str,
    hall_ticket: str,
    student_name: str,
    file_bytes: bytes,
    mime_type: str,
    file_ext: str,
    rubric_list: list[QuestionRubric],
):
    """Process a single answer sheet in the background."""
    try:
        # Save temp file for Gemini upload
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=file_ext
        ) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        try:
            client = get_gemini_client()
            uploaded = client.files.upload(
                file=tmp_path,
                config={"mime_type": mime_type}
            )

            result = process_paper(
                exam_id=exam_id,
                hall_ticket=hall_ticket,
                student_name=student_name,
                image_parts=[uploaded],
                rubric_list=rubric_list,
                image_bytes_list=[file_bytes],
            )

            _update_queue(exam_id, hall_ticket, result.status)

        finally:
            Path(tmp_path).unlink(missing_ok=True)

    except Exception as error:
        print(f"[Upload] Background processing error for {hall_ticket}: {error}")
        _update_queue(exam_id, hall_ticket, "ERROR")


# =========================================================
# HELPERS
# =========================================================

def _load_rubric(exam_id: str) -> list[QuestionRubric]:
    """Load approved rubric from Firestore."""
    try:
        db = get_firestore()
        if db is None:
            return []

        doc = (
            db.collection("exams")
            .document(exam_id)
            .collection("rubric")
            .document("approved")
            .get()
        )

        if not doc.exists:
            return []

        data = doc.to_dict()
        questions = data.get("questions", [])

        return [
            QuestionRubric(
                question_number=q["question_number"],
                question_text=q["question_text"],
                max_marks=q["max_marks"],
                criteria=[
                    RubricCriterion(**c) for c in q.get("criteria", [])
                ],
            )
            for q in questions
        ]

    except Exception as error:
        print(f"[Upload] Rubric load error: {error}")
        return []


def _store_to_firebase(
    exam_id: str,
    hall_ticket: str,
    filename: str,
    file_bytes: bytes,
):
    """Upload answer sheet image to Firebase Storage."""
    try:
        bucket = get_storage_bucket()
        if bucket is None:
            return

        blob = bucket.blob(
            f"exams/{exam_id}/sheets/{hall_ticket}/{filename}"
        )
        blob.upload_from_string(file_bytes)
        print(f"[Upload] Stored: {blob.name}")

    except Exception as error:
        print(f"[Upload] Storage error: {error}")


def _update_queue(exam_id: str, hall_ticket: str, status: str):
    """Update queue status in Firestore."""
    try:
        db = get_firestore()
        if db is None:
            return

        db.collection("exams").document(exam_id).collection(
            "queue"
        ).document(hall_ticket).set(
            {"hall_ticket": hall_ticket, "status": status},
            merge=True,
        )
    except Exception as error:
        print(f"[Upload] Queue update error: {error}")
