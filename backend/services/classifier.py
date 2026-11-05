"""
AutoGrade — Question Type Classifier

Uses a single Gemini call to classify exam questions as
THEORY, NUMERICAL, or DIAGRAM.
"""

from __future__ import annotations

import json
import time

from config import gemini_generate_with_retry
from models.schemas import QuestionRubric
from services.router import model_router
from services.timing import log_timing


# =========================================================
# CLASSIFIER PROMPT
# =========================================================

CLASSIFIER_PROMPT = """
Classify this exam question as exactly one of:
THEORY, NUMERICAL, DIAGRAM.

THEORY = questions that require written explanations, definitions,
         essays, short answers, comparisons, or descriptive content.

NUMERICAL = questions that require mathematical calculations,
            solving equations, computations, or numerical problem solving.

DIAGRAM = questions that require drawing diagrams, flowcharts,
          circuit diagrams, graphs, or any visual representation.

Return ONLY the single label. Nothing else.
"""


# =========================================================
# CLASSIFY QUESTION
# =========================================================

def classify_question(question_text: str) -> str:
    """
    Classify a single exam question.

    Parameters
    ----------
    question_text : str
        The text of the exam question.

    Returns
    -------
    str
        One of: "THEORY", "NUMERICAL", "DIAGRAM"
    """

    response = gemini_generate_with_retry(
        contents=[
            CLASSIFIER_PROMPT,
            f"QUESTION:\n{question_text}"
        ],
        config={
            "temperature": 0.0,
        }
    )

    if not response.text:
        raise RuntimeError("Question classifier returned an empty response.")

    label = response.text.strip().upper()

    # Normalise to one of the three valid labels
    if "NUMERICAL" in label:
        return "NUMERICAL"
    elif "DIAGRAM" in label:
        return "DIAGRAM"
    else:
        return "THEORY"


_CLASSIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "classifications": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "question_type": {
                        "type": "string",
                        "enum": ["THEORY", "NUMERICAL", "DIAGRAM", "MIXED"],
                    },
                },
                "required": ["index", "question_type"],
            },
        },
    },
    "required": ["classifications"],
}


def classify_questions(
    rubrics: list[QuestionRubric],
    *,
    job_id: str = "",
    on_stage=None,
) -> dict[int, str]:
    """Reuse approved question types and classify only questions lacking one."""
    if not rubrics:
        return {}
    allowed_types = {"THEORY", "NUMERICAL", "DIAGRAM", "MIXED"}
    result = {
        index: rubric.question_type.strip().upper()
        for index, rubric in enumerate(rubrics)
        if rubric.question_type
        and rubric.question_type.strip().upper() in allowed_types
    }
    pending = [index for index in range(len(rubrics)) if index not in result]
    if not pending:
        return result
    if on_stage is not None:
        on_stage(
            "identifying_answers",
            f"Classifying {len(pending)} questions without an approved type",
        )

    prompt = (
        "Classify each numbered exam question as THEORY, NUMERICAL, DIAGRAM, or MIXED. "
        "Use MIXED only when both written reasoning and a diagram/calculation are required. "
        "Return one classification for every input index, without adding or omitting indices.\n"
        + json.dumps(
            [
                {"index": index, "question_text": rubric.question_text}
                for index, rubric in enumerate(rubrics)
                if index in pending
            ],
            ensure_ascii=False,
        )
    )
    started_at = time.perf_counter()
    try:
        response = model_router.generate(
            task_type="classification",
            prompt=prompt,
            format_json=True,
            temperature=0.0,
            max_tokens=max(128, len(pending) * 32),
        )
        data = response.parsed_json
        if not isinstance(data, (dict, list)):
            try:
                data = json.loads(response.text)
            except json.JSONDecodeError:
                data = None
        if isinstance(data, list):
            classifications = data
        elif isinstance(data, dict):
            classifications = data.get("classifications")
            if classifications is None:
                classifications = next(
                    (
                        value
                        for value in data.values()
                        if isinstance(value, list)
                        and all(isinstance(item, dict) for item in value)
                    ),
                    None,
                )
        else:
            classifications = None
        if not isinstance(classifications, list):
            raise RuntimeError(
                "Question classifier returned empty or invalid structured output; "
                "no question types were inferred."
            )

        pending_set = set(pending)
        for item in classifications:
            if not isinstance(item, dict):
                continue
            index = item.get("index")
            label = str(item.get("question_type", "")).upper()
            if isinstance(index, int) and index in pending_set and label in allowed_types:
                result[index] = label

        missing = [index for index in pending if index not in result]
        if missing:
            raise RuntimeError(
                "Question classifier omitted valid classifications for indices "
                + ", ".join(map(str, missing))
            )
        return result
    finally:
        log_timing(
            "question_classification",
            started_at,
            job_id=job_id,
            question_count=len(rubrics),
        )
