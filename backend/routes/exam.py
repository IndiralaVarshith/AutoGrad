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

import json
import os
import re
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Form, HTTPException

from config import OLLAMA_PAPER_READER_MODEL, get_firestore
from services.router import NoSuitableModelError, model_router
from services.criteria_draft import (
    AnswerKeyError,
    approval_problems,
    draft_criteria,
    parse_answer_key,
)
from services.db import db_service
from services.rubric_cache import (
    compute_doc_fingerprint,
    compute_generation_fingerprint,
    get_cached_draft,
    store_draft_cache,
    validate_rubric_structure,
    RUBRIC_SCHEMA_VERSION,
    RUBRIC_PROMPT_VERSION,
)
from services.marks_validator import (
    validate_and_reconcile_exam_structure,
    normalize_rubric_questions_with_structure,
    parse_question_number_and_part,
    read_marks,
)
from models.schemas import (
    QuestionRubric,
    RubricCriterion,
    RubricApproveRequest,
)
from services.rag import setup_rubric_knowledge, generate_rubric_for_question
from services.ocr import _page_image_bytes
from services.paper_intake import (
    QualityCheckUnavailable,
    apply_review_items,
    check_image_quality,
    parse_question_paper_text,
    questions_from_model_reply,
)


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
# RUBRIC GENERATION PROMPT (versioned)
# =========================================================

RUBRIC_GENERATION_PROMPT = """You are the Rubric Generation Module of AutoGrade — an AI-powered examination grading system.

Carefully analyze the uploaded question paper image(s).

1. EXAM HEADER & DECLARED MARKS:
   - Identify the exam title/subject.
   - Look for the explicit total maximum marks printed on the paper (e.g. "Marks: 20", "Max Marks: 20", "M.M.: 20", "Total Marks: 20").
   - Set 'declared_maximum_marks' to this exact number.

2. SECTIONS & INSTRUCTIONS:
   - Identify all sections (e.g., Section I, Section II, Section III, Part A, Part B).
   - Read the exact section instructions and formulas (e.g. "4x1=4M", "4x2=8M", "2x4=8M", "Answer any 2 of the following").
   - Extract the attempt instruction and the marks per question for each section.

3. FOR EVERY QUESTION AND SUB-QUESTION VISIBLE IN THE PAPER:
   - Extract the question number exactly as printed (1, 2, 3... or 9A, 9B, 10A, 10B).
   - If a question has sub-parts (e.g. A, B, a, b), record 'sub_part'.
   - Extract the complete question text accurately.
   - Question type: THEORY, NUMERICAL, DIAGRAM, or MIXED.
   - Marks: Use the marks allocated by the section formula (e.g. in '4x1=4M', each question gets 1.0 mark; in '4x2=8M', each gets 2.0 marks; in '2x4=8M', each gets 4.0 marks). DO NOT INVENT MARKS.
   - Choice groups: If questions are alternatives connected by 'OR' (e.g., 9A OR 9B), set 'choice_group' to the parent question (e.g., 'Q9') and 'is_or_choice' to true.
   - If a question prints answer options, set 'question_format' to "MCQ" and copy them into 'options' as {"label": "A", "text": "..."}. Otherwise use "SHORT_ANSWER" and an empty list.
   - If you cannot read a question's number, text or marks, leave that value empty. Never fill it in from context.
   - Copy 'question_text' word for word as printed. Do not add, explain or summarise anything.
   - Never put values read from a graph, chart, table, map or figure into 'question_text'.
   - If a question uses a graph, chart, table, map or figure, set 'has_figure' to true and 'figure_page' to the page it is on (first page is 1). Otherwise set 'has_figure' to false and 'figure_page' to null.
   - Create 2-3 clear marking criteria for the question. The sum of all criteria max_marks MUST EXACTLY EQUAL the question's marks.

CRITICAL RULES:
- Never invent marks. Never assume all questions have equal marks.
- The criteria marks sum for each question must EXACTLY equal the question's marks.
- Always check the header for the declared maximum marks.

Return ONLY valid JSON in this exact structure — no markdown, no explanation:
{
  "exam_title": "Class X Social DPT",
  "declared_maximum_marks": 20.0,
  "sections": [
    {
      "section_id": "I",
      "section_title": "Section I",
      "attempt_instruction": "I. ANSWER THE FOLLOWING QUESTIONS: 4x1=4M",
      "attempt_count": 4,
      "marks_per_question": 1.0,
      "section_maximum_marks": 4.0,
      "questions": [
        {
          "question_number": 1,
          "sub_part": "",
          "question_text": "...",
          "question_type": "THEORY",
          "marks": 1.0,
          "choice_group": "",
          "is_or_choice": false,
          "question_format": "SHORT_ANSWER",
          "options": [],
          "has_figure": false,
          "figure_page": null,
          "criteria": [
            {"description": "...", "max_marks": 0.5},
            {"description": "...", "max_marks": 0.5}
          ]
        }
      ]
    }
  ]
}
"""


# =========================================================
# POST /exam/setup
# =========================================================

@router.post("/setup")
async def exam_setup(
    exam_id: str = Form(...),
    question_paper: UploadFile = File(...),
    textbook: UploadFile | None = File(None),
    force_regenerate: str = Form("false"),
):
    """
    Upload a question paper and turn it into structured questions and marks.

    - Same file for this exam: returns the saved paper without parsing again.
    - Different file for this exam: 409 unless force_regenerate=true.
    - .txt papers are parsed without AI. Scans are quality-checked, then read by
      the vision model.
    - Unreadable uploads: 422, nothing saved.
    - Ambiguous papers: draft returned with needs_teacher_review, nothing saved.
    """

    question_path = None

    try:
        exam_id = exam_id.strip()
        if not exam_id:
            raise HTTPException(400, "exam_id is required.")

        if not question_paper.filename:
            raise HTTPException(400, "Question paper is required.")

        ext = Path(question_paper.filename).suffix.lower()
        if not ALLOWED_MIME.get(ext):
            raise HTTPException(400, "Question paper must be PDF, JPG, PNG, or TXT.")

        q_bytes = await question_paper.read()
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

        if ext == ".txt":
            return _finish_paper(
                exam_id, _paper_from_text(q_bytes, doc_fingerprint), should_force, notices=notices
            )

        gen_fingerprint = compute_generation_fingerprint(
            doc_fingerprint=doc_fingerprint,
            model_name=f"{model_router.routing_policy_fingerprint()}:{OLLAMA_PAPER_READER_MODEL}",
            schema_version=RUBRIC_SCHEMA_VERSION,
            prompt_version=RUBRIC_PROMPT_VERSION,
        )

        if not should_force:
            cached = get_cached_draft(gen_fingerprint)
            if cached:
                print(f"[ExamSetup] Cache hit for gen_fingerprint={gen_fingerprint[:12]}… returning cached rubric.")
                return _finish_paper(exam_id, cached, should_force, from_cache=True, notices=notices)

        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            tmp.write(q_bytes)
            question_path = tmp.name

        paper_images, page_bytes = _render_pages(question_path, ext, q_bytes, doc_fingerprint)
        _reject_unreadable(page_bytes)

        # Reference material is not sent here: its pages could be read as questions.
        try:
            res = model_router.generate(
                task_type="rubric_generation",
                prompt=RUBRIC_GENERATION_PROMPT,
                images=paper_images,
                format_json=True,
                temperature=0,
                force_model=OLLAMA_PAPER_READER_MODEL,
                requires_vision=True,
            )
        except NoSuitableModelError:
            raise HTTPException(
                503,
                f"The paper reader model '{OLLAMA_PAPER_READER_MODEL}' is not available, so "
                "nothing was saved. Install it or change OLLAMA_PAPER_READER_MODEL.",
            )
        page_readers = _log_page_readers(exam_id, len(page_bytes), res)

        if not res.parsed_json or not isinstance(res.parsed_json, dict):
            raise HTTPException(422, _UNUSABLE_REPLY_MESSAGE)

        try:
            rubric_data = _paper_from_model_reply(res.parsed_json, doc_fingerprint, gen_fingerprint)
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            print(f"[ExamSetup] Unusable model reply: {type(error).__name__}")
            raise HTTPException(422, _UNUSABLE_REPLY_MESSAGE)
        rubric_data["page_readers"] = page_readers

        if not rubric_data["needs_teacher_review"]:
            store_draft_cache(gen_fingerprint, rubric_data)

        return _finish_paper(exam_id, rubric_data, should_force, notices=notices)

    except HTTPException:
        raise

    except Exception as error:
        import traceback
        print(f"[ExamSetup] Error: {traceback.format_exc()}")
        raise HTTPException(500, str(error))

    finally:
        if question_path:
            try:
                Path(question_path).unlink(missing_ok=True)
            except Exception:
                pass


def _log_page_readers(exam_id: str, page_count: int, res) -> list[dict]:
    """Record which model read each page. All pages of a paper go in one model call."""
    readers = [
        {
            "page": page,
            "model": res.model_used,
            "provider": res.provider,
            "task_id": res.task_id,
        }
        for page in range(1, page_count + 1)
    ]
    for reader in readers:
        print(
            f"[ExamSetup] exam={exam_id} page={reader['page']}/{page_count} "
            f"read_by={reader['model']} provider={reader['provider']} task={reader['task_id']}"
        )
    return readers


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


def _render_pages(
    question_path: str, ext: str, q_bytes: bytes, doc_fingerprint: str
) -> tuple[list, list[bytes]]:
    """Return page images for the model and raw page bytes for the quality check."""
    if ext != ".pdf":
        return [question_path], [q_bytes]
    try:
        images = model_router.ollama.render_pdf_to_images(
            question_path, max_pages=None, dpi=150, source_hash=doc_fingerprint
        )
    except Exception as error:
        print(f"[ExamSetup] PDF could not be rendered: {type(error).__name__}")
        raise HTTPException(
            422,
            _UNREADABLE_MESSAGE.format(reasons="The PDF file is damaged or cannot be opened."),
        )
    return images, [page for page in map(_page_image_bytes, images) if page]


def _reject_unreadable(page_bytes: list[bytes]) -> None:
    try:
        problems = check_image_quality(page_bytes)
    except QualityCheckUnavailable as error:
        raise HTTPException(503, f"{error} Nothing was saved.")
    if problems:
        raise HTTPException(422, _UNREADABLE_MESSAGE.format(reasons=" ".join(problems)))


def _paper_from_text(q_bytes: bytes, doc_fingerprint: str) -> dict:
    try:
        text = q_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(
            422, _UNREADABLE_MESSAGE.format(reasons="The text file is not UTF-8 text.")
        )

    parsed = parse_question_paper_text(text)
    if not parsed.questions:
        raise HTTPException(
            422, _UNREADABLE_MESSAGE.format(reasons="No numbered questions were found.")
        )

    declared = parsed.raw.get("declared_maximum_marks")
    structure = validate_and_reconcile_exam_structure(parsed.raw)
    problems = parsed.problems + structure.validation_warnings
    return _paper_record(
        questions=parsed.questions,
        structure=structure,
        declared=declared,
        problems=problems,
        warnings=[],
        source_kind="text",
        doc_fingerprint=doc_fingerprint,
        gen_fingerprint=compute_generation_fingerprint(
            doc_fingerprint=doc_fingerprint,
            model_name="text-parser",
            schema_version=RUBRIC_SCHEMA_VERSION,
            prompt_version=RUBRIC_PROMPT_VERSION,
        ),
    )


def _paper_from_model_reply(reply: dict, doc_fingerprint: str, gen_fingerprint: str) -> dict:
    questions_list, problems = questions_from_model_reply(reply)
    if not questions_list:
        raise HTTPException(422, _UNREADABLE_MESSAGE.format(reasons="No questions were found."))

    declared = read_marks(reply.get("declared_maximum_marks"))
    # Validate the same question list that gets saved, so the total matches it.
    structure = validate_and_reconcile_exam_structure(reply)

    validated_questions = []
    for idx, q in enumerate(questions_list):
        q_num, extracted_part = parse_question_number_and_part(q.get("question_number"), idx + 1)
        q_type = str(q.get("question_type", "THEORY")).upper()
        if q_type not in {"THEORY", "NUMERICAL", "DIAGRAM", "MIXED"}:
            q_type = "THEORY"

        validated_criteria = [
            {
                "description": str(c.get("description", "Evaluation criterion")).strip(),
                "max_marks": read_marks(c.get("max_marks", c.get("marks"))) or 0.0,
            }
            for c in q.get("criteria") or []
        ]
        validated_questions.append({
            "question_number": q_num,
            "sub_part": str(q.get("sub_part", "")).strip() or extracted_part,
            "question_text": str(q.get("question_text", "")).strip(),
            "question_type": q_type,
            "question_format": str(q.get("question_format", "")).upper(),
            "options": q.get("options") or [],
            "has_figure": q.get("has_figure") is True,
            "figure_page": q.get("figure_page"),
            "max_marks": read_marks(q.get("max_marks", q.get("marks"))),
            "marks": read_marks(q.get("marks", q.get("max_marks"))),
            "section": str(q.get("section", q.get("section_id", ""))).strip(),
            "choice_group": str(q.get("choice_group", "")),
            "is_or_choice": bool(q.get("is_or_choice", False)),
            "criteria": validated_criteria,
        })

    normalized_questions = normalize_rubric_questions_with_structure(validated_questions, structure)
    return _paper_record(
        questions=normalized_questions,
        structure=structure,
        declared=declared,
        problems=problems + structure.validation_warnings,
        warnings=validate_rubric_structure({"questions": normalized_questions}),
        source_kind="scan",
        doc_fingerprint=doc_fingerprint,
        gen_fingerprint=gen_fingerprint,
    )


def _paper_record(
    *,
    questions: list[dict],
    structure,
    declared: float | None,
    problems: list[str],
    warnings: list[str],
    source_kind: str,
    doc_fingerprint: str,
    gen_fingerprint: str,
) -> dict:
    if declared is None:
        warnings = warnings + [
            f"The paper does not print a total; {structure.calculated_maximum_marks:g} marks "
            "was calculated from the question marks. Teacher should confirm it."
        ]
    sections = sorted({q.get("section", "") for q in questions if q.get("section")})
    review_items = apply_review_items(questions)
    return {
        "exam_title": structure.exam_title,
        "questions": questions,
        "question_count": len(questions),
        "total_marks": structure.calculated_maximum_marks,
        "declared_maximum_marks": declared,
        "calculated_maximum_marks": structure.calculated_maximum_marks,
        "validation_status": structure.validation_status,
        "reconciliation_notes": structure.reconciliation_notes,
        "sections": sections,
        "extraction_warnings": warnings,
        "problems": problems,
        "needs_teacher_review": bool(problems) or structure.validation_status == "DISCREPANCY",
        "review_items": review_items,
        "verification_status": "UNVERIFIED",
        "source_kind": source_kind,
        "doc_fingerprint": doc_fingerprint,
        "gen_fingerprint": gen_fingerprint,
        "schema_version": RUBRIC_SCHEMA_VERSION,
        "prompt_version": RUBRIC_PROMPT_VERSION,
    }


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
        warnings = validate_rubric_structure(rubric_dict)
        rubric_structure = validate_and_reconcile_exam_structure({
            **rubric_dict,
            "declared_maximum_marks": request.total_marks,
        })
        if rubric_structure.validation_status == "DISCREPANCY":
            raise HTTPException(
                422,
                detail=(
                    f"Rubric maximum mismatch: declared "
                    f"{rubric_structure.declared_maximum_marks:g}, but question/attempt "
                    f"structure totals {rubric_structure.calculated_maximum_marks:g}. "
                    "Correct the rubric or preserve the paper's section attempt rules "
                    "before approval."
                ),
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
