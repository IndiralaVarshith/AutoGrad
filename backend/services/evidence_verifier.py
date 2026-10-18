"""
AutoGrade — Evidence Verifier

Uses rapidfuzz.fuzz.partial_ratio to verify that verbatim
quotes cited by the Grading Agent actually appear in the
OCR transcript. Strips marks from unverified quotes.

Threshold constant: PARTIAL_RATIO_THRESHOLD = 85
"""

from __future__ import annotations

from rapidfuzz.fuzz import partial_ratio

from models.schemas import CriterionResult


# =========================================================
# CONSTANT — do not change without updating this file header
# =========================================================

PARTIAL_RATIO_THRESHOLD: int = 85


# =========================================================
# VERIFY EVIDENCE
# =========================================================

def verify_evidence(
    criteria: list[CriterionResult],
    ocr_transcript: str,
) -> tuple[list[CriterionResult], float]:
    """
    Verify verbatim quotes against the OCR transcript.

    For each criterion where marks_awarded > 0:
      - rapidfuzz.fuzz.partial_ratio(verbatim_quote, ocr_transcript)
      - If score >= 85 → quote_verified = True
      - Else → quote_verified = False, marks_awarded = 0

    Parameters
    ----------
    criteria : list[CriterionResult]
        The grading criteria with verbatim quotes.
    ocr_transcript : str
        The full OCR-extracted text from the student answer.

    Returns
    -------
    updated_criteria : list[CriterionResult]
        Criteria with quote_verified set and unverified marks stripped.
    c_evid : float
        Evidence confidence = valid / total_k if total_k > 0 else 1.0
    """

    updated: list[CriterionResult] = []
    valid_count = 0
    total_k = 0

    for criterion in criteria:
        c = criterion.model_copy()

        if c.marks_awarded > 0:
            total_k += 1

            if c.verbatim_quote.strip():
                score = partial_ratio(
                    c.verbatim_quote,
                    ocr_transcript,
                )

                if score >= PARTIAL_RATIO_THRESHOLD:
                    c.quote_verified = True
                    valid_count += 1
                else:
                    c.quote_verified = False
                    c.marks_awarded = 0
                    c.rationale += (
                        f" [Evidence stripped: partial_ratio={score}, "
                        f"below threshold {PARTIAL_RATIO_THRESHOLD}]"
                    )
            else:
                # No quote provided but marks awarded — strip
                c.quote_verified = False
                c.marks_awarded = 0
                c.rationale += " [Evidence stripped: empty verbatim_quote]"

        updated.append(c)

    c_evid = valid_count / total_k if total_k > 0 else 1.0

    print(
        f"[Evidence] {valid_count}/{total_k} quotes verified, "
        f"c_evid={c_evid:.4f}"
    )

    return updated, c_evid
