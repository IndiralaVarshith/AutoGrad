"""
AutoGrade — Grading Agent

Evaluates student answers against the teacher-approved rubric
using Gemini 2.0 Flash at T=0.1. For each criterion, cites
the EXACT substring from the student answer as verbatim_quote.
"""

from __future__ import annotations

import json
import math
import re

from models.schemas import CriterionResult, QuestionRubric
from config import AI_PROVIDER, OLLAMA_GRADER_MODEL, gemini_generate_with_retry
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
You are an experienced teacher marking a student's handwritten exam answer against
the rubric. The answer was machine-read from handwriting.

How to mark:
- Judge the meaning. The student's own wording earns marks if it shows the idea.
  Do not require the rubric's words.
- Never take marks off for spelling, grammar or awkward English.
- Slashes, "or" and lists in a rubric are alternatives or examples. One correct
  item is enough unless the criterion says all are needed. A correct example the
  rubric does not list also counts.
- Mark generously, as a classroom teacher would. Give each criterion full marks if
  the main idea is there, even if brief, loosely worded or incomplete. Give half
  marks to any attempt that is on the topic and shows some related understanding.
  Give 0 only if the answer is about something else or plainly says the opposite.
  Use steps of 0.5.
- Reasons to give 0 that are NOT valid: the rubric's term is missing, the answer is
  vague, the sentence is badly formed, or the answer is short.

For each criterion, copy the criterion text exactly as "criterion". If you award
marks, cite the EXACT substring of the student answer that earns them as
verbatim_quote. If nothing earns marks, set verbatim_quote to "" and marks_awarded
to 0. Never award marks without a verbatim quote.

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
    avoid_model: str | None = None,
    avoid_models: list[str] | set[str] | None = None,
    task_category: str | None = None,
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

    criteria_budget = sum(criterion.max_marks for criterion in rubric.criteria)
    if not math.isfinite(max_marks) or max_marks < 0:
        raise ValueError(f"Invalid approved maximum for question {rubric.question_number}.")
    if any(
        not math.isfinite(criterion.max_marks) or criterion.max_marks < 0
        for criterion in rubric.criteria
    ) or criteria_budget > max_marks:
        raise ValueError(
            f"Approved criteria for question {rubric.question_number} total "
            f"{criteria_budget:.2f}, exceeding its maximum of {max_marks:.2f}."
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

Grade this answer. For each criterion, cite EXACT text from
the student answer as verbatim_quote. Return JSON only.
"""

    complex_reasoning_verbs = {
        "analyze", "analyse", "evaluate", "justify", "derive", "prove", "compare",
        "contrast", "critically", "synthesize", "synthesise", "multi-step", "multi step",
    }
    question_lower = question_text.casefold()
    if task_category is None and (
        len(rubric.criteria) >= 4
        or any(verb in question_lower for verb in complex_reasoning_verbs)
    ):
        task_category = "high_complexity"

    excluded = {avoid_model, *(avoid_models or [])}
    use_configured_grader = AI_PROVIDER != "gemini" and OLLAMA_GRADER_MODEL not in excluded

    res = model_router.generate(
        task_type="grading",
        prompt=user_prompt,
        images=image_parts if image_parts else None,
        system_instruction=GRADING_SYSTEM_PROMPT,
        format_json=True,
        temperature=temperature,
        question_type=question_type,
        illegible_count=illegible_count,
        avoid_model=avoid_model,
        avoid_models=avoid_models,
        force_model=OLLAMA_GRADER_MODEL if use_configured_grader else None,
        task_category=task_category,
        requires_reasoning=(task_category == "high_complexity") if task_category else None,
    )

    data = res.parsed_json
    if not isinstance(data, dict):
        try:
            data = json.loads(res.text)
        except (json.JSONDecodeError, TypeError) as error:
            raise RuntimeError("Grading agent returned invalid structured output.") from error
    if not isinstance(data, dict) or not isinstance(data.get("criterion_results"), list):
        raise RuntimeError("Grading agent response omitted criterion_results.")

    # -------------------------------------------------
    # Parse into CriterionResult models
    # -------------------------------------------------
    criteria: list[CriterionResult] = []
    rubric_by_description = {
        re.sub(r"\s+", " ", item.description).strip().casefold(): item
        for item in rubric.criteria
    }
    seen_criteria: set[str] = set()
    for item in data.get("criterion_results", []):
        criterion_desc = str(item.get("criterion", "")).strip()
        criterion_key = re.sub(r"\s+", " ", criterion_desc).casefold()
        rubric_criterion = rubric_by_description.get(criterion_key)
        if rubric_criterion is None or criterion_key in seen_criteria:
            continue
        seen_criteria.add(criterion_key)
        marks = float(item.get("marks_awarded", 0))
        if (
            not math.isfinite(marks)
            or marks < 0
            or marks > rubric_criterion.max_marks
        ):
            raise ValueError(
                f"Grader awarded invalid marks for criterion '{criterion_desc}' "
                f"({marks}; approved maximum {rubric_criterion.max_marks})."
            )

        criteria.append(
            CriterionResult(
                criterion=criterion_desc,
                verbatim_quote=item.get("verbatim_quote", ""),
                marks_awarded=marks,
                rationale=item.get("rationale", ""),
                quote_verified=False,
            )
        )

    # Missing rubric criteria receive no marks and remain visible in the report.
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
    elif len(seen_criteria) < len(rubric.criteria):
        for rc in rubric.criteria:
            key = re.sub(r"\s+", " ", rc.description).strip().casefold()
            if key not in seen_criteria:
                criteria.append(
                    CriterionResult(
                        criterion=rc.description,
                        verbatim_quote="",
                        marks_awarded=0.0,
                        rationale="Grading agent did not return this approved rubric criterion",
                        quote_verified=False,
                    )
                )

    total_score = sum(c.marks_awarded for c in criteria)
    if total_score > max_marks:
        raise ValueError(
            f"Grader criteria total {total_score:.2f} exceeds the approved "
            f"maximum {max_marks:.2f} for question {rubric.question_number}."
        )

    print(f"[Grader] Model: {res.model_used} | Score: {total_score}/{max_marks} | Reason: {res.routing_reason}")

    return criteria, total_score, res.model_used, res.routing_reason
