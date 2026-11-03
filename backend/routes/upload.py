"""
AutoGrade — Upload Routes

POST /upload — Bulk answer sheet upload + queue processing
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from fastapi import (
    APIRouter, UploadFile, File, Form, HTTPException, BackgroundTasks
)

from config import get_gemini_client, get_firestore, get_storage_bucket, AI_PROVIDER
from models.schemas import QuestionRubric, RubricCriterion, StudentMeta
from pipeline import process_paper
from services.ocr import extract_student_meta
from services.router import model_router
from services.queue_manager import queue_manager, QueueJob
from services.db import db_service


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
    if not rubric_list:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot evaluate answer sheets: No approved rubric found for exam '{exam_id}'. "
                f"Please generate and approve the grading rubric before evaluating student answer sheets."
            ),
        )

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

        # Store to Firebase Storage if available
        _store_to_firebase(exam_id, hall_ticket, file.filename, file_bytes)

        # Register submission record in Firestore
        try:
            if db_service.is_connected():
                db_service.save_submission(
                    exam_id=exam_id,
                    student_id=hall_ticket,
                    file_reference=f"exams/{exam_id}/sheets/{hall_ticket}/{file.filename}",
                    status="QUEUED",
                )
        except Exception as e:
            print(f"[Upload] Submission record note: {e}")

        # Enqueue into persistent batch manager
        job = queue_manager.enqueue(
            exam_id=exam_id,
            hall_ticket=hall_ticket,
            student_name=name,
            file_bytes=file_bytes,
            file_ext=ext,
            mime_type=mime,
        )

        queued += 1

    # Start sequential background worker
    queue_manager.start_worker(
        lambda job: _execute_grading_job(job, rubric_list)
    )

    return {
        "queued": queued,
        "exam_id": exam_id,
        "status": "QUEUED",
        "message": f"Successfully queued {queued} answer sheets for sequential local evaluation."
    }


# =========================================================
# JOB PROCESSOR
# =========================================================

def _execute_grading_job(job: QueueJob, rubric_list: list[QuestionRubric]) -> tuple[str, float, float]:
    """Execute evaluation for one student paper from the queue."""
    file_path = job.file_path
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Spooled answer sheet not found: {file_path}")

    file_bytes = Path(file_path).read_bytes()

    if AI_PROVIDER == "ollama":
        # -------------------------------------------------
        # LOCAL OLLAMA PATH
        # -------------------------------------------------
        if job.file_ext.lower() == ".pdf":
            image_parts = model_router.ollama.render_pdf_to_images(file_path, max_pages=6, dpi=120)
        else:
            image_parts = [file_path]

        # Auto-read student meta if placeholder
        hall_ticket = job.hall_ticket
        student_name = job.student_name
        _is_placeholder = (
            not hall_ticket
            or hall_ticket.startswith("STUDENT_")
            or hall_ticket.startswith("HT_")
            or not student_name
            or student_name.startswith("Student ")
        )
        if _is_placeholder and image_parts:
            try:
                meta = extract_student_meta(image_parts[:1])
                if meta.get("hall_ticket"):
                    hall_ticket = meta["hall_ticket"]
                if meta.get("student_name"):
                    student_name = meta["student_name"]
            except Exception as e:
                print(f"[Upload] Meta extraction skip: {e}")

        result = process_paper(
            exam_id=job.exam_id,
            hall_ticket=hall_ticket,
            student_name=student_name,
            image_parts=image_parts,
            rubric_list=rubric_list,
            image_bytes_list=[file_bytes],
        )

        return result.status, result.total_score, result.max_total

    else:
        # -------------------------------------------------
        # PRESERVED GEMINI CLOUD PATH
        # -------------------------------------------------
        client = get_gemini_client()
        if not client:
            raise RuntimeError("Gemini client not initialized.")

        uploaded = client.files.upload(
            file=file_path,
            config={"mime_type": job.mime_type}
        )

        result = process_paper(
            exam_id=job.exam_id,
            hall_ticket=job.hall_ticket,
            student_name=job.student_name,
            image_parts=[uploaded],
            rubric_list=rubric_list,
            image_bytes_list=[file_bytes],
        )

        return result.status, result.total_score, result.max_total


# =========================================================
# POST /evaluate (Single Student Direct Evaluation)
# =========================================================

@router.post("/evaluate")
async def evaluate_single_student(
    exam_id: str = Form(...),
    hall_ticket: str = Form(...),
    file: UploadFile = File(...),
    student_name: str = Form(""),
):
    """
    Direct synchronous evaluation of ONE student answer sheet against the
    previously approved and persisted Firestore rubric.
    """
    if not file.filename:
        raise HTTPException(400, "Answer sheet file is required.")

    ext = Path(file.filename).suffix.lower()
    mime = ALLOWED_MIME.get(ext)
    if not mime:
        raise HTTPException(400, "Answer sheet must be a PDF, JPG, or PNG document.")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(400, "Uploaded answer sheet file is empty.")

    # 1. Verify that approved rubric exists in Firestore
    rubric_list = _load_rubric(exam_id)
    if not rubric_list:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Evaluation blocked: No approved rubric exists for exam '{exam_id}'. "
                f"You must generate and approve the grading rubric before evaluating answer sheets."
            ),
        )

    # 2. Store to Firebase Storage if available
    _store_to_firebase(exam_id, hall_ticket, file.filename, file_bytes)

    # 3. Save submission record in Firestore
    try:
        if db_service.is_connected():
            db_service.save_submission(
                exam_id=exam_id,
                student_id=hall_ticket,
                file_reference=f"exams/{exam_id}/sheets/{hall_ticket}/{file.filename}",
                status="EVALUATING",
            )
    except Exception as e:
        print(f"[Evaluate] Submission note: {e}")

    # 4. Spool temporarily for image extraction
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        if AI_PROVIDER == "ollama":
            if ext == ".pdf":
                image_parts = model_router.ollama.render_pdf_to_images(tmp_path, max_pages=6, dpi=120)
            else:
                image_parts = [tmp_path]
        else:
            client = get_gemini_client()
            if not client:
                raise RuntimeError("Gemini client not initialized.")
            uploaded = client.files.upload(file=tmp_path, config={"mime_type": mime})
            image_parts = [uploaded]

        # 5. Execute full evaluation pipeline
        result = process_paper(
            exam_id=exam_id,
            hall_ticket=hall_ticket,
            student_name=student_name or hall_ticket,
            image_parts=image_parts,
            rubric_list=rubric_list,
            image_bytes_list=[file_bytes],
        )

        return {
            "success": True,
            "exam_id": exam_id,
            "hall_ticket": hall_ticket,
            "student_name": student_name or hall_ticket,
            "result": result.model_dump(),
        }

    finally:
        Path(tmp_path).unlink(missing_ok=True)


# =========================================================
# QUEUE STATUS & RETRY ROUTES
# =========================================================

@router.get("/queue/{exam_id}")
def get_queue_status(exam_id: str):
    """Retrieve full batch queue status for an examination."""
    jobs = queue_manager.get_exam_jobs(exam_id)
    return {
        "exam_id": exam_id,
        "total_jobs": len(jobs),
        "pending": sum(1 for j in jobs if j.status == "PENDING"),
        "processing": sum(1 for j in jobs if j.status == "PROCESSING"),
        "completed": sum(1 for j in jobs if j.status == "COMPLETE"),
        "needs_review": sum(1 for j in jobs if j.status == "NEEDS_REVIEW"),
        "failed": sum(1 for j in jobs if j.status == "FAILED"),
        "jobs": [
            {
                "job_id": j.job_id,
                "hall_ticket": j.hall_ticket,
                "student_name": j.student_name,
                "status": j.status,
                "score": j.score,
                "max_score": j.max_score,
                "error": j.error_message,
                "updated_at": j.updated_at,
            }
            for j in jobs
        ],
    }


@router.post("/queue/{exam_id}/retry/{hall_ticket}")
def retry_failed_job(exam_id: str, hall_ticket: str):
    """Retry an individual failed student job without restarting completed ones."""
    job_id = f"{exam_id}_{hall_ticket}"
    job = queue_manager.get_job(job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id} not found.")

    queue_manager.update_job_status(job_id, "PENDING", error_message="")
    return {"success": True, "message": f"Job {job_id} reset to PENDING for retry."}


# =========================================================
# HELPERS
# =========================================================

def _load_rubric(exam_id: str) -> list[QuestionRubric]:
    """Load approved rubric from Firestore using DatabaseService."""
    try:
        rubric_doc = db_service.get_approved_rubric(exam_id)
        if not rubric_doc:
            print(f"[Upload] No approved rubric found in Firestore for exam '{exam_id}'.")
            return []

        questions = rubric_doc.get("questions", [])
        return [
            QuestionRubric(
                question_number=q["question_number"],
                question_text=q["question_text"],
                max_marks=float(q["max_marks"]),
                criteria=[
                    RubricCriterion(
                        description=c.get("description", ""),
                        max_marks=float(c.get("max_marks", 1.0)),
                    )
                    for c in q.get("criteria", [])
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
