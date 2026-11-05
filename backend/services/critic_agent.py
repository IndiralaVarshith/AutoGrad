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

# Marks can differ by this much and still count as the two markers agreeing.
AGREEMENT_GAP = 0.5

CRITIC_SYSTEM_PROMPT = """
You are the second marker on a handwritten exam answer. Work out your own mark for
the whole answer, then compare it with the first marker's.

Mark by the same standard: judge the meaning, not the wording. Never take marks off
for spelling, grammar or awkward English. Slashes, "or" and lists in a rubric are
alternatives or examples, so one correct item is enough unless the criterion says
all are needed. Full marks if the idea is right, half if partly right or vague, 0
only if wrong, unrelated or missing. Use steps of 0.5.

Also check that each quote really appears in the student answer.

Return JSON:
{
  "verdict": "ACCEPT" or "REJECT",
  "challenged_criteria": [list of criterion descriptions with issues],
  "suggested_score": float (your own mark for the whole answer),
  "reason": "explanation"
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
    avoid_model: str | None = None,
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
        },
        task_category="critical_validation",
        requires_vision=bool(image_parts),
        requires_reasoning=True,
        avoid_model=avoid_model,
    )

    if not response.text:
        raise RuntimeError("Critic agent returned an empty response.")

    data = json.loads(response.text)

    raw_challenged = data.get("challenged_criteria", [])
    clean_challenged = []
    for item in raw_challenged:
        if isinstance(item, dict):
            clean_challenged.append(str(item.get("description", item.get("criterion", str(item)))))
        else:
            clean_challenged.append(str(item))

    suggested_score = float(data.get("suggested_score", grader_score))
    # Agreement is decided from the two marks, not from the model's choice of word.
    verdict = CriticVerdict(
        verdict="ACCEPT" if abs(suggested_score - grader_score) <= AGREEMENT_GAP else "REJECT",
        challenged_criteria=clean_challenged,
        suggested_score=suggested_score,
        reason=data.get("reason", ""),
        model_used=response.model_used,
    )

    print(f"[Critic] Verdict: {verdict.verdict}, suggested: {verdict.suggested_score}")

    return verdict
