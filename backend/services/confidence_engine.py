"""
AutoGrade — Confidence Engine

4-signal weighted formula with routing thresholds.
All weights are constants at the top of this file.
DIAGRAM questions get c_final multiplied by 0.85 before routing.
"""

from __future__ import annotations

from math import isfinite
from operator import index

# =========================================================
# WEIGHT CONSTANTS — do not hardcode inline
# =========================================================

W_OCR: float = 0.20
W_AGREE: float = 0.35
W_EVID: float = 0.25
W_MATH: float = 0.20

# DIAGRAM post-formula adjustment
DIAGRAM_PENALTY: float = 0.85

# Keep illegibility weighting consistent with the OCR confidence formula.
ILLEGIBLE_OCR_PENALTY_RATE: float = 0.30

# Routing thresholds
AUTO_ACCEPT_THRESHOLD: float = 0.85
FLAGGED_THRESHOLD: float = 0.65


# Largest grader/critic gap on one answer that can still be auto-accepted.
AUTO_ACCEPT_MAX_SCORE_GAP: float = 0.5


def route_answer(
    *,
    grader_score: float,
    critic_score: float,
    critic_verdict: str,
    c_evid: float,
    force_flag: bool = False,
    illegible_count: int = 0,
    math_unverified: bool = False,
) -> tuple[str, str]:
    """Decide whether a graded answer stands or goes to the teacher.

    Returns (routing, flag_reason). OCR confidence is not used because the
    reader gives no calibrated value; the signals below are all observed.
    """
    if illegible_count > 0:
        return "MANUAL", "UNCLEAR_READING"
    if math_unverified:
        return "MANUAL", "MATH_NOT_VERIFIED"
    if force_flag or critic_verdict != "ACCEPT":
        return "FLAGGED", "CRITIC_DISAGREED"
    if abs(grader_score - critic_score) > AUTO_ACCEPT_MAX_SCORE_GAP:
        return "FLAGGED", "SCORE_GAP"
    # On the tuning sheets a model's 0 on a written answer was usually wrong, so the
    # teacher decides. Blank answers never reach this point.
    if grader_score <= 0:
        return "FLAGGED", "ZERO_ON_WRITTEN_ANSWER"
    if c_evid < 1.0:
        return "FLAGGED", "EVIDENCE_NOT_VERIFIED"
    return "AUTO_ACCEPT", ""


def _nonnegative_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default

    try:
        return max(0, index(value))
    except TypeError:
        return default


# =========================================================
# COMPUTE AGREEMENT SCORE
# =========================================================

def compute_c_agree(
    grader_score: float,
    critic_score: float,
    max_marks: float,
) -> float:
    """
    c_agree = 1.0 - abs(grader_score - critic_score) / max_marks
    Clamped to [0.0, 1.0].
    """
    if max_marks <= 0:
        return 1.0

    c_agree = 1.0 - abs(grader_score - critic_score) / max_marks
    return max(0.0, min(1.0, c_agree))


# =========================================================
# COMPUTE FINAL CONFIDENCE + ROUTING
# =========================================================

def compute_confidence(
    c_ocr: float,
    c_agree: float,
    c_evid: float,
    c_math: float,
    question_type: str,
    total_illegible: int | None = 0,
    total_questions: int | None = 1,
) -> tuple[float, str]:
    """
    Compute final confidence and routing decision.

    Formula:
        effective_c_ocr = c_ocr * max(
            0, 1 - 0.30 * total_illegible / total_questions
        )
        base = W_OCR*effective_c_ocr + W_AGREE*c_agree + W_EVID*c_evid + W_MATH*c_math
        if question_type == "DIAGRAM":
            c_final = base * DIAGRAM_PENALTY
        else:
            c_final = base

    Routing:
        c_final >= 0.85  → AUTO_ACCEPT
        0.65 <= c_final < 0.85 → FLAGGED
        c_final < 0.65   → MANUAL

    Parameters
    ----------
    c_ocr : float
        OCR confidence [0, 1].
    c_agree : float
        Grader-critic agreement [0, 1].
    c_evid : float
        Evidence verification confidence [0, 1].
    c_math : float
        Math verification confidence [0, 1].
    question_type : str
        One of THEORY, NUMERICAL, DIAGRAM.
    total_illegible : int | None, optional
        Number of illegible regions in this question.
    total_questions : int | None, optional
        Number of OCR answers used to normalize the illegibility penalty.
        Defaults to one question.

    Returns
    -------
    c_final : float
        Final confidence score.
    routing : str
        AUTO_ACCEPT | FLAGGED | MANUAL
    """

    illegible_count = _nonnegative_int(total_illegible, 0)
    question_count = _nonnegative_int(total_questions, 1) or 1
    illegible_penalty = min(
        ILLEGIBLE_OCR_PENALTY_RATE * illegible_count / question_count,
        1.0,
    )
    effective_c_ocr = c_ocr * (1.0 - illegible_penalty)

    base = (
        W_OCR * effective_c_ocr
        + W_AGREE * c_agree
        + W_EVID * c_evid
        + W_MATH * c_math
    )

    if question_type == "DIAGRAM":
        c_final = base * DIAGRAM_PENALTY
    else:
        c_final = base

    if not isfinite(c_final):
        c_final = 0.0
    else:
        c_final = max(0.0, min(1.0, c_final))

    # Routing decision
    if c_final >= AUTO_ACCEPT_THRESHOLD:
        routing = "AUTO_ACCEPT"
    elif c_final >= FLAGGED_THRESHOLD:
        routing = "FLAGGED"
    else:
        routing = "MANUAL"

    print(
        f"[Confidence] c_final={c_final:.4f}, "
        f"routing={routing} "
        f"(OCR={effective_c_ocr:.2f}[illegible={illegible_count}], AGREE={c_agree:.2f}, "
        f"EVID={c_evid:.2f}, MATH={c_math:.2f})"
    )

    return c_final, routing
