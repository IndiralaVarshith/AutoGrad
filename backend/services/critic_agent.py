"""
AutoGrade — Critic Agent

Adversarial academic reviewer that checks the Grading Agent's
output for errors: marks awarded without evidence, missed
penalties, misidentified criteria.

Uses Gemini 2.0 Flash at T=0.3.
"""

from __future__ import annotations

import json

from models.schemas import CriterionResult, QuestionRubric, CriticVerdict
from config import gemini_generate_with_retry


# =========================================================
# CRITIC RESPONSE SCHEMA
# =========================================================

CRITIC_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["ACCEPT", "REJECT"]
        },
        "challenged_criteria": {
            "type": "array",
            "items": {"type": "string"}
        },
        "suggested_score": {"type": "number"},
        "reason": {"type": "string"}
    },
    "required": ["verdict", "challenged_criteria", "suggested_score", "reason"]
}


# =========================================================
# CRITIC SYSTEM PROMPT
# =========================================================

CRITIC_SYSTEM_PROMPT = """
You are an adversarial academic reviewer. Your job is to
find errors in the grading decision provided. Look for:
- awarded marks with no real evidence
- missed concepts that should have been penalised
- misidentified criteria
- verbatim quotes that do not actually appear in the student answer

Return JSON:
{
  "verdict": "ACCEPT" or "REJECT",
  "challenged_criteria": [list of criterion descriptions with issues],
  "suggested_score": float,
  "reason": "explanation of why you accept or reject"
}
"""


# =========================================================
# CRITIQUE GRADE
# =========================================================

def critique_grade(
    question_text: str,
    extracted_text: str,
    image_parts: list,
    rubric: QuestionRubric,
    grader_criteria: list[CriterionResult],
    grader_score: float,
    max_marks: float,
) -> CriticVerdict:
    """
    Review the Grading Agent's output adversarially.

    Parameters
    ----------
    question_text : str
        The exam question text.
    extracted_text : str
        OCR-extracted student answer text.
    image_parts : list
        Original answer sheet image parts.
    rubric : QuestionRubric
        The approved rubric.
    grader_criteria : list[CriterionResult]
        The Grading Agent's criterion-level results.
    grader_score : float
        Total score from the Grading Agent.
    max_marks : float
        Maximum marks for this question.

    Returns
    -------
    CriticVerdict
        The critic's verdict with challenged criteria and reason.
    """


    grading_json = json.dumps(
        [c.model_dump() for c in grader_criteria],
        indent=2,
    )

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

GRADING AGENT OUTPUT (score: {grader_score}/{max_marks}):
{grading_json}

Review this grading. Return JSON only.
"""

    contents = [user_prompt] + image_parts

    response = gemini_generate_with_retry(
        contents=contents,
        config={
            "system_instruction": CRITIC_SYSTEM_PROMPT,
            "response_mime_type": "application/json",
            "response_json_schema": CRITIC_RESPONSE_SCHEMA,
            "temperature": 0.3,
        }
    )

    if not response.text:
        # If critic fails, default to ACCEPT
        return CriticVerdict(
            verdict="ACCEPT",
            reason="Critic agent returned no output; defaulting to ACCEPT.",
        )

    data = json.loads(response.text)

    verdict = CriticVerdict(
        verdict=data.get("verdict", "ACCEPT"),
        challenged_criteria=data.get("challenged_criteria", []),
        suggested_score=float(data.get("suggested_score", grader_score)),
        reason=data.get("reason", ""),
    )

    print(f"[Critic] Verdict: {verdict.verdict}, suggested: {verdict.suggested_score}")

    return verdict
