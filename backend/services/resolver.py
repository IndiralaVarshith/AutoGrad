"""
AutoGrade — Majority Vote Resolver

Triggered when the Critic Agent returns REJECT.
Runs the Grading Agent 2 more times (same inputs, T=0.1)
and takes a majority vote across the 3 scores.
"""

from __future__ import annotations

from statistics import median

from models.schemas import CriterionResult, QuestionRubric
from services.grading_agent import grade_answer


# =========================================================
# RESOLVE DISAGREEMENT
# =========================================================

def resolve(
    question_text: str,
    extracted_text: str,
    image_parts: list,
    rubric: QuestionRubric,
    max_marks: float,
    original_criteria: list[CriterionResult],
    original_score: float,
) -> tuple[list[CriterionResult], float, bool]:
    """
    Run grading_agent 2 more times and take majority vote.

    Parameters
    ----------
    question_text : str
        The exam question text.
    extracted_text : str
        OCR-extracted student answer.
    image_parts : list
        Answer sheet image parts.
    rubric : QuestionRubric
        The approved rubric.
    max_marks : float
        Maximum marks.
    original_criteria : list[CriterionResult]
        Criteria from the original grading run.
    original_score : float
        Score from the original grading run.

    Returns
    -------
    final_criteria : list[CriterionResult]
        Criteria from the winning grade.
    final_score : float
        The resolved score.
    force_flag : bool
        True if all 3 scores differ by >0.5 (must be FLAGGED).
    """

    print("[Resolver] Running 2 additional grading passes...")

    # Run 2 more grading passes at T=0.1
    criteria_2, score_2 = grade_answer(
        question_text=question_text,
        extracted_text=extracted_text,
        image_parts=image_parts,
        rubric=rubric,
        max_marks=max_marks,
        temperature=0.1,
    )

    criteria_3, score_3 = grade_answer(
        question_text=question_text,
        extracted_text=extracted_text,
        image_parts=image_parts,
        rubric=rubric,
        max_marks=max_marks,
        temperature=0.1,
    )

    scores = [original_score, score_2, score_3]
    all_criteria = [original_criteria, criteria_2, criteria_3]

    print(f"[Resolver] 3 scores: {scores}")

    # -------------------------------------------------
    # Check if 2+ scores are within 0.5 of each other
    # -------------------------------------------------
    pairs_close = []
    for i in range(3):
        for j in range(i + 1, 3):
            if abs(scores[i] - scores[j]) <= 0.5:
                pairs_close.append((i, j))

    if pairs_close:
        # At least 2 scores agree — use median
        med = median(scores)
        # Pick the criteria set whose score is closest to median
        closest_idx = min(
            range(3),
            key=lambda i: abs(scores[i] - med)
        )

        print(f"[Resolver] Majority reached. Median={med}, using run {closest_idx + 1}")

        return all_criteria[closest_idx], scores[closest_idx], False

    # All 3 differ by >0.5 — force FLAGGED
    print("[Resolver] All 3 scores differ by >0.5. Forcing FLAGGED.")
    return original_criteria, original_score, True
