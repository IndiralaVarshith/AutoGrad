"""
AutoGrade — Exam Setup Routes

POST /exam/setup              — Analyze question paper, extract structure, generate proposed rubric
POST /exam/rubric/approve     — Save approved rubric to Firestore (with write-verify)
GET  /exam                    — List all exams
GET  /exam/{exam_id}          — Get exam metadata
GET  /exam/{exam_id}/rubric   — Retrieve approved rubric for an exam

Route ordering is critical:
  /exam/rubric/approve  (static segment "rubric") MUST be registered before
  /exam/{exam_id}       (wildcard) to prevent shadowing.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Form, HTTPException

import config
from services.gemini_client import BadModelResponse, DailyLimitReached, GeminiError, RunCapReached
from services.gemini_paper import PAPER_PROMPT_VERSION, read_question_paper, rubric_total_problems
from services.criteria_draft import (
    AnswerKeyError,
    approval_problems,
    draft_criteria,
    parse_answer_key,
)
from services.db import db_service
from services.upload_limits import read_limited
from services.rubric_cache import (
    compute_doc_fingerprint,
    compute_generation_fingerprint,
    get_cached_draft,
    store_draft_cache,
    RUBRIC_SCHEMA_VERSION,
)
from models.schemas import RubricApproveRequest


router = APIRouter(prefix="/exam", tags=["Exam"])


# =========================================================
# ALLOWED FILE TYPES
# =========================================================

ALLOWED_MIME = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".txt": "text/plain",
}


# =========================================================
# POST /exam/setup
# =========================================================

@router.post("/setup")
async def exam_setup(
    exam_id: str = Form(...),
    question_paper: UploadFile = File(...),
    textbook: UploadFile | None = File(None),
    force_regenerate: str = Form("false"),
    answer_key: UploadFile | None = File(None),
):
    """
    Upload a question paper and turn it into structured questions and marks.

    - Same file for this exam: returns the saved paper without parsing again.
    - Different file for this exam: 409 unless force_regenerate=true.
    - One Gemini request reads the paper, and the answer key if one is given.
    - Unreadable uploads: 422, nothing saved.
    - Ambiguous papers: draft returned with needs_teacher_review, nothing saved.
    """

    try:
        exam_id = exam_id.strip()
        if not exam_id:
            raise HTTPException(400, "exam_id is required.")

        if not question_paper.filename:
            raise HTTPException(400, "Question paper is required.")

        ext = Path(question_paper.filename).suffix.lower()
        if not ALLOWED_MIME.get(ext):
            raise HTTPException(400, "Question paper must be PDF, JPG, PNG, or TXT.")

        q_bytes = await read_limited(question_paper)
        if not q_bytes:
            raise HTTPException(400, "Question paper file is empty.")

        should_force = force_regenerate.lower() in ("true", "1", "yes")
        doc_fingerprint = compute_doc_fingerprint(q_bytes)
        notices = [_TEXTBOOK_NOT_USED_MESSAGE] if textbook and textbook.filename else []

        # Checked first so a repeat upload costs no parsing or model time.
        existing = _load_saved_paper(exam_id)
        if existing and not should_force:
            if existing.get("doc_fingerprint") != doc_fingerprint:
                raise HTTPException(409, _DIFFERENT_PAPER_MESSAGE.format(exam_id=exam_id))
            return _paper_response(exam_id, existing, saved=True, duplicate=True, notices=notices)

        key_bytes = await read_limited(answer_key) if answer_key and answer_key.filename else b""
        key_ext = Path(answer_key.filename).suffix.lower() if key_bytes else ""
        return _gemini_paper(
            exam_id, q_bytes, ext, doc_fingerprint, key_bytes, key_ext, should_force, notices
        )

    except HTTPException:
        raise

    except Exception as error:
        import traceback
        print(f"[ExamSetup] Error: {traceback.format_exc()}")
        raise HTTPException(500, str(error))





_TEXTBOOK_NOT_USED_MESSAGE = (
    "The textbook you uploaded was not used. Only the question paper is read at this step."
)
_RUBRIC_CANCELLED_MESSAGE = (
    "The question paper changed, so the previously approved rubric was cancelled: {ids}. "
    "Approve a new rubric before grading. Results already graded were not changed."
)
_UNREADABLE_MESSAGE = (
    "This question paper could not be read, so nothing was saved. {reasons} "
    "Please upload a clearer scan."
)
_UNUSABLE_REPLY_MESSAGE = (
    "The question paper could not be read reliably, so nothing was saved. "
    "Please upload a clearer scan or check it with the teacher."
)
_DIFFERENT_PAPER_MESSAGE = (
    "Exam '{exam_id}' already has a different question paper saved. "
    "Use force_regenerate=true to replace it, or a new exam ID."
)


def _load_saved_paper(exam_id: str) -> dict | None:
    if not db_service.is_connected():
        raise HTTPException(503, "Firestore is unavailable, so the question paper cannot be saved.")
    try:
        return db_service.get_question_paper(exam_id)
    except Exception as error:
        print(f"[ExamSetup] Could not read saved paper: {type(error).__name__}")
        raise HTTPException(503, "Firestore is unavailable, so the question paper cannot be saved.")










def _gemini_paper(
    exam_id: str,
    q_bytes: bytes,
    ext: str,
    doc_fingerprint: str,
    key_bytes: bytes,
    key_ext: str,
    should_force: bool,
    notices: list[str],
) -> dict:
    """One Gemini request for the paper, unless these exact files were already read."""
    gen_fingerprint = compute_generation_fingerprint(
        doc_fingerprint=doc_fingerprint,
        model_name=config.GEMINI_MODEL,
        schema_version=RUBRIC_SCHEMA_VERSION,
        prompt_version=PAPER_PROMPT_VERSION,
        ref_fingerprint=compute_doc_fingerprint(key_bytes) if key_bytes else "",
    )
    cached = None if should_force else get_cached_draft(gen_fingerprint)
    if cached:
        return _finish_paper(exam_id, cached, should_force, from_cache=True, notices=notices)
    try:
        paper = read_question_paper(
            q_bytes, ext, answer_key=key_bytes or None, answer_key_extension=key_ext or ".txt"
        )
    except (DailyLimitReached, RunCapReached) as error:
        raise HTTPException(429, f"{error} Nothing was saved.")
    except BadModelResponse:
        raise HTTPException(422, _UNUSABLE_REPLY_MESSAGE)
    except GeminiError as error:
        print(f"[ExamSetup] Gemini failed: {error}")
        raise HTTPException(503, "Gemini could not read the paper, so nothing was saved. Try again.")
    if paper.get("unreadable"):
        reasons = "; ".join(paper.get("unclear_items") or []) or "No questions were found."
        raise HTTPException(422, _UNREADABLE_MESSAGE.format(reasons=reasons))
    if key_bytes:
        # The key is the teacher's own upload, so it fills the picker. Approval still confirms it.
        for question in paper["questions"]:
            question["correct_option"] = question.get("suggested_option", "")
    paper["doc_fingerprint"] = doc_fingerprint
    paper["gen_fingerprint"] = gen_fingerprint
    paper["schema_version"] = RUBRIC_SCHEMA_VERSION
    store_draft_cache(gen_fingerprint, paper)
    return _finish_paper(exam_id, paper, should_force, notices=notices)




def _finish_paper(
    exam_id: str,
    rubric_data: dict,
    should_force: bool,
    *,
    from_cache: bool = False,
    notices: list[str] | None = None,
) -> dict:
    """Save a clean paper. A paper needing teacher review is returned, never saved."""
    notices = list(notices or [])
    if rubric_data["needs_teacher_review"]:
        return _paper_response(
            exam_id, rubric_data, saved=False, from_cache=from_cache, notices=notices
        )

    try:
        result = db_service.save_question_paper(exam_id, rubric_data, replace=should_force)
    except Exception as error:
        print(f"[ExamSetup] Could not save paper: {type(error).__name__}")
        raise HTTPException(503, "Firestore is unavailable, so the question paper was not saved.")
    if result["outcome"] == "conflict":
        raise HTTPException(409, _DIFFERENT_PAPER_MESSAGE.format(exam_id=exam_id))
    cancelled = result["invalidated_rubric_ids"]
    if cancelled:
        notices.append(_RUBRIC_CANCELLED_MESSAGE.format(ids=", ".join(cancelled)))
    return _paper_response(
        exam_id,
        result["paper"],
        saved=True,
        duplicate=result["outcome"] == "duplicate",
        from_cache=from_cache,
        notices=notices,
        invalidated_rubric_ids=cancelled,
    )


def _paper_response(
    exam_id: str,
    rubric: dict,
    *,
    saved: bool,
    duplicate: bool = False,
    from_cache: bool = False,
    notices: list[str] | None = None,
    invalidated_rubric_ids: list[str] | None = None,
) -> dict:
    """Build the reply. Notices describe this upload, so they are never saved."""
    notices = notices or []
    return {
        "success": True,
        "exam_id": exam_id,
        # The Setup page shows rubric.extraction_warnings to the teacher.
        "rubric": {
            **rubric,
            "extraction_warnings": list(rubric.get("extraction_warnings", [])) + notices,
        },
        "warnings": notices,
        "invalidated_rubric_ids": invalidated_rubric_ids or [],
        "doc_fingerprint": rubric.get("doc_fingerprint", ""),
        "gen_fingerprint": rubric.get("gen_fingerprint", ""),
        "from_cache": from_cache,
        "saved": saved,
        "duplicate": duplicate,
        "needs_teacher_review": bool(rubric.get("needs_teacher_review")),
        "problems": rubric.get("problems", []),
    }


# =========================================================
# POST /exam/{exam_id}/rubric/draft-criteria
# =========================================================

@router.post("/{exam_id}/rubric/draft-criteria")
async def draft_rubric_criteria(exam_id: str, answer_key: UploadFile = File(...)):
    """Draft criteria from an answer key. Nothing is saved or approved here."""
    paper = _load_saved_paper(exam_id.strip())
    if not paper:
        raise HTTPException(404, "Upload the question paper for this exam first.")
    try:
        key, problems = parse_answer_key((await answer_key.read()).decode("utf-8-sig"))
    except UnicodeDecodeError:
        raise HTTPException(422, "The answer key is not a UTF-8 text file.")
    except AnswerKeyError as error:
        raise HTTPException(422, str(error))

    questions, draft_problems = draft_criteria(paper.get("questions", []), key)
    problems = problems + draft_problems
    return {
        "success": True,
        "exam_id": exam_id.strip(),
        "approved": False,
        "questions": questions,
        "problems": problems,
        "needs_teacher_review": True,
    }


# =========================================================
# POST /exam/rubric/approve
# =========================================================
# IMPORTANT: This route MUST be registered BEFORE GET /{exam_id}
# to prevent FastAPI treating "rubric" as exam_id.

@router.post("/rubric/approve")
async def approve_rubric(request: RubricApproveRequest):
    """
    Save teacher-approved rubric to Firestore with write-verify.

    1. Validates rubric.
    2. Saves to Firestore.
    3. Reads back the saved document to verify persistence.
    4. Returns the verified rubric_id and version.
    """
    try:
        exam_id = request.exam_id.strip()
        if not exam_id:
            raise HTTPException(400, "exam_id is required.")

        if not request.rubric:
            raise HTTPException(400, "Rubric questions list is empty.")

        if not request.teacher_confirmed:
            raise HTTPException(
                422,
                "The teacher must confirm every question and mark against the paper "
                "before approving. Nothing was saved.",
            )

        # Validate before saving
        rubric_dict = {"questions": [
            q.model_dump() if hasattr(q, "model_dump") else q
            for q in request.rubric
        ]}
        checked_total, total_problems, warnings = rubric_total_problems(
            rubric_dict["questions"], request.total_marks
        )
        if total_problems:
            raise HTTPException(
                422, f"Fix these before approving: {' '.join(total_problems)} Nothing was saved."
            )

        problems = approval_problems(request.rubric)
        if problems:
            raise HTTPException(
                422,
                f"Fix these before approving: {' '.join(problems)} Nothing was saved.",
            )

        if not db_service.is_connected():
            raise HTTPException(503, "Firestore is unavailable, so the rubric was not saved.")

        # Save to Firestore
        saved = db_service.save_approved_rubric(
            exam_id=exam_id,
            questions=request.rubric,
            doc_fingerprint=getattr(request, "doc_fingerprint", ""),
            gen_fingerprint=getattr(request, "gen_fingerprint", ""),
            total_marks=getattr(request, "total_marks", None),
            teacher_confirmed=True,
            checked_total=checked_total,
        )

        # WRITE-VERIFY: read back the saved document
        rubric_id = saved.get("rubric_id")
        version = saved.get("version", 1)

        verify_result = db_service.get_approved_rubric(exam_id)
        if not verify_result:
            raise HTTPException(
                500,
                f"Rubric {rubric_id} was saved but could not be read back from Firestore. "
                "Check Firestore rules and connectivity."
            )

        # Confirm it belongs to correct exam
        if verify_result.get("exam_id") != exam_id:
            raise HTTPException(
                500,
                f"Write-verify failed: retrieved rubric has exam_id="
                f"'{verify_result.get('exam_id')}', expected '{exam_id}'."
            )

        return {
            "success": True,
            "exam_id": exam_id,
            "rubric_id": rubric_id,
            "version": version,
            "total_marks": saved.get("total_marks", 0.0),
            "questions_count": len(saved.get("questions", [])),
            "warnings": warnings,
            "verified": True,
            "message": (
                f"Rubric version {version} approved, saved, and verified in Firestore."
                + (f" ({len(warnings)} warnings)" if warnings else "")
            ),
        }

    except HTTPException:
        raise

    except Exception as error:
        import traceback
        print(f"[ExamApprove] Error: {traceback.format_exc()}")
        raise HTTPException(500, f"Failed to save rubric: {error}")


# =========================================================
# GET /exam  (List all exams)
# =========================================================
# Registered BEFORE /{exam_id} to prevent route shadowing.

@router.get("")
@router.get("/")
async def list_exams():
    """Retrieve list of all exams from Firestore."""
    try:
        exams = db_service.list_exams()
        return {"exams": exams, "count": len(exams)}
    except Exception as error:
        raise HTTPException(500, f"Failed to list exams: {error}")


# =========================================================
# GET /exam/{exam_id}/rubric  (Retrieve approved rubric)
# =========================================================
# MUST be registered BEFORE /exam/{exam_id} so FastAPI doesn't
# swallow the /rubric suffix as part of the exam_id wildcard.

@router.get("/{exam_id}/rubric")
async def get_exam_rubric(exam_id: str):
    """
    Retrieve the teacher-approved rubric for an exam.
    Checks both the sub-collection path and the top-level rubrics collection.
    """
    exam_id = exam_id.strip()
    rubric_doc = db_service.get_approved_rubric(exam_id)
    if not rubric_doc:
        raise HTTPException(
            404,
            f"No approved rubric found in Firestore for exam '{exam_id}'. "
            "Please generate and approve a rubric in the Setup Exam tab first."
        )
    return rubric_doc


# =========================================================
# GET /exam/{exam_id}  (Get exam details)
# =========================================================
# Registered AFTER all static-segment routes.

@router.get("/{exam_id}")
async def get_exam_details(exam_id: str):
    """Retrieve exam record and its approved rubric metadata."""
    exam_id = exam_id.strip()
    exam_doc = db_service.get_exam(exam_id)
    if not exam_doc:
        raise HTTPException(404, f"Exam '{exam_id}' not found.")
    return exam_doc
