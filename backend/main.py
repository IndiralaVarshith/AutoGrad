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

import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import route modules
from routes import exam, upload, results, review, export
from config import (
    GEMINI_MODEL,
    get_firestore,
)


app = FastAPI(
    title="AutoGrade AI Grading System",
    description="Automated AI-powered grading system for handwritten answer sheets, using Gemini.",
    version="2.0.0",
)


@app.on_event("startup")
def resume_queue_after_restart() -> None:
    """Sheets waiting or cut off by a restart carry on without a new upload."""
    if upload.queue_manager.has_pending_jobs():
        upload.queue_manager.start_worker(upload._execute_grading_job)


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
    return {
        "service": "AutoGrade AI Grading System",
        "status": "online",
        "version": "2.0.0",
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
        },
    }


@app.get("/health")
def health_check():
    """Health check for local dev and load balancers."""
    return {
        "status": "healthy",
        "provider": "gemini",
        "firestore_emulator": bool(os.getenv("FIRESTORE_EMULATOR_HOST", "").strip()),
    }


# =========================================================
# LEGACY / ROOT UI COMPATIBILITY ROUTES
# =========================================================


