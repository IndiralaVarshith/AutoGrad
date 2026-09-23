import os
import json
import tempfile
from pathlib import Path

from dotenv import load_dotenv

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from google import genai

from grading_engine import GradingEngine, MODEL_NAME


# =========================================================
# LOAD ENVIRONMENT
# =========================================================

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY is missing from backend/.env"
    )


# =========================================================
# GEMINI / GRADING ENGINE
# =========================================================

grading_engine = GradingEngine(
    GEMINI_API_KEY
)


# =========================================================
# FASTAPI
# =========================================================

app = FastAPI(
    title="AutoGrade Backend",
    description="AutoGrade AI grading backend",
    version="2.0.0"
)


# =========================================================
# CORS
# =========================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)


# =========================================================
# FILE TYPES
# =========================================================

ALLOWED_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png"
}


# =========================================================
# RUBRIC SCHEMA
# =========================================================

RUBRIC_SCHEMA = {
    "type": "object",

    "properties": {
        "exam_title": {
            "type": "string"
        },

        "questions": {
            "type": "array",

            "items": {
                "type": "object",

                "properties": {
                    "question_number": {
                        "type": "string"
                    },

                    "question_text": {
                        "type": "string"
                    },

                    "question_type": {
                        "type": "string",

                        "enum": [
                            "Theory",
                            "Diagram",
                            "Numerical",
                            "Mixed"
                        ]
                    },

                    "total_marks": {
                        "type": "integer"
                    },

                    "criteria": {
                        "type": "array",

                        "items": {
                            "type": "object",

                            "properties": {
                                "criterion": {
                                    "type": "string"
                                },

                                "marks": {
                                    "type": "integer"
                                },

                                "description": {
                                    "type": "string"
                                }
                            },

                            "required": [
                                "criterion",
                                "marks",
                                "description"
                            ]
                        }
                    }
                },

                "required": [
                    "question_number",
                    "question_text",
                    "question_type",
                    "total_marks",
                    "criteria"
                ]
            }
        }
    },

    "required": [
        "exam_title",
        "questions"
    ]
}


# =========================================================
# ROOT
# =========================================================

@app.get("/")
def root():
    return {
        "status": "running",
        "service": "AutoGrade Backend",
        "model": MODEL_NAME,
        "grading_pipeline": "enabled"
    }


# =========================================================
# GENERATE RUBRIC
# =========================================================

@app.post("/generate-rubric")
async def generate_rubric(
    question_paper: UploadFile = File(...),
    textbook: UploadFile | None = File(None)
):

    question_path = None
    textbook_path = None

    try:

        # -------------------------------------------------
        # QUESTION PAPER VALIDATION
        # -------------------------------------------------

        if not question_paper.filename:
            raise HTTPException(
                status_code=400,
                detail="Question paper is required."
            )

        q_extension = Path(
            question_paper.filename
        ).suffix.lower()

        q_mime = ALLOWED_MIME_TYPES.get(
            q_extension
        )

        if not q_mime:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Question paper must be "
                    "PDF, JPG, JPEG or PNG."
                )
            )


        # -------------------------------------------------
        # READ QUESTION PAPER
        # -------------------------------------------------

        q_bytes = await question_paper.read()

        if not q_bytes:
            raise HTTPException(
                status_code=400,
                detail="Question paper is empty."
            )


        # -------------------------------------------------
        # SAVE QUESTION PAPER
        # -------------------------------------------------

        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=q_extension
        ) as temp_q:

            temp_q.write(q_bytes)

            question_path = temp_q.name


        # -------------------------------------------------
        # UPLOAD QUESTION PAPER TO GEMINI
        # -------------------------------------------------

        question_file = grading_engine._call_gemini(
            lambda: grading_engine.client.files.upload(
                file=question_path,
                config={
                    "mime_type": q_mime
                }
            ),
            "Uploading question paper"
        )


        print(
            f"Question paper uploaded: "
            f"{question_paper.filename}"
        )


        # -------------------------------------------------
        # OPTIONAL TEXTBOOK
        # -------------------------------------------------

        textbook_file = None


        if textbook and textbook.filename:

            t_extension = Path(
                textbook.filename
            ).suffix.lower()

            t_mime = ALLOWED_MIME_TYPES.get(
                t_extension
            )

            if not t_mime:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Textbook must be "
                        "PDF, JPG, JPEG or PNG."
                    )
                )


            t_bytes = await textbook.read()

            if not t_bytes:
                raise HTTPException(
                    status_code=400,
                    detail="Textbook is empty."
                )


            with tempfile.NamedTemporaryFile(
                delete=False,
                suffix=t_extension
            ) as temp_t:

                temp_t.write(t_bytes)

                textbook_path = temp_t.name


            textbook_file = grading_engine._call_gemini(
                lambda: grading_engine.client.files.upload(
                    file=textbook_path,
                    config={
                        "mime_type": t_mime
                    }
                ),
                "Uploading textbook"
            )


            print(
                f"Textbook uploaded: "
                f"{textbook.filename}"
            )


        # -------------------------------------------------
        # PROMPT
        # -------------------------------------------------

        prompt = """
You are the Rubric Generation Module of AutoGrade.

Analyze the uploaded question paper.

For every question:

1. Extract the question number.
2. Extract the question text accurately.
3. Identify the question type as:
   Theory, Diagram, Numerical, or Mixed.
4. Identify the total marks.
5. Create a clear marking rubric.
6. Divide the total marks into meaningful criteria.
7. The sum of all criteria MUST equal the
   total marks of that question.

If a reference textbook is provided,
use it as the primary reference for
determining the concepts expected in
the answer.

Do not invent unrelated concepts.

Keep the rubric simple and practical
for teacher review.

Return only the required JSON.
"""


        # -------------------------------------------------
        # GEMINI CONTENT
        # -------------------------------------------------

        contents = [
            prompt,
            question_file
        ]


        if textbook_file:

            contents.append(
                "\nREFERENCE TEXTBOOK / "
                "REFERENCE MATERIAL:\n"
            )

            contents.append(
                textbook_file
            )


        # -------------------------------------------------
        # GENERATE RUBRIC
        # -------------------------------------------------

        response = grading_engine._call_gemini(

            lambda: grading_engine.client.models.generate_content(

                model=MODEL_NAME,

                contents=contents,

                config={
                    "response_mime_type": "application/json",
                    "response_json_schema": RUBRIC_SCHEMA,
                    "temperature": 0.1
                }

            ),

            "Rubric Generation"

        )


        # -------------------------------------------------
        # CHECK RESPONSE
        # -------------------------------------------------

        if not response.text:

            raise HTTPException(
                status_code=500,
                detail="Gemini returned an empty rubric."
            )


        # -------------------------------------------------
        # PARSE JSON
        # -------------------------------------------------

        try:

            rubric = json.loads(
                response.text
            )

        except json.JSONDecodeError:

            raise HTTPException(
                status_code=500,
                detail="Gemini returned invalid rubric JSON."
            )


        # -------------------------------------------------
        # RETURN
        # -------------------------------------------------

        return {
            "success": True,
            "model": MODEL_NAME,
            "rubric": rubric
        }


    except HTTPException:
        raise


    except Exception as error:

        print()
        print("========================================")
        print("RUBRIC GENERATION ERROR")
        print("========================================")
        print(repr(error))
        print("========================================")
        print()

        raise HTTPException(
            status_code=500,
            detail=str(error)
        )


    finally:

        if question_path:

            try:
                Path(question_path).unlink(
                    missing_ok=True
                )
            except Exception:
                pass


        if textbook_path:

            try:
                Path(textbook_path).unlink(
                    missing_ok=True
                )
            except Exception:
                pass


# =========================================================
# REAL ANSWER-SHEET GRADING
# =========================================================

@app.post("/process-answer-sheet")
async def process_answer_sheet(

    answer_sheet: UploadFile = File(...),

    rubric: str = ""

):

    answer_path = None

    try:

        # -------------------------------------------------
        # VALIDATE RUBRIC
        # -------------------------------------------------

        if not rubric:

            raise HTTPException(
                status_code=400,
                detail="Approved rubric is required."
            )


        try:

            rubric_data = json.loads(
                rubric
            )

        except json.JSONDecodeError:

            raise HTTPException(
                status_code=400,
                detail="Invalid rubric JSON."
            )


        # -------------------------------------------------
        # VALIDATE ANSWER SHEET
        # -------------------------------------------------

        if not answer_sheet.filename:

            raise HTTPException(
                status_code=400,
                detail="Answer sheet is required."
            )


        extension = Path(
            answer_sheet.filename
        ).suffix.lower()


        mime_type = ALLOWED_MIME_TYPES.get(
            extension
        )


        if not mime_type:

            raise HTTPException(
                status_code=400,
                detail=(
                    "Answer sheet must be "
                    "PDF, JPG, JPEG or PNG."
                )
            )


        # -------------------------------------------------
        # READ ANSWER SHEET
        # -------------------------------------------------

        answer_bytes = await answer_sheet.read()


        if not answer_bytes:

            raise HTTPException(
                status_code=400,
                detail="Answer sheet is empty."
            )


        # -------------------------------------------------
        # SAVE ANSWER SHEET
        # -------------------------------------------------

        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=extension
        ) as temp_answer:

            temp_answer.write(
                answer_bytes
            )

            answer_path = temp_answer.name


        print(
            f"Processing answer sheet: "
            f"{answer_sheet.filename}"
        )


        # -------------------------------------------------
        # RUN COMPLETE GRADING ENGINE
        # -------------------------------------------------

        result = grading_engine.process_answer_sheet(

            answer_path,

            mime_type,

            rubric_data

        )


        return result


    except HTTPException:
        raise


    except Exception as error:

        print()
        print("========================================")
        print("ANSWER PROCESSING ERROR")
        print("========================================")
        print(repr(error))
        print("========================================")
        print()

        raise HTTPException(
            status_code=500,
            detail=str(error)
        )


    finally:

        if answer_path:

            try:
                Path(answer_path).unlink(
                    missing_ok=True
                )
            except Exception:
                pass