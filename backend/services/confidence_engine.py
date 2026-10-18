"""
AutoGrade — Confidence Engine

4-signal weighted formula with routing thresholds.
All weights are constants at the top of this file.
DIAGRAM questions get c_final multiplied by 0.85 before routing.
"""

from __future__ import annotations


# =========================================================
# WEIGHT CONSTANTS — do not hardcode inline
# =========================================================

W_OCR: float = 0.20
W_AGREE: float = 0.35
W_EVID: float = 0.25
W_MATH: float = 0.20

# DIAGRAM post-formula adjustment
DIAGRAM_PENALTY: float = 0.85

# Routing thresholds
AUTO_ACCEPT_THRESHOLD: float = 0.85
FLAGGED_THRESHOLD: float = 0.65


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
) -> tuple[float, str]:
    """
    Compute final confidence and routing decision.

    Formula:
        if question_type == "DIAGRAM":
            base = W_OCR*c_ocr + W_AGREE*c_agree + W_EVID*c_evid + W_MATH*c_math
            c_final = base * DIAGRAM_PENALTY
        else:
            c_final = W_OCR*c_ocr + W_AGREE*c_agree + W_EVID*c_evid + W_MATH*c_math

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

    Returns
    -------
    c_final : float
        Final confidence score.
    routing : str
        AUTO_ACCEPT | FLAGGED | MANUAL
    """

    base = (
        W_OCR * c_ocr
        + W_AGREE * c_agree
        + W_EVID * c_evid
        + W_MATH * c_math
    )

    if question_type == "DIAGRAM":
        c_final = base * DIAGRAM_PENALTY
    else:
        c_final = base

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
        f"(OCR={c_ocr:.2f}, AGREE={c_agree:.2f}, "
        f"EVID={c_evid:.2f}, MATH={c_math:.2f})"
    )

    return c_final, routing
