"""
AutoGrade — OCR Module

Uses Gemini 2.0 Flash with response_logprobs to extract
handwritten text per question and compute c_ocr from
token-level log-probabilities.
"""

from __future__ import annotations

import json
import math

from config import gemini_generate_with_retry


# =========================================================
# GEMINI OCR RESPONSE SCHEMA
# =========================================================

OCR_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question_number": {"type": "integer"},
                    "extracted_text": {"type": "string"},
                    "illegible_regions": {
                        "type": "array",
                        "items": {"type": "string"}
                    }
                },
                "required": [
                    "question_number",
                    "extracted_text",
                    "illegible_regions"
                ]
            }
        }
    },
    "required": ["answers"]
}


# =========================================================
# OCR PROMPT
# =========================================================

OCR_PROMPT = """
You are the OCR module of AutoGrade, an AI-powered exam grading system.

Extract all handwritten text from this answer sheet, separated by
question number.

For regions you cannot read with confidence, mark them as [illegible].

For each question the student has answered, return:
- question_number (integer)
- extracted_text (the full handwritten text, preserving the student's
  wording as closely as possible)
- illegible_regions (list of descriptions for any parts you could
  not confidently read)

Important:
- Do NOT grade the answers or assign marks.
- Do NOT summarise or rephrase the student's writing.
- Preserve mathematical expressions as accurately as possible.
- If a question number is ambiguous, use your best judgment.

Return the required JSON only.
"""


# =========================================================
# EXTRACT TEXT FROM IMAGES
# =========================================================

def extract_text(
    image_parts: list,
) -> tuple[dict[int, dict], float]:
    """
    Run OCR on answer sheet images via Gemini 2.0 Flash
    with response_logprobs.

    Parameters
    ----------
    image_parts : list
        List of image content parts (bytes, Part objects, or
        uploaded file references) representing all pages of
        one answer sheet.

    Returns
    -------
    results : dict[int, dict]
        Mapping of question_number → {extracted_text, illegible_regions}
    c_ocr : float
        OCR confidence in [0.0, 1.0] computed from logprobs.
    """

    contents = [OCR_PROMPT] + image_parts

    response = gemini_generate_with_retry(
        contents=contents,
        config={
            "response_mime_type": "application/json",
            "response_json_schema": OCR_RESPONSE_SCHEMA,
            "temperature": 0.1,
        }
    )

    if not response.text:
        raise RuntimeError("Gemini OCR returned empty output.")

    # -------------------------------------------------
    # Parse JSON response
    # -------------------------------------------------
    data = json.loads(response.text)
    answers = data.get("answers", [])

    results: dict[int, dict] = {}
    total_illegible = 0

    for item in answers:
        q_num = int(item.get("question_number", 0))
        regions = item.get("illegible_regions", [])
        total_illegible += len(regions)

        results[q_num] = {
            "extracted_text": item.get("extracted_text", ""),
            "illegible_regions": regions,
        }

    # -------------------------------------------------
    # Compute c_ocr from logprobs
    # -------------------------------------------------
    c_ocr = _compute_c_ocr(response, total_illegible, len(results))

    print(f"[OCR] Extracted {len(results)} answers, c_ocr={c_ocr:.4f}")

    return results, c_ocr


# =========================================================
# LOGPROB-BASED OCR CONFIDENCE
# =========================================================

def _compute_c_ocr(
    response,
    illegible_count: int,
    total_questions: int,
) -> float:
    """
    Compute OCR confidence:
        mean_logprob = average of all token logprobs
        oov_penalty  = count("[illegible]") / total_questions * 0.3
        c_ocr        = exp(mean_logprob) * (1 - oov_penalty)

    Clamped to [0.0, 1.0].
    """

    # Extract logprobs from response metadata
    logprobs_list: list[float] = []

    try:
        if hasattr(response, "candidates") and response.candidates:
            candidate = response.candidates[0]

            if hasattr(candidate, "logprobs_result") and candidate.logprobs_result:
                for token_info in candidate.logprobs_result.chosen_candidates:
                    if hasattr(token_info, "log_probability"):
                        logprobs_list.append(token_info.log_probability)
    except Exception:
        pass

    # If logprobs are available, compute mean; otherwise fall back
    if logprobs_list:
        mean_logprob = sum(logprobs_list) / len(logprobs_list)
    else:
        # Fallback: assume moderate confidence when logprobs
        # are not available (e.g. model doesn't support them)
        mean_logprob = -0.1

    # OOV penalty
    if total_questions > 0:
        oov_penalty = (illegible_count / total_questions) * 0.3
    else:
        oov_penalty = 0.0

    c_ocr = math.exp(mean_logprob) * (1 - oov_penalty)

    # Clamp to [0.0, 1.0]
    return max(0.0, min(1.0, c_ocr))


# =========================================================
# STUDENT METADATA EXTRACTION
# =========================================================

_META_SCHEMA = {
    "type": "object",
    "properties": {
        "hall_ticket": {"type": "string"},
        "student_name": {"type": "string"},
    },
    "required": ["hall_ticket", "student_name"],
}

_META_PROMPT = """
You are reading the cover/header section of a student's handwritten exam answer sheet.

Extract ONLY:
1. hall_ticket — the student's roll number, hall ticket number, register number, or exam ID
   (usually a number like "5069140" or alphanumeric like "21CS001")
2. student_name — the student's full name as written on the sheet

Rules:
- If you cannot find the hall ticket, return an empty string "".
- If you cannot find the name, return an empty string "".
- Do NOT guess or invent values. Only return what is clearly written.
- Return JSON only.
"""


def extract_student_meta(image_parts: list) -> dict:
    """
    Use Gemini vision to extract student name and hall ticket
    from the answer sheet cover page.

    Parameters
    ----------
    image_parts : list
        Image content parts (same format as extract_text).

    Returns
    -------
    dict with keys:
        hall_ticket : str  (empty string if not found)
        student_name : str (empty string if not found)
    """
    try:
        response = gemini_generate_with_retry(
            contents=[_META_PROMPT] + image_parts,
            config={
                "response_mime_type": "application/json",
                "response_json_schema": _META_SCHEMA,
                "temperature": 0.0,
            }
        )

        if not response.text:
            return {"hall_ticket": "", "student_name": ""}

        data = json.loads(response.text)
        hall_ticket = str(data.get("hall_ticket", "")).strip()
        student_name = str(data.get("student_name", "")).strip()

        print(f"[OCR] Student meta → hall_ticket={hall_ticket!r}, name={student_name!r}")
        return {"hall_ticket": hall_ticket, "student_name": student_name}

    except Exception as exc:
        print(f"[OCR] Student meta extraction failed: {exc}")
        return {"hall_ticket": "", "student_name": ""}
