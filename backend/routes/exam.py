"""
AutoGrade — Exam Setup Routes

POST /exam/setup         — Generate proposed rubric via RAG
POST /exam/rubric/approve — Save approved rubric to Firestore
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Form, HTTPException

from config import get_gemini_client, get_firestore, GEMINI_MODEL
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
# RUBRIC SCHEMA FOR GEMINI
# =========================================================

RUBRIC_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question_number": {"type": "integer"},
                    "question_text": {"type": "string"},
                    "max_marks": {"type": "number"},
                    "criteria": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "description": {"type": "string"},
                                "max_marks": {"type": "number"},
                            },
                            "required": ["description", "max_marks"]
                        }
                    }
                },
                "required": [
                    "question_number",
                    "question_text",
                    "max_marks",
                    "criteria",
                ]
            }
        }
    },
    "required": ["questions"]
}


# =========================================================
# POST /exam/setup
# =========================================================

@router.post("/setup")
async def exam_setup(
    exam_id: str = Form(...),
    question_paper: UploadFile = File(...),
    textbook: UploadFile | None = File(None),
    max_marks_per_question: str = Form("{}"),
):
    """
    Upload question paper + optional textbook.
    Runs RAG pipeline and returns proposed rubric for teacher approval.
    """

    question_path = None
    textbook_path = None

    try:
        # Validate question paper
        if not question_paper.filename:
            raise HTTPException(400, "Question paper is required.")

        ext = Path(question_paper.filename).suffix.lower()
        mime = ALLOWED_MIME.get(ext)
        if not mime:
            raise HTTPException(400, "Question paper must be PDF, JPG, or PNG.")

        q_bytes = await question_paper.read()
        if not q_bytes:
            raise HTTPException(400, "Question paper is empty.")

        # Save temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            tmp.write(q_bytes)
            question_path = tmp.name

        # Upload to Gemini
        client = get_gemini_client()
        question_file = client.files.upload(
            file=question_path,
            config={"mime_type": mime}
        )

        # Optional textbook — process for RAG
        textbook_file = None
        if textbook and textbook.filename:
            t_ext = Path(textbook.filename).suffix.lower()
            t_mime = ALLOWED_MIME.get(t_ext)
            if not t_mime:
                raise HTTPException(400, "Textbook must be PDF, JPG, or PNG.")

            t_bytes = await textbook.read()
            if t_bytes:
                with tempfile.NamedTemporaryFile(delete=False, suffix=t_ext) as tmp:
                    tmp.write(t_bytes)
                    textbook_path = tmp.name

                textbook_file = client.files.upload(
                    file=textbook_path,
                    config={"mime_type": t_mime}
                )

                # Extract text from textbook for RAG indexing
                try:
                    text_response = client.models.generate_content(
                        model=GEMINI_MODEL,
                        contents=[
                            "Extract all text content from this document. "
                            "Return plain text only, preserving structure.",
                            textbook_file,
                        ],
                        config={"temperature": 0.0}
                    )
                    if text_response.text:
                        setup_rubric_knowledge(exam_id, text_response.text)
                except Exception as error:
                    print(f"[ExamSetup] RAG indexing error: {error}")

        # Generate rubric via Gemini
        contents = [
            """
You are the Rubric Generation Module of AutoGrade.
Analyze the uploaded question paper. For every question:
1. Extract the question number
2. Extract the question text accurately
3. Identify total marks
4. Create a clear marking rubric with criteria
5. The sum of all criteria max_marks MUST equal the
   total marks of that question

Return only the required JSON.
""",
            question_file,
        ]

        if textbook_file:
            contents.append("\nREFERENCE TEXTBOOK:\n")
            contents.append(textbook_file)

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config={
                "response_mime_type": "application/json",
                "response_json_schema": RUBRIC_RESPONSE_SCHEMA,
                "temperature": 0.1,
            }
        )

        if not response.text:
            raise HTTPException(500, "Gemini returned empty rubric.")

        rubric_data = json.loads(response.text)

        # Parse max_marks overrides
        try:
            marks_override = json.loads(max_marks_per_question)
        except json.JSONDecodeError:
            marks_override = {}

        # Apply overrides
        for q in rubric_data.get("questions", []):
            q_num = q.get("question_number")
            if q_num and str(q_num) in marks_override:
                q["max_marks"] = marks_override[str(q_num)]

        return {
            "success": True,
            "exam_id": exam_id,
            "rubric": rubric_data,
        }

    except HTTPException:
        raise

    except Exception as error:
        print(f"[ExamSetup] Error: {repr(error)}")
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


# =========================================================
# POST /exam/rubric/approve
# =========================================================

@router.post("/rubric/approve")
async def approve_rubric(request: RubricApproveRequest):
    """Save the teacher-approved rubric to Firestore."""

    try:
        db = get_firestore()
        if db is None:
            # Local dev mode — just return success
            return {
                "success": True,
                "exam_id": request.exam_id,
                "message": "Rubric approved (Firestore unavailable — local mode).",
            }

        doc_ref = (
            db.collection("exams")
            .document(request.exam_id)
            .collection("rubric")
            .document("approved")
        )

        doc_ref.set({
            "exam_id": request.exam_id,
            "questions": [q.model_dump() for q in request.rubric],
            "approved": True,
        })

        return {
            "success": True,
            "exam_id": request.exam_id,
            "message": "Rubric approved and saved to Firestore.",
        }

    except Exception as error:
        raise HTTPException(500, f"Failed to save rubric: {error}")
