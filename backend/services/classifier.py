"""
AutoGrade — Question Type Classifier

Uses a single Gemini call to classify exam questions as
THEORY, NUMERICAL, or DIAGRAM.
"""

from __future__ import annotations

from config import gemini_generate_with_retry


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
        return "THEORY"

    label = response.text.strip().upper()

    # Normalise to one of the three valid labels
    if "NUMERICAL" in label:
        return "NUMERICAL"
    elif "DIAGRAM" in label:
        return "DIAGRAM"
    else:
        return "THEORY"
