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
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import route modules
from routes import auth, exam, upload, results, review, export
from services import teacher_auth
import config
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

# Only pages served from this machine may call the API.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # The export's file name says NOT_FINAL, so the page has to be able to read it.
    expose_headers=["Content-Disposition"],
)


# These run inside the CORS layer, so a refusal still reaches the page with its reason.
_OPEN_PATHS = {"/", "/health", "/auth/login", "/auth/logout", "/auth/status"}


@app.middleware("http")
async def limit_and_require_teacher(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > config.MAX_REQUEST_MB * 1024 * 1024:
        return JSONResponse(
            status_code=413,
            content={"detail": f"This upload is over the {config.MAX_REQUEST_MB:g} MB limit. Send fewer sheets at once."},
        )
    if request.url.path not in _OPEN_PATHS and not teacher_auth.session_is_valid(
        request.cookies.get(teacher_auth.COOKIE_NAME)
    ):
        return JSONResponse(status_code=401, content={"detail": "Please sign in."})
    return await call_next(request)


# =========================================================
# REGISTER ROUTERS
# =========================================================

app.include_router(auth.router)
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


