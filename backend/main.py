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
from config import GEMINI_MODEL, get_firestore, AI_PROVIDER, OLLAMA_SIMPLE_MODEL, OLLAMA_COMPLEX_MODEL
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
            is_rubric_from_scanned_paper=True,
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


@app.post("/process-answer-sheet")
async def process_answer_sheet(
    answer_sheet: UploadFile = File(...),
    rubric: str = Form(""),
):
    """
    Direct answer-sheet evaluation for root UI (app.js).
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

    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(sheet_bytes)
        sheet_path = tmp.name

    try:
        if ext == ".pdf":
            image_parts = model_router.ollama.render_pdf_to_images(sheet_path, max_pages=6, dpi=120)
        else:
            image_parts = [sheet_path]

        # Convert rubric dictionary to list of QuestionRubric
        from models.schemas import QuestionRubric, RubricCriterion
        rubric_list = []
        q_raw_list = rubric_data.get("questions", []) if isinstance(rubric_data, dict) else rubric_data
        for q in q_raw_list:
            rubric_list.append(
                QuestionRubric(
                    question_number=int(q.get("question_number", 1)),
                    question_text=str(q.get("question_text", "")),
                    max_marks=float(q.get("max_marks", 5.0)),
                    criteria=[RubricCriterion(**c) for c in q.get("criteria", [])],
                )
            )

        from pipeline import process_paper
        paper_res = process_paper(
            exam_id="manual_run",
            hall_ticket="STUDENT_1",
            student_name="Student",
            image_parts=image_parts,
            rubric_list=rubric_list,
            image_bytes_list=[sheet_bytes],
        )

        return paper_res.model_dump()

    finally:
        Path(sheet_path).unlink(missing_ok=True)