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

from config import get_gemini_client, get_firestore, GEMINI_MODEL, AI_PROVIDER
from services.router import model_router
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
from models.schemas import (
    QuestionRubric,
    RubricCriterion,
    RubricApproveRequest,
)
from services.rag import setup_rubric_knowledge, generate_rubric_for_question


router = APIRouter(prefix="/exam", tags=["Exam"])


# =========================================================
# ALLOWED FILE TYPES
# =========================================================

ALLOWED_MIME = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}


# =========================================================
# RUBRIC GENERATION PROMPT (versioned)
# =========================================================

RUBRIC_GENERATION_PROMPT = """You are the Rubric Generation Module of AutoGrade — an AI-powered examination grading system.

Carefully analyze the uploaded question paper image(s).

For EVERY question and sub-question visible in the paper:
1. Extract the question number exactly as printed (e.g., 1, 2a, 2b, Q3, Part-A Q1).
2. Extract the complete question text accurately.
3. Identify the question type: THEORY, NUMERICAL, DIAGRAM, or MIXED.
4. Identify the marks allocated to this question (look for marks in brackets like [5], (10 marks), etc.).
5. If marks are not explicitly stated for a sub-question, infer from the section total.
6. Create 2-4 clear marking criteria for the question. IMPORTANT: the sum of all criteria max_marks MUST EXACTLY EQUAL the question's max_marks.
7. Note any internal choices (OR questions) — list each option as a separate entry with an 'is_choice_of' field.
8. Note sections (Section A, Section B, etc.) using a 'section' field.

CRITICAL RULES:
- Never invent marks. Only extract what is explicitly or implicitly stated in the paper.
- Never invent question text. Only extract what is visible.
- Do NOT assume equal marks across all questions.
- The sum of criteria marks for each question must EXACTLY equal the question's max_marks.

Return ONLY valid JSON in this exact structure — no markdown, no explanation:
{
  "questions": [
    {
      "question_number": 1,
      "question_text": "...",
      "question_type": "THEORY",
      "max_marks": 5.0,
      "section": "A",
      "criteria": [
        {"description": "...", "max_marks": 2.0},
        {"description": "...", "max_marks": 3.0}
      ]
    }
  ],
  "total_marks": 50.0,
  "sections": ["A", "B"],
  "extraction_warnings": []
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
    Upload question paper.
    1. Computes SHA-256 fingerprint of the document.
    2. Checks in-memory cache for an existing rubric draft with the same fingerprint.
    3. If cached → returns cached draft (same paper always gives same rubric).
    4. If not cached or force_regenerate=true → generates fresh rubric, caches result.
    """

    question_path = None
    textbook_path = None

    try:
        # --------------------------------------------------
        # Validate inputs
        # --------------------------------------------------
        exam_id = exam_id.strip()
        if not exam_id:
            raise HTTPException(400, "exam_id is required.")

        if not question_paper.filename:
            raise HTTPException(400, "Question paper is required.")

        ext = Path(question_paper.filename).suffix.lower()
        mime = ALLOWED_MIME.get(ext)
        if not mime:
            raise HTTPException(400, "Question paper must be PDF, JPG, or PNG.")

        q_bytes = await question_paper.read()
        if not q_bytes:
            raise HTTPException(400, "Question paper file is empty.")

        should_force = force_regenerate.lower() in ("true", "1", "yes")

        # --------------------------------------------------
        # Document fingerprint
        # --------------------------------------------------
        doc_fingerprint = compute_doc_fingerprint(q_bytes)

        # Reference material fingerprint
        ref_bytes = b""
        if textbook and textbook.filename:
            t_ext = Path(textbook.filename).suffix.lower()
            if ALLOWED_MIME.get(t_ext):
                ref_bytes = await textbook.read() or b""
        ref_fingerprint = compute_doc_fingerprint(ref_bytes) if ref_bytes else ""

        # Determine model name for fingerprint
        if AI_PROVIDER == "ollama":
            model_name = model_router.ai_config.complex_model  # gemma4:e4b for visual
        else:
            model_name = GEMINI_MODEL

        gen_fingerprint = compute_generation_fingerprint(
            doc_fingerprint=doc_fingerprint,
            model_name=model_name,
            schema_version=RUBRIC_SCHEMA_VERSION,
            prompt_version=RUBRIC_PROMPT_VERSION,
            ref_fingerprint=ref_fingerprint,
        )

        # --------------------------------------------------
        # Check cache first (unless force-regenerate)
        # --------------------------------------------------
        if not should_force:
            cached = get_cached_draft(gen_fingerprint)
            if cached:
                print(f"[ExamSetup] Cache hit for gen_fingerprint={gen_fingerprint[:12]}… returning cached rubric.")
                # Register exam (idempotent)
                _register_exam(exam_id, cached)
                return {
                    "success": True,
                    "exam_id": exam_id,
                    "rubric": cached,
                    "doc_fingerprint": doc_fingerprint,
                    "gen_fingerprint": gen_fingerprint,
                    "from_cache": True,
                }

        # --------------------------------------------------
        # Write question paper to temp file
        # --------------------------------------------------
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            tmp.write(q_bytes)
            question_path = tmp.name

        # Write reference material if any
        if ref_bytes:
            t_ext = Path(textbook.filename).suffix.lower()
            with tempfile.NamedTemporaryFile(delete=False, suffix=t_ext) as tmp:
                tmp.write(ref_bytes)
                textbook_path = tmp.name

        rubric_data = None

        if AI_PROVIDER == "ollama":
            # --------------------------------------------------
            # LOCAL OLLAMA PATH — Gemma 4 E4B for visual papers
            # --------------------------------------------------
            print(f"[ExamSetup] Generating rubric via local ModelRouter (doc_fp={doc_fingerprint[:12]}…)")
            paper_images = []
            if ext == ".pdf":
                paper_images = model_router.ollama.render_pdf_to_images(question_path, max_pages=6, dpi=150)
            else:
                paper_images = [question_path]

            # Include reference material images if any
            if textbook_path:
                ref_ext = Path(textbook_path).suffix.lower()
                if ref_ext == ".pdf":
                    ref_images = model_router.ollama.render_pdf_to_images(textbook_path, max_pages=3, dpi=100)
                    paper_images = paper_images + ref_images
                elif ref_ext in (".jpg", ".jpeg", ".png"):
                    paper_images = paper_images + [textbook_path]

            res = model_router.generate(
                task_type="rubric_generation",
                prompt=RUBRIC_GENERATION_PROMPT,
                images=paper_images,
                format_json=True,
                temperature=0,          # Deterministic generation
                is_rubric_from_scanned_paper=True,
            )

            if not res.parsed_json or not isinstance(res.parsed_json, dict):
                raise HTTPException(
                    500,
                    f"Local model returned invalid rubric structure. Raw response: {res.text[:300]}"
                )
            rubric_data = res.parsed_json

        else:
            # --------------------------------------------------
            # PRESERVED GEMINI CLOUD PATH
            # --------------------------------------------------
            client = get_gemini_client()
            if not client:
                raise HTTPException(500, "Gemini client not initialized.")

            question_file = client.files.upload(
                file=question_path,
                config={"mime_type": mime}
            )

            textbook_file = None
            if textbook_path:
                t_ext = Path(textbook_path).suffix.lower()
                t_mime = ALLOWED_MIME.get(t_ext)
                if t_mime:
                    textbook_file = client.files.upload(
                        file=textbook_path,
                        config={"mime_type": t_mime}
                    )

            contents = [RUBRIC_GENERATION_PROMPT, question_file]
            if textbook_file:
                contents.extend(["\nREFERENCE TEXTBOOK:\n", textbook_file])

            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=contents,
                config={
                    "response_mime_type": "application/json",
                    "temperature": 0,
                }
            )

            if not response.text:
                raise HTTPException(500, "Gemini returned empty rubric.")
            rubric_data = json.loads(response.text)

        # --------------------------------------------------
        # VALIDATE & NORMALIZE RUBRIC DATA
        # --------------------------------------------------
        questions_list = rubric_data.get("questions", [])
        if not questions_list:
            raise HTTPException(500, "No questions were extracted from the question paper.")

        validated_questions = []
        for idx, q in enumerate(questions_list):
            q_num_raw = q.get("question_number", idx + 1)
            try:
                q_num_str = re.sub(r"[^\d]", "", str(q_num_raw))
                q_num = int(q_num_str) if q_num_str else (idx + 1)
            except Exception:
                q_num = idx + 1

            q_text = str(q.get("question_text", f"Question {q_num}")).strip()
            q_type = str(q.get("question_type", "THEORY")).upper()
            if q_type not in {"THEORY", "NUMERICAL", "DIAGRAM", "MIXED"}:
                q_type = "THEORY"

            section = str(q.get("section", "")).strip()
            total_marks = float(q.get("max_marks", q.get("total_marks", 0.0)))

            criteria_raw = q.get("criteria", [])
            validated_criteria = []
            criteria_sum = 0.0

            for c in criteria_raw:
                desc = str(c.get("description", "Evaluation criterion")).strip()
                c_marks = float(c.get("max_marks", c.get("marks", 0.0)))
                validated_criteria.append({"description": desc, "max_marks": c_marks})
                criteria_sum += c_marks

            if not validated_criteria:
                validated_criteria.append({
                    "description": "Comprehensive and accurate answer demonstrating full understanding",
                    "max_marks": total_marks
                })
                criteria_sum = total_marks

            # Enforce mathematical consistency: sum(criteria) == total_marks
            if total_marks > 0 and abs(criteria_sum - total_marks) > 0.01 and criteria_sum > 0:
                scale = total_marks / criteria_sum
                new_sum = 0.0
                for c in validated_criteria:
                    c["max_marks"] = round(c["max_marks"] * scale, 1)
                    new_sum += c["max_marks"]
                diff = round(total_marks - new_sum, 1)
                validated_criteria[-1]["max_marks"] = max(0.5, validated_criteria[-1]["max_marks"] + diff)
            elif total_marks == 0 and criteria_sum > 0:
                # Marks missing from question — use criteria sum as fallback
                total_marks = criteria_sum

            entry = {
                "question_number": q_num,
                "question_text": q_text,
                "question_type": q_type,
                "max_marks": total_marks,
                "criteria": validated_criteria,
            }
            if section:
                entry["section"] = section

            validated_questions.append(entry)

        # Compute totals
        computed_total = sum(q.get("max_marks", 0.0) for q in validated_questions)
        sections = sorted(set(
            q.get("section", "") for q in validated_questions if q.get("section")
        ))

        # Run structural validation — collect warnings, don't block
        warnings = validate_rubric_structure({"questions": validated_questions})

        rubric_data = {
            "questions": validated_questions,
            "total_marks": computed_total,
            "sections": sections,
            "extraction_warnings": warnings,
            "doc_fingerprint": doc_fingerprint,
            "gen_fingerprint": gen_fingerprint,
            "schema_version": RUBRIC_SCHEMA_VERSION,
            "prompt_version": RUBRIC_PROMPT_VERSION,
        }

        # Cache the validated result
        store_draft_cache(gen_fingerprint, rubric_data)

        # Register exam in Firestore (idempotent)
        _register_exam(exam_id, rubric_data)

        return {
            "success": True,
            "exam_id": exam_id,
            "rubric": rubric_data,
            "doc_fingerprint": doc_fingerprint,
            "gen_fingerprint": gen_fingerprint,
            "from_cache": False,
        }

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
        if textbook_path:
            try:
                Path(textbook_path).unlink(missing_ok=True)
            except Exception:
                pass


def _register_exam(exam_id: str, rubric_data: dict) -> None:
    """Idempotent exam registration in Firestore."""
    try:
        total_marks = rubric_data.get("total_marks", 0.0)
        db_service.save_exam(
            exam_id=exam_id,
            exam_name=exam_id,
            total_marks=total_marks,
            status="PROPOSED",
        )
    except Exception as e:
        print(f"[ExamSetup] DB exam registration skipped: {e}")


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

        # Validate before saving
        rubric_dict = {"questions": [
            q.model_dump() if hasattr(q, "model_dump") else q
            for q in request.rubric
        ]}
        warnings = validate_rubric_structure(rubric_dict)

        if not db_service.is_connected():
            return {
                "success": True,
                "exam_id": exam_id,
                "rubric_id": f"{exam_id}_v1",
                "version": 1,
                "warnings": warnings,
                "message": "Rubric approved (Firestore offline — local fallback mode).",
            }

        # Save to Firestore
        saved = db_service.save_approved_rubric(
            exam_id=exam_id,
            questions=request.rubric,
            doc_fingerprint=getattr(request, "doc_fingerprint", ""),
            gen_fingerprint=getattr(request, "gen_fingerprint", ""),
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
