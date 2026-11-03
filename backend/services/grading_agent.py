"""
AutoGrade — Grading Agent

Evaluates student answers against the teacher-approved rubric
using Gemini 2.0 Flash at T=0.1. For each criterion, cites
the EXACT substring from the student answer as verbatim_quote.
"""

from __future__ import annotations

import json

from models.schemas import CriterionResult, QuestionRubric
from config import gemini_generate_with_retry
from services.router import model_router


# =========================================================
# GRADING RESPONSE SCHEMA
# =========================================================

GRADING_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "criterion_results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "criterion": {"type": "string"},
                    "verbatim_quote": {"type": "string"},
                    "marks_awarded": {"type": "number"},
                    "rationale": {"type": "string"},
                },
                "required": [
                    "criterion",
                    "verbatim_quote",
                    "marks_awarded",
                    "rationale",
                ]
            }
        },
        "total_score": {"type": "number"}
    },
    "required": ["criterion_results", "total_score"]
}


# =========================================================
# GRADING PROMPT
# =========================================================

GRADING_SYSTEM_PROMPT = """
You are an academic grader. Evaluate the student answer
strictly against the provided rubric. For each criterion,
cite the EXACT substring from the student answer that
satisfies it as verbatim_quote. If no evidence exists,
set verbatim_quote to "" and marks_awarded to 0.
Never award marks without a verbatim quote.

Return JSON with:
- criterion_results: list of {criterion, verbatim_quote,
  marks_awarded, rationale}
- total_score: float (sum of marks_awarded, capped at max_marks)
"""


# =========================================================
# GRADE ANSWER
# =========================================================

def grade_answer(
    question_text: str,
    extracted_text: str,
    image_parts: list,
    rubric: QuestionRubric,
    max_marks: float,
    temperature: float = 0.1,
    question_type: str = "THEORY",
    illegible_count: int = 0,
) -> tuple[list[CriterionResult], float, str, str]:
    """
    Grade a student's answer against the rubric.

    Parameters
    ----------
    question_text : str
        The exam question text.
    extracted_text : str
        OCR-extracted student answer text.
    image_parts : list
        Original answer sheet image parts for visual inspection.
    rubric : QuestionRubric
        The approved rubric for this question.
    max_marks : float
        Maximum marks for this question.
    temperature : float
        Sampling temperature (default 0.1).
    question_type : str
        Question category (THEORY, NUMERICAL, DIAGRAM, MIXED).
    illegible_count : int
        Number of illegible OCR tokens.

    Returns
    -------
    criteria : list[CriterionResult]
        Grading results per criterion.
    total_score : float
        Total marks awarded.
    model_used : str
        Model identifier that evaluated this question.
    routing_reason : str
        Reason for model selection.
    """

    rubric_json = json.dumps(
        [
            {"description": c.description, "max_marks": c.max_marks}
            for c in rubric.criteria
        ],
        indent=2,
    )

    user_prompt = f"""
QUESTION:
{question_text}

STUDENT ANSWER (OCR):
{extracted_text}

RUBRIC (max {max_marks} marks):
{rubric_json}

Grade this answer. For each criterion, cite EXACT text from
the student answer as verbatim_quote. Return JSON only.
"""

    res = model_router.generate(
        task_type="grading",
        prompt=user_prompt,
        images=image_parts if image_parts else None,
        system_instruction=GRADING_SYSTEM_PROMPT,
        format_json=True,
        temperature=temperature,
        question_type=question_type,
        illegible_count=illegible_count,
    )

    data = res.parsed_json or {}
    if not isinstance(data, dict):
        try:
            data = json.loads(res.text)
        except Exception:
            data = {}

    # -------------------------------------------------
    # Parse into CriterionResult models
    # -------------------------------------------------
    criteria: list[CriterionResult] = []
    for item in data.get("criterion_results", []):
        marks = float(item.get("marks_awarded", 0))

        # Find the matching rubric criterion to enforce max
        criterion_desc = item.get("criterion", "")
        max_for_criterion = max_marks  # fallback
        for rc in rubric.criteria:
            if rc.description == criterion_desc:
                max_for_criterion = rc.max_marks
                break

        marks = max(0.0, min(marks, max_for_criterion))

        criteria.append(
            CriterionResult(
                criterion=criterion_desc,
                verbatim_quote=item.get("verbatim_quote", ""),
                marks_awarded=marks,
                rationale=item.get("rationale", ""),
                quote_verified=False,
            )
        )

    # If model failed to return criteria, create fallback criteria
    if not criteria and rubric.criteria:
        for rc in rubric.criteria:
            criteria.append(
                CriterionResult(
                    criterion=rc.description,
                    verbatim_quote="",
                    marks_awarded=0.0,
                    rationale="Model did not return structured criterion evaluation",
                    quote_verified=False,
                )
            )

    total_score = min(
        sum(c.marks_awarded for c in criteria),
        max_marks
    )

    print(f"[Grader] Model: {res.model_used} | Score: {total_score}/{max_marks} | Reason: {res.routing_reason}")

    return criteria, total_score, res.model_used, res.routing_reason
