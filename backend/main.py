"""
AutoGrade — FastAPI Application Entry Point

Wires all modular routes:
- /exam    : Exam setup and rubric approval
- /upload  : Bulk answer sheet upload and queue processing
- /results : Fetching graded paper results from Firestore
- /review  : Teacher confirmation and manual score overrides
- /export  : Excel spreadsheet export
"""

from __future__ import annotations

import json
import hashlib
import os
import tempfile
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import route modules
from routes import exam, upload, results, review, export
from config import (
    GEMINI_MODEL,
    get_firestore,
    AI_PROVIDER,
    OLLAMA_SIMPLE_MODEL,
    OLLAMA_COMPLEX_MODEL,
    OLLAMA_PAPER_READER_MODEL,
)
from services.router import model_router


app = FastAPI(
    title="AutoGrade AI Grading System",
    description="Automated AI-powered grading system for handwritten answer sheets with multi-agent verification, local Ollama dual-model routing, and RAG.",
    version="2.0.0",
)


# =========================================================
# CORS MIDDLEWARE
# =========================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins for local dev & Firebase hosting
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# REGISTER ROUTERS
# =========================================================

app.include_router(exam.router)
app.include_router(upload.router)
app.include_router(results.router)
app.include_router(review.router)
app.include_router(export.router)


# =========================================================
# ROOT & HEALTH CHECK
# =========================================================

@app.get("/")
def root():
    """Service status, active AI provider, and endpoint overview."""
    db = get_firestore()
    ollama_health = model_router.ollama.check_health()
    return {
        "service": "AutoGrade AI Grading System",
        "status": "online",
        "version": "2.0.0",
        "ai_provider": AI_PROVIDER,
        "ollama": {
            "online": ollama_health.get("online", False),
            "simple_model": OLLAMA_SIMPLE_MODEL,
            "complex_model": OLLAMA_COMPLEX_MODEL,
            "installed_models": ollama_health.get("models", []),
        },
        "gemini_model": GEMINI_MODEL,
        "firestore_connected": db is not None,
        "endpoints": {
            "exam_setup": "POST /exam/setup",
            "rubric_approve": "POST /exam/rubric/approve",
            "upload_sheets": "POST /upload",
            "exam_results": "GET /results/{exam_id}",
            "student_result": "GET /results/{exam_id}/{hall_ticket}",
            "review_confirm": "POST /review/confirm",
            "review_edit": "POST /review/edit",
            "export_excel": "GET /export/{exam_id}",
            "generate_rubric": "POST /generate-rubric",
            "process_answer_sheet": "POST /process-answer-sheet",
        },
    }


@app.get("/health")
def health_check():
    """Health check for local dev and load balancers."""
    return {"status": "healthy", "provider": AI_PROVIDER}


# =========================================================
# LEGACY / ROOT UI COMPATIBILITY ROUTES
# =========================================================

@app.post("/generate-rubric")
async def generate_rubric(
    question_paper: UploadFile = File(...),
    textbook: UploadFile | None = File(None)
):
    """
    Generate rubric for root UI (app.js) using the local ModelRouter.
    Routes to Gemma 4 E4B for visual scanned question papers.
    """
    if not question_paper.filename:
        raise HTTPException(400, "Question paper is required.")

    ext = Path(question_paper.filename).suffix.lower()
    if ext not in {".pdf", ".jpg", ".jpeg", ".png"}:
        raise HTTPException(400, "Question paper must be PDF, JPG, or PNG.")

    q_bytes = await question_paper.read()
    if not q_bytes:
        raise HTTPException(400, "Question paper is empty.")

    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(q_bytes)
        q_path = tmp.name

    try:
        paper_images = []
        if ext == ".pdf":
            paper_images = model_router.ollama.render_pdf_to_images(q_path, max_pages=4, dpi=120)
        else:
            paper_images = [q_path]

        prompt = """
You are the Rubric Generation Module of AutoGrade.
Analyze the uploaded question paper. For every question:
1. Extract the question number
2. Extract the question text accurately
3. Identify total marks (max_marks)
4. Create a clear marking rubric with criteria (each with description and max_marks)
5. The sum of all criteria max_marks MUST EXACTLY EQUAL total_marks.

Return JSON in this format:
{
  "questions": [
    {
      "question_number": 1,
      "question_text": "...",
      "question_type": "THEORY",
      "max_marks": 5.0,
      "criteria": [
        {"description": "...", "max_marks": 2.5},
        {"description": "...", "max_marks": 2.5}
      ]
    }
  ]
}
"""
        res = model_router.generate(
            task_type="rubric_generation",
            prompt=prompt,
            images=paper_images,
            format_json=True,
            force_model=OLLAMA_PAPER_READER_MODEL,
        )

        rubric_data = res.parsed_json
        if not rubric_data or not isinstance(rubric_data, dict):
            raise HTTPException(500, f"Model returned invalid rubric: {res.text[:200]}")

        return {
            "success": True,
            "model": res.model_used,
            "routing_reason": res.routing_reason,
            "rubric": rubric_data,
        }

    finally:
        Path(q_path).unlink(missing_ok=True)


@app.post("/process-answer-sheet", status_code=202)
async def process_answer_sheet(
    answer_sheet: UploadFile = File(...),
    rubric: str = Form(""),
):
    """
    Accept an answer-sheet evaluation from the legacy root UI.
    """
    if not rubric:
        raise HTTPException(400, "Approved rubric JSON is required.")

    try:
        rubric_data = json.loads(rubric) if isinstance(rubric, str) else rubric
    except Exception:
        raise HTTPException(400, "Invalid rubric JSON.")

    if not answer_sheet.filename:
        raise HTTPException(400, "Answer sheet is required.")

    ext = Path(answer_sheet.filename).suffix.lower()
    if ext not in {".pdf", ".jpg", ".jpeg", ".png"}:
        raise HTTPException(400, "Answer sheet must be PDF, JPG, or PNG.")

    sheet_bytes = await answer_sheet.read()
    if not sheet_bytes:
        raise HTTPException(400, "Answer sheet is empty.")
    page_count = upload._validate_answer_sheet_bytes(sheet_bytes, ext)

    from models.schemas import QuestionRubric, RubricCriterion
    from services.marks_validator import safe_int_qnum, validate_and_reconcile_exam_structure
    q_raw_list = rubric_data.get("questions", []) if isinstance(rubric_data, dict) else rubric_data
    rubric_list = [
        QuestionRubric(
            question_number=safe_int_qnum(q.get("question_number", 1), 1),
            question_text=str(q.get("question_text", "")),
            max_marks=float(q.get("max_marks", 5.0)),
            criteria=[RubricCriterion(**criterion) for criterion in q.get("criteria", [])],
            sub_part=str(q.get("sub_part", "")),
            choice_group=str(q.get("choice_group", "")),
            is_or_choice=bool(q.get("is_or_choice", False)),
        )
        for q in q_raw_list
    ]
    if not rubric_list:
        raise HTTPException(400, "Rubric contains no questions.")

    total_marks = (
        float(rubric_data.get("total_marks", 0))
        if isinstance(rubric_data, dict)
        else 0
    )
    if total_marks <= 0:
        total_marks = validate_and_reconcile_exam_structure(
            {"questions": [question.model_dump() for question in rubric_list]}
        ).calculated_maximum_marks
    if total_marks <= 0:
        raise HTTPException(400, "Rubric does not define a positive maximum mark total.")

    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "total_marks": total_marks,
                "questions": [question.model_dump() for question in rubric_list],
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    job = upload.queue_manager.enqueue(
        exam_id="manual_run",
        hall_ticket="STUDENT_1",
        student_name="Student",
        file_bytes=sheet_bytes,
        file_ext=ext,
        mime_type={
            ".pdf": "application/pdf",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
        }[ext],
        original_filename=answer_sheet.filename,
        rubric_fingerprint=fingerprint,
        rubric_total_marks=total_marks,
        rubric_data=[question.model_dump() for question in rubric_list],
    )
    upload.queue_manager.start_worker(upload._execute_grading_job)
    return {
        "job_id": job.job_id,
        "exam_id": job.exam_id,
        "hall_ticket": job.hall_ticket,
        "status": job.status,
        "current_stage": job.current_stage,
        "page_count": page_count,
    }