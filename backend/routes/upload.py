"""
AutoGrade — Upload Routes

POST /upload — Bulk answer sheet upload + queue processing
"""

from __future__ import annotations

import json
import hashlib
import logging
import os
import time
from pathlib import Path
from io import BytesIO

from fastapi import (
    APIRouter, UploadFile, File, Form, HTTPException
)
from fastapi.responses import JSONResponse
from PIL import Image

import config
from config import get_firestore, get_storage_bucket
from services.gemini_sheet import grade_sheet
from models.schemas import QuestionRubric, RubricCriterion, StudentMeta
from pipeline import process_paper
from services.ocr import extract_student_meta
from services.router import model_router
from services.queue_manager import queue_manager, QueueJob
from services.db import db_service
from services.timing import log_timing, timed_stage


router = APIRouter(tags=["Upload"])
_logger = logging.getLogger(__name__)


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
    rubric_list, total_marks, rubric_version, rubric_fingerprint = _load_rubric_bundle(exam_id)
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
            raise HTTPException(400, f"Uploaded file at position {idx + 1} has no filename.")

        ext = Path(file.filename).suffix.lower()
        mime = ALLOWED_MIME.get(ext)
        if not mime:
            raise HTTPException(400, f"Unsupported answer-sheet file type: {ext or 'unknown'}.")

        # Determine student info
        if idx < len(meta_list) and isinstance(meta_list[idx], dict):
            hall_ticket = meta_list[idx].get("hall_ticket", f"UNKNOWN_{idx}")
            name = meta_list[idx].get("name", "")
        else:
            hall_ticket = f"STUDENT_{idx + 1}"
            name = ""

        with timed_stage(
            "pdf_upload_validation",
            detail=f"Reading and validating {file.filename}",
        ):
            file_bytes = await file.read()
            if not file_bytes:
                raise HTTPException(400, f"Answer-sheet file '{file.filename}' is empty.")
            _validate_answer_sheet_bytes(file_bytes, ext)
        file_sha256 = hashlib.sha256(file_bytes).hexdigest()

        queue_manager.enqueue(
            exam_id=exam_id,
            hall_ticket=hall_ticket,
            student_name=name,
            file_bytes=file_bytes,
            file_ext=ext,
            mime_type=mime,
            original_filename=file.filename,
            rubric_fingerprint=rubric_fingerprint,
            rubric_version=rubric_version,
            rubric_total_marks=total_marks,
            rubric_data=[question.model_dump() for question in rubric_list],
        )
        log_timing(
            "answer_sheet_upload",
            upload_started_at,
            exam_id=exam_id,
            byte_count=len(file_bytes),
            file_sha256=file_sha256,
        )

        queued += 1

    # Start sequential background worker
    queue_manager.start_worker(_execute_grading_job)

    return {
        "queued": queued,
        "exam_id": exam_id,
        "status": "QUEUED",
        "message": f"Successfully queued {queued} answer sheets for sequential local evaluation."
    }


# =========================================================
# JOB PROCESSOR
# =========================================================

def _execute_grading_job(job: QueueJob):
    """Execute evaluation for one student paper from the queue."""
    file_path = job.file_path
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Spooled answer sheet not found: {file_path}")

    def update_stage(stage: str, detail: str = "") -> None:
        queue_manager.update_job_stage(job.job_id, stage, detail)

    def check_hash(file_bytes: bytes) -> None:
        actual_hash = hashlib.sha256(file_bytes).hexdigest()
        if job.file_sha256 and actual_hash != job.file_sha256:
            raise RuntimeError("Spooled answer-sheet hash changed after upload.")

    file_bytes = Path(file_path).read_bytes()
    check_hash(file_bytes)

    rubric_list: list[QuestionRubric]
    rubric_total_marks = job.rubric_total_marks
    if job.rubric_data:
        rubric_list = [QuestionRubric(**question) for question in job.rubric_data]
    else:
        rubric_list, rubric_total_marks, _, _ = _load_rubric_bundle(job.exam_id)
    if not rubric_list:
        raise RuntimeError(f"No approved rubric is available for exam '{job.exam_id}'.")

    with timed_stage(
        "firebase_storage",
        job_id=job.job_id,
        on_stage=update_stage,
        detail="Saving the uploaded source sheet",
    ):
        _store_to_firebase(
            job.exam_id,
            job.hall_ticket,
            job.original_filename or Path(file_path).name,
            file_bytes,
        )

    with timed_stage(
        "submission_record",
        job_id=job.job_id,
        on_stage=update_stage,
        detail="Recording the evaluation job",
    ):
        if db_service.is_connected():
            db_service.save_submission(
                exam_id=job.exam_id,
                student_id=job.hall_ticket,
                file_reference=(
                    f"exams/{job.exam_id}/sheets/{job.hall_ticket}/"
                    f"{job.original_filename or Path(file_path).name}"
                ),
                status="EVALUATING",
            )

    if config.GRADING_ENGINE == "gemini":
        try:
            result = grade_sheet(
                exam_id=job.exam_id,
                hall_ticket=job.hall_ticket,
                student_name=job.student_name,
                file_bytes=file_bytes,
                extension=job.file_ext.lower(),
                rubric_list=rubric_list,
                source_file_reference=(
                    f"exams/{job.exam_id}/sheets/{job.hall_ticket}/"
                    f"{job.original_filename or Path(file_path).name}"
                ),
                rubric_version=job.rubric_version,
                total_marks=rubric_total_marks or None,
                on_stage=update_stage,
            )
            db_service.save_grading_result(job.exam_id, job.hall_ticket, result)
            return result
        except Exception as error:
            _update_submission_failure(job, error)
            raise

    # Render the source once to stable page images. Both local and cloud
    # inference now pass through the shared Model Router; the uploaded PDF is
    # not separately re-uploaded to Gemini.
    try:
        if job.file_ext.lower() == ".pdf":
            image_parts = model_router.ollama.render_pdf_to_images(
                file_path,
                max_pages=None,
                dpi=150,
                source_hash=job.file_sha256,
                job_id=job.job_id,
                on_stage=update_stage,
            )
        else:
            with timed_stage(
                "image_preprocessing",
                job_id=job.job_id,
                on_stage=update_stage,
                detail="Normalizing the uploaded image for handwriting recognition",
            ):
                image_parts = [model_router.ollama.prepare_image_payload(file_path)]
    except Exception as error:
        _update_submission_failure(job, error)
        raise RuntimeError(
            "Could not render every source page for OCR evidence; evaluation was stopped."
        ) from None

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
        except Exception as error:
            _logger.warning(
                "Student metadata extraction failed for job %s (%s)",
                job.job_id,
                type(error).__name__,
            )

    try:
        return process_paper(
            exam_id=job.exam_id,
            hall_ticket=hall_ticket,
            student_name=student_name,
            image_parts=image_parts,
            rubric_list=rubric_list,
            image_bytes_list=[file_bytes],
            ocr_image_parts=image_parts,
            source_file_reference=(
                f"exams/{job.exam_id}/sheets/{job.hall_ticket}/"
                f"{job.original_filename or Path(file_path).name}"
            ),
            approved_rubric_version=job.rubric_version,
            approved_total_marks=rubric_total_marks or None,
            job_id=job.job_id,
            on_stage=update_stage,
        )
    except Exception as error:
        _update_submission_failure(job, error)
        raise


def _update_submission_failure(job: QueueJob, error: Exception) -> None:
    if not db_service.is_connected():
        return
    teacher_review_required = bool(
        getattr(error, "requires_teacher_validation", False)
        or getattr(error, "teacher_validation_required", False)
    )
    status = (
        "NEEDS_REVIEW" if teacher_review_required
        else "TIMED_OUT" if "timeout" in str(error).lower()
        else "FAILED"
    )
    try:
        db_service.save_submission(
            exam_id=job.exam_id,
            student_id=job.hall_ticket,
            file_reference=(
                f"exams/{job.exam_id}/sheets/{job.hall_ticket}/"
                f"{job.original_filename or Path(job.file_path).name}"
            ),
            status=status,
        )
    except Exception:
        _logger.exception("Could not persist failed submission status for job %s", job.job_id)


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
    Accept one student answer sheet for persistent background evaluation.
    """
    request_started_at = time.perf_counter()
    if not file.filename:
        raise HTTPException(400, "Answer sheet file is required.")

    ext = Path(file.filename).suffix.lower()
    mime = ALLOWED_MIME.get(ext)
    if not mime:
        raise HTTPException(400, "Answer sheet must be a PDF, JPG, or PNG document.")

    if not exam_id.strip() or not hall_ticket.strip():
        raise HTTPException(400, "Exam ID and hall ticket are required.")

    upload_started_at = time.perf_counter()
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(400, "Uploaded answer sheet file is empty.")
    page_count = _validate_answer_sheet_bytes(file_bytes, ext)
    file_sha256 = hashlib.sha256(file_bytes).hexdigest()
    log_timing(
        "pdf_upload_validation",
        upload_started_at,
        exam_id=exam_id.strip(),
        byte_count=len(file_bytes),
        page_count=page_count,
        file_sha256=file_sha256,
    )

    rubric_started_at = time.perf_counter()
    rubric_list, total_marks, rubric_version, rubric_fingerprint = _load_rubric_bundle(
        exam_id.strip()
    )
    log_timing(
        "approved_rubric_retrieval",
        rubric_started_at,
        exam_id=exam_id.strip(),
        rubric_version=rubric_version,
        question_count=len(rubric_list),
    )
    if not rubric_list:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Evaluation blocked: No approved rubric exists for exam '{exam_id}'. "
                f"You must generate and approve the grading rubric before evaluating answer sheets."
            ),
        )

    job = queue_manager.enqueue(
        exam_id=exam_id.strip(),
        hall_ticket=hall_ticket.strip(),
        student_name=student_name.strip() or hall_ticket.strip(),
        file_bytes=file_bytes,
        file_ext=ext,
        mime_type=mime,
        original_filename=file.filename,
        rubric_fingerprint=rubric_fingerprint,
        rubric_version=rubric_version,
        rubric_total_marks=total_marks,
        rubric_data=[question.model_dump() for question in rubric_list],
    )
    queue_manager.start_worker(_execute_grading_job)
    log_timing(
        "evaluation_job_acceptance",
        request_started_at,
        job_id=job.job_id,
        status=job.status,
        file_sha256=file_sha256,
    )
    return JSONResponse(
        status_code=202,
        content={
            "success": True,
            "job_id": job.job_id,
            "exam_id": job.exam_id,
            "hall_ticket": job.hall_ticket,
            "status": job.status,
            "current_stage": job.current_stage,
            "stage_detail": job.stage_detail or "Evaluation queued",
        },
    )


@router.get("/evaluate/jobs/{job_id}")
def get_evaluation_job(job_id: str):
    """Return actual stage/status and, when ready, the persisted full result."""
    job = queue_manager.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Evaluation job was not found.")
    return {
        "job_id": job.job_id,
        "exam_id": job.exam_id,
        "hall_ticket": job.hall_ticket,
        "status": job.status,
        "current_stage": job.current_stage,
        "stage_detail": job.stage_detail,
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
        "error_code": job.error_code,
        "error": job.error_message,
        "score": job.score if job.result is not None and job.status in {"COMPLETE", "NEEDS_REVIEW"} else None,
        "max_score": job.max_score if job.result is not None and job.status in {"COMPLETE", "NEEDS_REVIEW"} else None,
        "result": job.result if job.status in {"COMPLETE", "NEEDS_REVIEW"} else None,
        "updated_at": job.updated_at,
    }


@router.post("/evaluate/jobs/{job_id}/retry")
def retry_evaluation_job(job_id: str):
    """Retry a failed job within its bounded attempt limit."""
    try:
        job = queue_manager.retry_job(job_id)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(410, str(error)) from error
    if job is None:
        raise HTTPException(404, "Evaluation job was not found.")
    queue_manager.start_worker(_execute_grading_job)
    return {
        "job_id": job.job_id,
        "status": job.status,
        "current_stage": job.current_stage,
        "stage_detail": job.stage_detail,
    }


@router.post("/evaluate/jobs/{job_id}/cancel")
def cancel_evaluation_job(job_id: str):
    """Cancel a queued evaluation or request cancellation at the next stage boundary."""
    existing = queue_manager.get_job(job_id)
    if existing is None:
        raise HTTPException(404, "Evaluation job was not found.")
    if existing.status in {"COMPLETE", "NEEDS_REVIEW", "FAILED", "TIMED_OUT", "CANCELLED"}:
        raise HTTPException(409, f"Evaluation job is already {existing.status.lower()}.")
    job = queue_manager.request_cancel(job_id)
    return {
        "job_id": job.job_id,
        "status": job.status,
        "current_stage": job.current_stage,
        "stage_detail": job.stage_detail,
    }


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
        "timed_out": sum(1 for j in jobs if j.status == "TIMED_OUT"),
        "cancelled": sum(1 for j in jobs if j.status == "CANCELLED"),
        "jobs": [
            {
                "job_id": j.job_id,
                "hall_ticket": j.hall_ticket,
                "student_name": j.student_name,
                "status": j.status,
                "current_stage": j.current_stage,
                "stage_detail": j.stage_detail,
                "attempt_count": j.attempt_count,
                "max_attempts": j.max_attempts,
                "score": j.score,
                "max_score": j.max_score,
                "error_code": j.error_code,
                "error": j.error_message,
                "updated_at": j.updated_at,
            }
            for j in jobs
        ],
    }


@router.post("/queue/{exam_id}/retry/{hall_ticket}")
def retry_failed_job(exam_id: str, hall_ticket: str):
    """Retry an individual failed student job without restarting completed ones."""
    matching = [
        job for job in queue_manager.get_exam_jobs(exam_id)
        if job.hall_ticket == hall_ticket
    ]
    if not matching:
        raise HTTPException(404, f"No evaluation job found for {hall_ticket}.")
    job = max(matching, key=lambda candidate: candidate.created_at)
    try:
        retried = queue_manager.retry_job(job.job_id)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(410, str(error)) from error
    queue_manager.start_worker(_execute_grading_job)
    return {"success": True, "job_id": retried.job_id, "status": retried.status}


# =========================================================
# HELPERS
# =========================================================

def _load_rubric(exam_id: str) -> list[QuestionRubric]:
    """Compatibility helper for routes that only need approved questions."""
    questions, _, _, _ = _load_rubric_bundle(exam_id)
    return questions


def _load_rubric_bundle(
    exam_id: str,
) -> tuple[list[QuestionRubric], float, str, str]:
    """Load and fingerprint the complete approved rubric snapshot."""
    if not db_service.is_connected():
        raise HTTPException(503, "Firestore is unavailable; approved rubric cannot be loaded.")
    try:
        rubric_doc = db_service.get_approved_rubric(exam_id)
        if not rubric_doc:
            return [], 0.0, "", ""

        questions = rubric_doc.get("questions", [])
        rubric_list = [
            QuestionRubric(
                question_number=q["question_number"],
                question_text=q["question_text"],
                max_marks=float(q["max_marks"]),
                question_type=q.get("question_type"),
                criteria=[
                    RubricCriterion(
                        description=c.get("description", ""),
                        max_marks=float(c.get("max_marks", 1.0)),
                    )
                    for c in q.get("criteria", [])
                ],
                sub_part=str(q.get("sub_part", "")),
                choice_group=str(q.get("choice_group", "")),
                is_or_choice=bool(q.get("is_or_choice", False)),
                question_format=str(q.get("question_format", "")),
                options=q.get("options") or [],
                correct_option=str(q.get("correct_option", "")),
                section=str(q.get("section", q.get("section_id", ""))),
                section_attempt_count=q.get(
                    "section_attempt_count",
                    q.get("attempt_count"),
                ),
                section_max_marks=q.get(
                    "section_max_marks",
                    q.get("section_maximum_marks"),
                ),
                attempt_instruction=str(q.get("attempt_instruction", "")),
            )
            for q in questions
        ]
        total_marks_value = rubric_doc.get("total_marks")
        from services.marks_validator import validate_and_reconcile_exam_structure
        structure = validate_and_reconcile_exam_structure({
            "questions": questions,
            "declared_maximum_marks": total_marks_value,
        })
        if structure.validation_status == "DISCREPANCY":
            raise HTTPException(
                409,
                detail=(
                    f"Approved rubric for exam '{exam_id}' is inconsistent: its declared "
                    f"maximum is {structure.declared_maximum_marks:g}, but its preserved "
                    f"question/attempt structure totals {structure.calculated_maximum_marks:g}. "
                    "Reconcile and re-approve the rubric before grading."
                ),
            )
        if total_marks_value is None or float(total_marks_value) <= 0:
            total_marks_value = structure.calculated_maximum_marks
        total_marks = float(total_marks_value)
        if total_marks <= 0:
            raise ValueError(f"Approved rubric for exam '{exam_id}' has no positive total marks.")
        fingerprint_payload = {
            "version": rubric_doc.get("version", 1),
            "total_marks": total_marks,
            "questions": [question.model_dump() for question in rubric_list],
        }
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return (
            rubric_list,
            total_marks,
            str(rubric_doc.get("version", "1")),
            fingerprint,
        )
    except HTTPException:
        raise
    except Exception as error:
        _logger.exception("Could not load approved rubric for exam %s", exam_id)
        raise HTTPException(503, f"Could not load the approved rubric: {error}") from error


def _validate_answer_sheet_bytes(file_bytes: bytes, extension: str) -> int:
    """Validate the actual document and return its page count."""
    try:
        if extension == ".pdf":
            import fitz
            with fitz.open(stream=file_bytes, filetype="pdf") as document:
                if document.page_count < 1:
                    raise ValueError("PDF contains no pages.")
                return document.page_count

        with Image.open(BytesIO(file_bytes)) as image:
            image.verify()
        return 1
    except Exception as error:
        raise HTTPException(400, f"Could not read the uploaded answer sheet: {error}") from error


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
        _logger.info("Stored answer-sheet source at %s", blob.name)

    except Exception as error:
        _logger.exception("Could not store answer sheet in Firebase Storage")
        raise RuntimeError(f"Firebase Storage upload failed: {error}") from error


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
