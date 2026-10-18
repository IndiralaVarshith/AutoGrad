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
from config import GEMINI_MODEL, get_firestore


app = FastAPI(
    title="AutoGrade AI Grading System",
    description="Automated AI-powered grading system for handwritten answer sheets with multi-agent verification and RAG.",
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
    """Service status and endpoint overview."""
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
    """Health check for Cloud Run and load balancers."""
    return {"status": "healthy"}