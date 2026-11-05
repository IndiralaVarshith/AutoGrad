"""
AutoGrade — Pipeline Orchestrator

Orchestrates the full per-paper grading flow in exact order:
1. OCR → extracted_text per question, c_ocr
2. Classifier → question_type per question
3. Load approved rubric from Firestore
4. For each question:
   a. If NUMERICAL → math_agent (c_math) else c_math=1.0
   b. grading_agent → criteria, grader_score
   c. critic_agent → verdict, critic_score
   d. If REJECT → resolver → final_score
   e. c_agree = 1.0 - abs(grader-critic)/max_marks
   f. evidence_verifier → updated criteria, c_evid
   g. confidence_engine → c_final, routing
5. Build PaperResult, write to Firestore
6. Update queue status
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import math
import os
import re
import time
from collections import defaultdict
from typing import Callable

from models.schemas import (
    PaperResult,
    QuestionGrade,
    CriterionResult,
    QuestionRubric,
    RubricCriterion,
)
from services.ocr import extract_page_regions, extract_text
from services.classifier import classify_questions
from services.grading_agent import grade_answer
from services.critic_agent import critique_grade
from services.resolver import resolve, compare_independent_verification as _compare_independent_verification
from services.evidence_verifier import verify_evidence
from services.confidence_engine import route_answer, compute_c_agree, compute_confidence
from services.math_agent import expected_expression_from_rubric, verify_math
from services.db import db_service
from config import get_firestore
from services.timing import log_timing, timed_stage
from services.router import local_only, model_router


_logger = logging.getLogger(__name__)

INDEPENDENT_VERIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "transcription": {"type": "string"},
        "addresses_question": {"type": "boolean"},
        "evidence_supported": {"type": "boolean"},
        "missed_relevant_content": {"type": "boolean"},
        "suggested_score": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": [
        "transcription",
        "addresses_question",
        "evidence_supported",
        "missed_relevant_content",
        "suggested_score",
        "reason",
    ],
}


def _submission_source_hash(
    exam_id: str,
    hall_ticket: str,
    image_bytes_list: list[bytes] | None,
) -> str:
    """Scope OCR cache identity to student, exam, and every page in order."""
    if not image_bytes_list:
        return ""
    file_digest = hashlib.sha256()
    for page_bytes in image_bytes_list:
        page_bytes = bytes(page_bytes)
        file_digest.update(len(page_bytes).to_bytes(8, "big"))
        file_digest.update(page_bytes)
    cache_scope = f"{exam_id}:{hall_ticket}:{file_digest.hexdigest()}"
    return hashlib.sha256(cache_scope.encode("utf-8")).hexdigest()


def _gemini_verification_reason(
    *,
    critic_verdict: str,
    force_flag: bool,
    illegible_regions: list[str],
    math_requires_manual_review: bool,
    c_evid: float,
    exam_id: str,
    hall_ticket: str,
    question_number: int,
    sub_part: str,
) -> str:
    """Select only explicit risk cases plus a stable, bounded sample."""
    if critic_verdict == "REJECT":
        return "local_critic_disagreement"
    if force_flag:
        return "resolver_unresolved_disagreement"
    if illegible_regions:
        return "illegible_or_uncertain_content"
    if math_requires_manual_review:
        return "mathematics_not_verified"
    if c_evid < 1.0:
        return "evidence_verification_incomplete"

    try:
        sample_rate = min(1.0, max(0.0, float(os.getenv("GEMINI_VERIFICATION_SAMPLE_RATE", "0"))))
    except (TypeError, ValueError):
        sample_rate = 0.0
    if sample_rate <= 0:
        return ""
    identity = f"{exam_id}:{hall_ticket}:{question_number}:{sub_part.strip().upper()}"
    bucket = int(hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12], 16) / float(0xFFFFFFFFFFFF)
    return "routine_quality_sample" if bucket < sample_rate else ""


SMALLEST_HEADER_SHARE = 0.12
LOWEST_HEADER_LINE_SHARE = 0.4
_OPTION_RE = re.compile(r"^\(?([A-Za-z])\)?[.)]?$")


def _selected_option(text: str, labels: set[str]) -> str:
    """The single printed option a student chose, or "" if it is not clear."""
    match = _OPTION_RE.match(text.strip())
    letter = match.group(1).upper() if match else ""
    return letter if letter in labels else ""


def _header_end(page_image) -> float:
    """Where the header ends, as a share of page height: the top ruled line, if any."""
    import numpy as np

    gray = np.asarray(page_image.convert("L"))
    limit = int(gray.shape[0] * LOWEST_HEADER_LINE_SHARE)
    rows = np.flatnonzero((gray[:limit] < 110).mean(axis=1) > 0.5)
    line = (int(rows[-1]) + 1) / gray.shape[0] if rows.size else 0.0
    return max(SMALLEST_HEADER_SHARE, line)


def _crop_evidence_regions(ocr_pages: list, page_image_parts: list, region_ids: list[str]) -> list[bytes]:
    """Render only cited OCR evidence boxes for a selective Gemini check."""
    if not region_ids:
        return []
    from PIL import Image

    from services.ocr import _page_image_bytes

    wanted = set(region_ids)
    output: list[bytes] = []
    for page in ocr_pages:
        page_number = int(getattr(page, "page_number", 0) or 0)
        if not 1 <= page_number <= len(page_image_parts):
            continue
        matching = [
            region for region in getattr(page, "regions", [])
            if getattr(region, "region_id", "") in wanted
        ]
        if not matching:
            continue
        raw = _page_image_bytes(page_image_parts[page_number - 1])
        if not raw:
            continue
        try:
            with Image.open(io.BytesIO(raw)) as source:
                page_image = source.convert("RGB")
            width, height = page_image.size
            for region in matching:
                bbox = list(getattr(region, "bbox", []) or [])
                if len(bbox) != 4:
                    continue
                if float(bbox[1]) < _header_end(page_image):
                    continue  # the student's name and ID can head any scanned page
                x0, y0, x1, y1 = [max(0.0, min(1.0, float(v))) for v in bbox]
                left, right = sorted((int(x0 * width), int(x1 * width)))
                top, bottom = sorted((int(y0 * height), int(y1 * height)))
                box_w, box_h = max(1, right - left), max(1, bottom - top)
                pad_x, pad_y = max(8, int(box_w * 0.08)), max(12, int(box_h * 0.65))
                crop = page_image.crop((
                    max(0, left - pad_x), max(0, top - pad_y),
                    min(width, right + pad_x), min(height, bottom + pad_y),
                ))
                buffer = io.BytesIO()
                crop.save(buffer, format="JPEG", quality=92)
                output.append(buffer.getvalue())
        except Exception:
            _logger.warning("Could not prepare a mapped OCR region image for independent verification")
    return output[:4]


def _independent_verification_prompt(
    *,
    rubric: QuestionRubric,
    extracted_text: str,
    criteria: list[CriterionResult],
    local_score: float,
) -> str:
    rubric_data = [item.model_dump() for item in rubric.criteria]
    evidence = [
        {
            "criterion": item.criterion,
            "quote": item.verbatim_quote,
            "marks": item.marks_awarded,
            "rationale": item.rationale,
        }
        for item in criteria
    ]
    return (
        "Independently inspect the attached crop(s) of the student's original answer. "
        "First transcribe only what is visually supported; use [illegible] for unreadable parts. "
        "Then assess the answer against the approved question and rubric below. The local OCR, "
        "local evidence quotes, and provisional score are shown only for comparison after your "
        "independent reading. Do not copy them if the images disagree. Do not invent missing text. "
        "Do not modify the approved rubric. Return the requested JSON.\n\n"
        f"Question: {rubric.question_text}\n"
        f"Approved rubric: {json.dumps(rubric_data, ensure_ascii=False)}\n"
        f"Maximum marks: {rubric.max_marks}\n\n"
        f"Local OCR transcript (comparison only): {extracted_text}\n"
        f"Local evidence and provisional grading (comparison only): {json.dumps(evidence, ensure_ascii=False)}\n"
        f"Local evidence-verified score (comparison only): {local_score}\n"
    )


@local_only()
def regrade_question_text(
    rubric: QuestionRubric,
    answer_text: str,
    *,
    question_type: str | None = None,
    image_parts: list | None = None,
    ocr_regions: list | None = None,
    illegible_count: int = 0,
) -> dict:
    """Run the existing grading and evidence chain on a teacher-validated transcript.

    OCR is intentionally not rerun. Transcript confidence remains uncalibrated,
    so the result always needs a teacher's score confirmation even when the
    grading agents agree.
    """
    if not answer_text.strip():
        raise ValueError("Teacher-validated answer text is empty.")

    q_type = (question_type or rubric.question_type or "THEORY").strip().upper()
    evidence_images = image_parts or []
    c_math = 1.0
    math_requires_manual_review = False
    routing_notes: list[str] = [
        "Teacher-validated transcript was regraded; OCR confidence remains uncalibrated."
    ]

    if q_type == "NUMERICAL":
        expected_expression = expected_expression_from_rubric(rubric.criteria)
        if expected_expression:
            with timed_stage(
                "ocr_regrade_sympy_verification",
                detail=f"Verifying teacher-validated math for Q{rubric.question_number}{rubric.sub_part}",
                question_number=rubric.question_number,
            ):
                math_result = verify_math(answer_text, expected_expression)
            c_math = math_result.c_math
            math_requires_manual_review = math_result.symbolic_correct is not True
        else:
            c_math = 0.0
            math_requires_manual_review = True
        if math_requires_manual_review:
            routing_notes.append(
                "Numerical verification requires teacher review because the approved rubric "
                "has no explicit answer expression or SymPy could not confirm the response."
            )

    with timed_stage(
        "ocr_regrade_grading",
        detail=f"Regrading teacher-validated Q{rubric.question_number}{rubric.sub_part}",
        question_number=rubric.question_number,
    ):
        grader_criteria, grader_score, model_used, routing_reason = grade_answer(
            question_text=rubric.question_text,
            extracted_text=answer_text,
            image_parts=evidence_images,
            rubric=rubric,
            max_marks=rubric.max_marks,
            question_type=q_type,
            illegible_count=illegible_count,
        )
    with timed_stage(
        "ocr_regrade_critic",
        detail=f"Reviewing regrade for Q{rubric.question_number}{rubric.sub_part}",
        question_number=rubric.question_number,
    ):
        critic_verdict = critique_grade(
            question_text=rubric.question_text,
            extracted_text=answer_text,
            image_parts=evidence_images,
            rubric=rubric,
            grader_criteria=grader_criteria,
            grader_score=grader_score,
            max_marks=rubric.max_marks,
            avoid_model=model_used,
        )
    critic_score = float(critic_verdict.suggested_score)
    if not math.isfinite(critic_score) or not 0 <= critic_score <= rubric.max_marks:
        raise ValueError("Critic score is outside the approved question mark range.")

    final_criteria = grader_criteria
    force_flag = False
    if critic_verdict.verdict == "REJECT":
        with timed_stage(
            "ocr_regrade_resolver",
            detail=f"Resolving disagreement for Q{rubric.question_number}{rubric.sub_part}",
            question_number=rubric.question_number,
        ):
            final_criteria, _, force_flag = resolve(
                question_text=rubric.question_text,
                extracted_text=answer_text,
                image_parts=evidence_images,
                rubric=rubric,
                max_marks=rubric.max_marks,
                original_criteria=grader_criteria,
                original_score=grader_score,
                avoid_model=critic_verdict.model_used,
                avoid_models=[model_used, critic_verdict.model_used],
                question_type=q_type,
                illegible_count=illegible_count,
            )
        if force_flag:
            routing_notes.append("The existing Resolver could not reconcile grader disagreement.")

    c_agree = compute_c_agree(grader_score, critic_score, rubric.max_marks)
    with timed_stage(
        "ocr_regrade_evidence_verification",
        detail=f"Verifying answer evidence for Q{rubric.question_number}{rubric.sub_part}",
        question_number=rubric.question_number,
    ):
        final_criteria, c_evid = verify_evidence(
            final_criteria,
            answer_text,
            ocr_regions=ocr_regions,
        )
    final_score = sum(item.marks_awarded for item in final_criteria)
    if (
        not math.isfinite(final_score)
        or final_score < 0
        or final_score > rubric.max_marks + 1e-6
    ):
        raise ValueError("Evidence-verified score is outside the approved question mark range.")

    with timed_stage(
        "ocr_regrade_confidence",
        detail=f"Calculating provisional confidence for Q{rubric.question_number}{rubric.sub_part}",
        question_number=rubric.question_number,
    ):
        c_final, _ = compute_confidence(
            c_ocr=0.0,
            c_agree=c_agree,
            c_evid=c_evid,
            c_math=c_math,
            question_type=q_type,
            total_illegible=illegible_count,
            total_questions=1,
        )
    if routing_reason:
        routing_notes.insert(0, routing_reason)

    return {
        "criteria": final_criteria,
        "grader_score": grader_score,
        "critic_score": critic_score,
        "final_score": final_score,
        "c_ocr": 0.0,
        "c_agree": c_agree,
        "c_evid": c_evid,
        "c_math": c_math,
        "c_final": c_final,
        "routing": "MANUAL",
        "routing_reason": " ".join(routing_notes),
        "model_used": model_used,
    }


# =========================================================
# MAIN PIPELINE
# =========================================================

@local_only()
def process_paper(
    exam_id: str,
    hall_ticket: str,
    student_name: str,
    image_parts: list,
    rubric_list: list[QuestionRubric],
    image_bytes_list: list[bytes] | None = None,
    *,
    ocr_image_parts: list | None = None,
    source_file_reference: str = "",
    approved_rubric_version: str = "",
    approved_total_marks: float | None = None,
    job_id: str = "",
    on_stage: Callable[[str, str], None] | None = None,
) -> PaperResult:
    """
    Full grading pipeline for one student's answer sheet.

    Parameters
    ----------
    exam_id : str
        The exam identifier.
    hall_ticket : str
        Student hall ticket / roll number.
    student_name : str
        Student name (optional).
    image_parts : list
        Image content parts for Gemini (uploaded file refs or bytes).
    rubric_list : list[QuestionRubric]
        Approved rubrics for all questions.
    image_bytes_list : list[bytes] | None
        Original uploaded PDF/image bytes, used for cache identity.
    ocr_image_parts : list | None
        Page-wise image payloads for local OCR and region evidence. Defaults
        to image_parts for the local provider path.

    Returns
    -------
    PaperResult
        Complete grading result for this paper.
    """

    print(f"\n{'='*60}")
    print(f"[Pipeline] Processing: {hall_ticket}")
    print(f"{'='*60}\n")

    # -------------------------------------------------
    # Step 1: OCR
    # -------------------------------------------------
    if not rubric_list:
        raise ValueError("Approved rubric has no questions.")
    question_ids = [(r.question_number, r.sub_part.strip().upper()) for r in rubric_list]
    if len(question_ids) != len(set(question_ids)):
        raise ValueError("Approved rubric contains duplicate question/sub-part identifiers.")

    source_hash = _submission_source_hash(exam_id, hall_ticket, image_bytes_list)
    page_image_parts = ocr_image_parts if ocr_image_parts is not None else image_parts
    print("[Pipeline] Step 1: Page-wise OCR region extraction...")
    with timed_stage(
        "page_region_ocr",
        job_id=job_id,
        on_stage=on_stage,
        detail="Inspecting pages and locating OCR text regions",
        page_count=len(page_image_parts),
    ):
        ocr_pages = extract_page_regions(
            page_image_parts,
            source_hash=source_hash,
            job_id=job_id,
            on_stage=on_stage,
        )

    print("[Pipeline] Step 1: OCR extraction...")
    page_readers: list[dict] = []
    with timed_stage(
        "handwriting_ocr",
        job_id=job_id,
        on_stage=on_stage,
        detail="Reading handwritten answers",
        page_count=len(image_parts),
    ):
        ocr_results, c_ocr = extract_text(
            image_parts,
            source_hash=source_hash,
            job_id=job_id,
            on_stage=on_stage,
            page_regions=ocr_pages,
            page_readers=page_readers,
        )

    unresolved_candidates: dict[int, list[dict]] = defaultdict(list)
    for (detected_q, _), answer in ocr_results.items():
        candidate_q = answer.get("candidate_question_number")
        if candidate_q and int(candidate_q) > 0:
            unresolved_candidates[int(candidate_q)].append(answer)

    expected_answer_ids = {(r.question_number, r.sub_part.strip().upper()) for r in rubric_list}
    mapped_answer_ids = {
        key for key, answer in ocr_results.items()
        if key[0] > 0 and answer.get("mapping_status", "MAPPED") == "MAPPED"
    }
    missing_expected = sorted(expected_answer_ids - mapped_answer_ids)
    if missing_expected:
        print(
            "[Pipeline] OCR coverage: "
            f"{len(mapped_answer_ids & expected_answer_ids)}/{len(expected_answer_ids)} rubric answer IDs mapped; "
            f"{len(missing_expected)} require review or are absent."
        )

    # -------------------------------------------------
    # Step 2: Classification (for questions not already typed)
    # -------------------------------------------------
    print("[Pipeline] Step 2: Question classification...")
    with timed_stage(
        "question_classification",
        job_id=job_id,
        on_stage=on_stage,
        detail=f"Identifying question-wise responses for {len(rubric_list)} questions",
        question_count=len(rubric_list),
    ):
        question_types = classify_questions(
            rubric_list,
            job_id=job_id,
            on_stage=on_stage,
        )

    # -------------------------------------------------
    # Steps 3-4: Per-question grading
    # -------------------------------------------------
    print("[Pipeline] Steps 3-4: Per-question grading...")
    question_grades: list[QuestionGrade] = []
    rubrics_by_choice_group: dict[str, list[QuestionRubric]] = defaultdict(list)
    rubrics_by_number: dict[int, list[QuestionRubric]] = defaultdict(list)
    for rubric in rubric_list:
        rubrics_by_number[rubric.question_number].append(rubric)
        group = rubric.choice_group or (
            f"Q{rubric.question_number}" if rubric.is_or_choice else ""
        )
        if group:
            rubrics_by_choice_group[group].append(rubric)

    used_answer_keys: set[tuple[int, str]] = set()

    for rubric_index, rubric in enumerate(rubric_list):
        q_num = rubric.question_number
        sub_part = rubric.sub_part.strip().upper()
        q_type = question_types[rubric_index]
        max_marks = rubric.max_marks

        rubric_group = rubric.choice_group or (
            f"Q{q_num}" if rubric.is_or_choice else ""
        )
        choice_group = rubrics_by_choice_group.get(rubric_group, [])
        choice_keys = {
            (item.question_number, item.sub_part.strip().upper())
            for item in choice_group
        }
        attempted_keys = [
            key for key in ocr_results
            if key[0] in {item.question_number for item in choice_group}
            and ocr_results[key].get("extracted_text", "").strip()
        ] if len(choice_group) > 1 else []

        answer_key: tuple[int, str] | None = None
        answer_is_ambiguous = False
        not_attempted = False
        if len(choice_group) > 1:
            if len(attempted_keys) > 1:
                answer_is_ambiguous = True
            elif len(attempted_keys) == 1:
                candidate = attempted_keys[0]
                if candidate[1] and candidate in choice_keys:
                    if candidate != (q_num, sub_part):
                        not_attempted = True
                    else:
                        answer_key = candidate
                else:
                    answer_is_ambiguous = True
            else:
                answer_is_ambiguous = True
        elif sub_part:
            answer_key = (q_num, sub_part) if (q_num, sub_part) in ocr_results else None
            if answer_key is None and (q_num, "") in ocr_results:
                answer_is_ambiguous = True
        elif (q_num, "") in ocr_results:
            answer_key = (q_num, "")
        else:
            matching_keys = [key for key in ocr_results if key[0] == q_num]
            if len(matching_keys) == 1:
                answer_key = matching_keys[0]

        if answer_key in used_answer_keys:
            answer_is_ambiguous = True
            answer_key = None
        if answer_key is not None:
            used_answer_keys.add(answer_key)
            ocr_data = ocr_results[answer_key]
        else:
            ocr_data = {}

        if answer_key is not None and ocr_data.get("mapping_status", "MAPPED") != "MAPPED":
            answer_is_ambiguous = True
        if unresolved_candidates.get(q_num):
            answer_is_ambiguous = True
            ocr_data = dict(ocr_data)
            ocr_data["page_references"] = list(ocr_data.get("page_references", [])) + [
                {
                    **reference,
                    "mapping_status": candidate.get("mapping_status", "UNRESOLVED"),
                    "candidate_question_number": q_num,
                    "reference_role": "unresolved_possible_continuation",
                }
                for candidate in unresolved_candidates[q_num]
                for reference in candidate.get("page_references", [])
            ]

        extracted_text = str(ocr_data.get("extracted_text", ""))
        illegible_regions = list(ocr_data.get("illegible_regions", []))
        source_page_references = list(ocr_data.get("page_references", []))
        if unresolved_candidates.get(q_num):
            illegible_regions.append(
                "An unnumbered OCR fragment may continue this question; its text was kept separate and was not graded."
            )
        if answer_is_ambiguous:
            illegible_regions.append(
                "Answer could not be mapped to exactly one approved question/choice; teacher review required."
            )
        elif not extracted_text.strip() and not not_attempted:
            illegible_regions.append(
                "No answer text was confidently mapped to this approved question."
            )

        print(f"\n  [Q{q_num}] Type={q_type}, Max={max_marks}")

        if not_attempted:
            question_grades.append(
                QuestionGrade(
                    question_number=q_num,
                    sub_part=sub_part,
                    choice_group=rubric_group,
                    question_type=q_type,
                    max_marks=max_marks,
                    extracted_text="",
                    grading_input_text="",
                    source_page_references=[],
                    c_ocr=c_ocr,
                    c_final=0.0,
                    routing="NOT_ATTEMPTED",
                    model_used="",
                    routing_reason="A different approved alternative in this choice group was attempted.",
                )
            )
            continue

        if not extracted_text.strip() or answer_is_ambiguous:
            with timed_stage(
                "evidence_verification",
                job_id=job_id,
                on_stage=on_stage,
                detail=f"Checking available evidence for Q{q_num}{sub_part}",
                question_number=q_num,
            ):
                criteria, c_evid = verify_evidence([], extracted_text)
            question_grades.append(
                QuestionGrade(
                    question_number=q_num,
                    sub_part=sub_part,
                    choice_group=rubric_group,
                    question_type=q_type,
                    max_marks=max_marks,
                    extracted_text=extracted_text,
                    grading_input_text=extracted_text,
                    source_page_references=source_page_references,
                    criteria=criteria,
                    c_ocr=c_ocr,
                    c_evid=c_evid,
                    c_math=0.0 if q_type == "NUMERICAL" else 1.0,
                    c_final=0.0,
                    routing="MANUAL",
                    local_routing="MANUAL",
                    flag_reason=(
                        "UNMATCHED" if answer_is_ambiguous
                        else "BLANK_ANSWER" if answer_key is not None
                        else "NO_ANSWER_FOUND"
                    ),
                    illegible_regions=illegible_regions,
                    routing_reason="No confidently mapped answer was available; no marks were inferred.",
                )
            )
            continue

        if rubric.question_format == "MCQ":
            labels = {str(item.get("label", "")).upper() for item in rubric.options}
            key = rubric.correct_option.strip().upper()
            selected = _selected_option(extracted_text, labels)
            clear = bool(selected) and key in labels and not illegible_regions
            question_grades.append(
                QuestionGrade(
                    question_number=q_num,
                    sub_part=sub_part,
                    question_type=q_type,
                    max_marks=max_marks,
                    extracted_text=extracted_text,
                    grading_input_text=extracted_text,
                    source_page_references=source_page_references,
                    selected_option=selected,
                    final_score=max_marks if clear and selected == key else 0.0,
                    c_ocr=c_ocr,
                    c_final=1.0 if clear else 0.0,
                    routing="AUTO_ACCEPT" if clear else "MANUAL",
                    local_routing="AUTO_ACCEPT" if clear else "MANUAL",
                    flag_reason="" if clear else "UNCLEAR_OPTION",
                    illegible_regions=illegible_regions,
                    routing_reason=(
                        f"Option {selected} compared with the approved key."
                        if clear else "The chosen option or the key is not clear; no marks were inferred."
                    ),
                )
            )
            continue

        math_requires_manual_review = False

        # -------------------------------------------------
        # Step 4a: Math agent for NUMERICAL questions
        # -------------------------------------------------
        if q_type == "NUMERICAL":
            expected_expression = expected_expression_from_rubric(rubric.criteria)
            if expected_expression:
                with timed_stage(
                    "sympy_numerical_verification",
                    job_id=job_id,
                    on_stage=on_stage,
                    detail=f"Verifying the explicit approved answer for Q{q_num}{sub_part}",
                    question_number=q_num,
                ):
                    math_result = verify_math(extracted_text, expected_expression)
                c_math = math_result.c_math
                math_requires_manual_review = math_result.symbolic_correct is not True
            else:
                c_math = 0.0
                math_requires_manual_review = True
                log_timing(
                    "sympy_numerical_verification",
                    time.perf_counter(),
                    job_id=job_id,
                    question_number=q_num,
                    skipped=True,
                    reason="Approved rubric does not contain an explicit expected expression",
                )
        else:
            c_math = 1.0  # Neutral for non-numerical

        # -------------------------------------------------
        # Step 4b: Grading Agent
        # -------------------------------------------------
        try:
            with timed_stage(
                "rubric_matching_and_grading",
                job_id=job_id,
                on_stage=on_stage,
                detail=f"Evaluating Q{q_num}{sub_part} against its approved rubric",
                question_number=q_num,
            ):
                grader_criteria, grader_score, model_used, routing_reason = grade_answer(
                    question_text=rubric.question_text,
                    extracted_text=extracted_text,
                    image_parts=image_parts if q_type in {"DIAGRAM", "MIXED"} else [],
                    rubric=rubric,
                    max_marks=max_marks,
                    question_type=q_type,
                    illegible_count=len(illegible_regions),
                )

            # -------------------------------------------------
            # Step 4c: Critic Agent
            # -------------------------------------------------
            with timed_stage(
                "critic_agent",
                job_id=job_id,
                on_stage=on_stage,
                detail=f"Independently reviewing Q{q_num}{sub_part}",
                question_number=q_num,
            ):
                critic_verdict = critique_grade(
                    question_text=rubric.question_text,
                    extracted_text=extracted_text,
                    image_parts=image_parts if q_type in {"DIAGRAM", "MIXED", "NUMERICAL"} else [],
                    rubric=rubric,
                    grader_criteria=grader_criteria,
                    grader_score=grader_score,
                    max_marks=max_marks,
                    avoid_model=model_used,
                )
            critic_score = float(critic_verdict.suggested_score)
            if not math.isfinite(critic_score) or not 0 <= critic_score <= max_marks:
                raise ValueError(
                    f"Critic score for Q{q_num}{sub_part} is outside its approved "
                    f"range [0, {max_marks}]."
                )

            # -------------------------------------------------
            # Step 4d: Resolver (if REJECT)
            # -------------------------------------------------
            final_criteria = grader_criteria
            final_score = grader_score
            force_flag = False

            if critic_verdict.verdict == "REJECT":
                with timed_stage(
                    "critic_resolver",
                    job_id=job_id,
                    on_stage=on_stage,
                    detail=f"Resolving disagreement for Q{q_num}{sub_part} (two bounded re-grades)",
                    question_number=q_num,
                ):
                    final_criteria, final_score, force_flag = resolve(
                        question_text=rubric.question_text,
                        extracted_text=extracted_text,
                        image_parts=image_parts if q_type in {"DIAGRAM", "MIXED", "NUMERICAL"} else [],
                        rubric=rubric,
                        max_marks=max_marks,
                        original_criteria=grader_criteria,
                        original_score=grader_score,
                        avoid_model=critic_verdict.model_used,
                        avoid_models=[model_used, critic_verdict.model_used],
                        question_type=q_type,
                        illegible_count=len(illegible_regions),
                    )
        except Exception as error:  # noqa: BLE001 - one bad answer must not lose the sheet
            _logger.warning(
                "answer_grading_failed question=%s%s error=%s", q_num, sub_part, type(error).__name__
            )
            question_grades.append(
                _grading_failed_grade(
                    rubric, q_type, rubric_group, extracted_text, source_page_references,
                    c_ocr, illegible_regions, type(error).__name__,
                )
            )
            continue

        # -------------------------------------------------
        # Step 4e: c_agree
        # -------------------------------------------------
        c_agree = compute_c_agree(grader_score, critic_score, max_marks)

        # -------------------------------------------------
        # Step 4f: Evidence Verifier
        # -------------------------------------------------
        with timed_stage(
            "evidence_verification",
            job_id=job_id,
            on_stage=on_stage,
            detail=f"Verifying quoted evidence for Q{q_num}{sub_part}",
            question_number=q_num,
        ):
            final_criteria, c_evid = verify_evidence(
                criteria=final_criteria,
                ocr_transcript=extracted_text,
                ocr_regions=[
                    region
                    for page in ocr_pages
                    for region in page.regions
                ],
            )

        # Recalculate final_score after evidence stripping
        final_score = sum(c.marks_awarded for c in final_criteria)
        if final_score > max_marks + 1e-6:
            question_grades.append(
                _grading_failed_grade(
                    rubric, q_type, rubric_group, extracted_text, source_page_references,
                    c_ocr, illegible_regions, "AWARD_OVER_MAXIMUM",
                )
            )
            continue

        # -------------------------------------------------
        # Step 4g: Confidence Engine
        # -------------------------------------------------
        with timed_stage(
            "confidence_calculation",
            job_id=job_id,
            on_stage=on_stage,
            detail=f"Calculating confidence for Q{q_num}{sub_part}",
            question_number=q_num,
        ):
            c_final, routing = compute_confidence(
                c_ocr=c_ocr,
                c_agree=c_agree,
                c_evid=c_evid,
                c_math=c_math,
                question_type=q_type,
                total_illegible=len(illegible_regions),
                total_questions=len(ocr_results),
            )

        routing, flag_reason = route_answer(
            grader_score=grader_score,
            critic_score=critic_score,
            critic_verdict=critic_verdict.verdict,
            c_evid=c_evid,
            max_marks=max_marks,
            force_flag=force_flag,
            illegible_count=len(illegible_regions),
            math_unverified=math_requires_manual_review,
        )
        local_routing = routing
        if math_requires_manual_review:
            routing_reason = (
                f"{routing_reason} Numerical verification requires teacher review "
                "because no explicit answer expression was available or SymPy could not confirm it."
            ).strip()

        verification_status = "NOT_REQUESTED"
        independent_verification = None
        trigger_reason = _gemini_verification_reason(
            critic_verdict=critic_verdict.verdict,
            force_flag=force_flag,
            illegible_regions=illegible_regions,
            math_requires_manual_review=math_requires_manual_review,
            c_evid=c_evid,
            exam_id=exam_id,
            hall_ticket=hall_ticket,
            question_number=q_num,
            sub_part=sub_part,
        )
        # Gemini is a fallback for answers already going to the teacher, and nothing else.
        trigger_reason = "" if routing == "AUTO_ACCEPT" else (trigger_reason or "low_confidence")
        verification_enabled = (
            os.getenv("GEMINI_VERIFICATION_ENABLED", "false").strip().lower() == "true"
        )
        if trigger_reason and verification_enabled and model_router.gemini_calls_left() <= 0:
            verification_status = "SKIPPED_RUN_LIMIT_REACHED"
            independent_verification = {
                "status": verification_status,
                "trigger_reason": trigger_reason,
                "visual_evidence_included": False,
            }
        elif trigger_reason and verification_enabled:
            evidence_region_ids = list(dict.fromkeys(
                region_id
                for criterion in final_criteria
                for region_id in criterion.evidence_region_ids
            ))
            verification_images = _crop_evidence_regions(
                ocr_pages,
                page_image_parts,
                evidence_region_ids,
            )
            if not verification_images:
                verification_status = "NEEDS_TEACHER_REVIEW_NO_MAPPED_IMAGE"
                independent_verification = {
                    "status": verification_status,
                    "trigger_reason": trigger_reason,
                    "evidence_region_ids": evidence_region_ids,
                    "visual_evidence_included": False,
                }
                routing = "MANUAL"
                routing_reason = (
                    f"{routing_reason} Independent Gemini verification was not run because "
                    "no safely mapped source image region was available."
                ).strip()
            else:
                verification_task_id = hashlib.sha256(
                    f"{source_hash}:{q_num}:{sub_part}:gemini-verification".encode("utf-8")
                ).hexdigest()[:24]
                verification_prompt = _independent_verification_prompt(
                    rubric=rubric,
                    extracted_text=extracted_text,
                    criteria=final_criteria,
                    local_score=final_score,
                )
                try:
                    response = model_router.generate_gemini_verification(
                        task_id=verification_task_id,
                        prompt=verification_prompt,
                        images=verification_images,
                        response_json_schema=INDEPENDENT_VERIFICATION_SCHEMA,
                    )
                    verification_payload = response.parsed_json
                    if not isinstance(verification_payload, dict):
                        verification_payload = json.loads(response.text)
                    if not isinstance(verification_payload, dict):
                        raise ValueError("Gemini verification output was not a JSON object.")
                    required_fields = {
                        "transcription", "addresses_question", "evidence_supported",
                        "missed_relevant_content", "suggested_score", "reason",
                    }
                    if not required_fields.issubset(verification_payload):
                        raise ValueError("Gemini verification response was incomplete.")
                    if (
                        not isinstance(verification_payload.get("transcription"), str)
                        or type(verification_payload.get("addresses_question")) is not bool
                        or type(verification_payload.get("evidence_supported")) is not bool
                        or type(verification_payload.get("missed_relevant_content")) is not bool
                        or isinstance(verification_payload.get("suggested_score"), bool)
                        or not isinstance(verification_payload.get("suggested_score"), (int, float))
                        or not isinstance(verification_payload.get("reason"), str)
                    ):
                        raise ValueError("Gemini verification response had invalid field types.")
                    status, status_reason = _compare_independent_verification(
                        verification_payload,
                        local_score=final_score,
                        local_transcription=extracted_text,
                        max_marks=max_marks,
                    )
                    verification_status = status
                    independent_verification = {
                        "status": status,
                        "trigger_reason": trigger_reason,
                        "transcription": str(verification_payload.get("transcription", "")),
                        "addresses_question": verification_payload["addresses_question"],
                        "evidence_supported": verification_payload["evidence_supported"],
                        "missed_relevant_content": verification_payload["missed_relevant_content"],
                        "suggested_score": verification_payload.get("suggested_score"),
                        "reason": str(verification_payload.get("reason", "")),
                        "comparison_reason": status_reason,
                        "model": response.model_used,
                        "credential_id": response.credential_id,
                        "duration_seconds": response.duration_seconds,
                        "evidence_region_ids": evidence_region_ids,
                        "visual_evidence_included": True,
                        "local_score_preserved": final_score,
                    }
                    if status == "AGREEMENT" and routing == "FLAGGED":
                        routing, flag_reason = "AUTO_ACCEPT", ""
                        routing_reason = (
                            f"{routing_reason} Gemini's independent reading agreed with the local mark."
                        ).strip()
                    if status != "AGREEMENT":
                        routing = "MANUAL"
                        routing_reason = (
                            f"{routing_reason} Gemini independent verification requires teacher review: "
                            f"{status_reason} The existing local score was preserved."
                        ).strip()
                except Exception as error:
                    _logger.warning(
                        "independent_verification_failed task_id=%s error_type=%s",
                        verification_task_id,
                        type(error).__name__,
                    )
                    verification_status = "FAILED_REQUIRES_TEACHER_REVIEW"
                    independent_verification = {
                        "status": verification_status,
                        "trigger_reason": trigger_reason,
                        "failure_type": type(error).__name__,
                        "evidence_region_ids": evidence_region_ids,
                        "visual_evidence_included": True,
                        "local_score_preserved": final_score,
                    }
                    routing = "MANUAL"
                    routing_reason = (
                        f"{routing_reason} Independent Gemini verification did not complete; "
                        "teacher review is required."
                    ).strip()
        elif trigger_reason:
            verification_status = "DISABLED_REQUIRES_TEACHER_REVIEW"
            independent_verification = {
                "status": verification_status,
                "trigger_reason": trigger_reason,
                "visual_evidence_included": False,
            }
        question_grades.append(
            QuestionGrade(
                question_number=q_num,
                sub_part=sub_part,
                choice_group=rubric_group,
                question_type=q_type,
                max_marks=max_marks,
                extracted_text=extracted_text,
                grading_input_text=extracted_text,
                source_page_references=source_page_references,
                grader_score=grader_score,
                critic_score=critic_score,
                final_score=final_score if routing == "AUTO_ACCEPT" else 0.0,
                suggested_score=None if routing == "AUTO_ACCEPT" else final_score,
                flag_reason=flag_reason,
                local_routing=local_routing,
                criteria=final_criteria,
                c_ocr=c_ocr,
                c_agree=c_agree,
                c_evid=c_evid,
                c_math=c_math,
                c_final=c_final,
                routing=routing,
                illegible_regions=illegible_regions,
                model_used=model_used,
                routing_reason=routing_reason,
                ocr_confidence_class="UNCALIBRATED",
                verification_status=verification_status,
                independent_verification=independent_verification,
            )
        )

    # -------------------------------------------------
    # Step 5: Build PaperResult
    # -------------------------------------------------
    if on_stage is not None:
        on_stage("final_marks_calculation", "Validating question awards against approved exam marks")
    marks_started_at = time.perf_counter()
    total_score = sum(q.final_score for q in question_grades)
    max_total = (
        float(approved_total_marks)
        if approved_total_marks is not None and approved_total_marks > 0
        else sum(q.max_marks for q in rubric_list)
    )
    rubric_total = sum(r.max_marks for r in rubric_list)
    if not math.isfinite(max_total) or max_total <= 0:
        raise ValueError("Approved exam total must be a finite positive mark value.")
    if any(
        not math.isfinite(q.final_score)
        or q.final_score < 0
        or q.final_score > q.max_marks
        for q in question_grades
    ):
        raise ValueError("A question score is outside the maximum marks in its approved rubric.")
    if total_score > max_total:
        raise ValueError(
            f"Calculated score {total_score:.2f} exceeds the approved exam total "
            f"{max_total:.2f}; evaluation rejected rather than capped."
        )
    log_timing(
        "final_marks_calculation",
        marks_started_at,
        job_id=job_id,
        score=round(total_score, 4),
        approved_total=round(max_total, 4),
        rubric_question_max_sum=round(rubric_total, 4),
        question_count=len(question_grades),
    )
    flagged_count = sum(
        1 for q in question_grades if q.routing in ("FLAGGED", "MANUAL")
    )
    avg_confidence = (
        sum(q.c_final for q in question_grades) / len(question_grades)
        if question_grades
        else 0.0
    )

    rubric_numbers = {r.question_number for r in rubric_list}
    unmapped_answers = [
        {
            "detected_question_number": answer.get("detected_question_number", key[0]),
            "candidate_question_number": answer.get("candidate_question_number"),
            "sub_part": answer.get("sub_part", key[1]),
            "extracted_text": answer.get("extracted_text", ""),
            "illegible_regions": answer.get("illegible_regions", []),
            "mapping_status": (
                "NOT_ON_PAPER" if key[0] > 0 and key[0] not in rubric_numbers
                else answer.get("mapping_status", "UNRESOLVED")
            ),
            "page_references": answer.get("page_references", []),
        }
        for key, answer in ocr_results.items()
        if key[0] == 0
        or key[0] not in rubric_numbers
        or answer.get("mapping_status", "MAPPED") != "MAPPED"
    ]
    unreadable_pages = [r["page"] for r in page_readers if r.get("status") == "UNREADABLE"]

    status = "COMPLETE"
    if flagged_count > 0 or unmapped_answers or unreadable_pages:
        status = "NEEDS_REVIEW"

    paper_result = PaperResult(
        hall_ticket=hall_ticket,
        student_name=student_name,
        exam_id=exam_id,
        questions=question_grades,
        total_score=total_score,
        max_total=max_total,
        avg_confidence=round(avg_confidence, 4),
        flagged_count=flagged_count,
        status=status,
        finality_status="PENDING_VALIDATION",
        source_file_reference=source_file_reference,
        rubric_version=str(approved_rubric_version),
        ocr_pages=ocr_pages,
        unmapped_answers=unmapped_answers,
        page_readers=page_readers,
    )

    # -------------------------------------------------
    # Step 5 (cont): Write to Firestore
    # -------------------------------------------------
    with timed_stage(
        "response_and_firestore_persistence",
        job_id=job_id,
        on_stage=on_stage,
        detail="Persisting the complete evaluation result",
    ):
        _save_to_firestore(exam_id, hall_ticket, paper_result)

    print(f"\n[Pipeline] Done: {hall_ticket} — "
          f"{total_score}/{max_total}, status={status}\n")

    return paper_result


# =========================================================
# FIRESTORE PERSISTENCE
# =========================================================

def _grading_failed_grade(
    rubric: QuestionRubric,
    q_type: str,
    rubric_group: str,
    extracted_text: str,
    source_page_references: list,
    c_ocr: float,
    illegible_regions: list[str],
    failure: str,
) -> QuestionGrade:
    return QuestionGrade(
        question_number=rubric.question_number,
        sub_part=rubric.sub_part.strip().upper(),
        choice_group=rubric_group,
        question_type=q_type,
        max_marks=rubric.max_marks,
        extracted_text=extracted_text,
        grading_input_text=extracted_text,
        source_page_references=source_page_references,
        c_ocr=c_ocr,
        routing="MANUAL",
        local_routing="MANUAL",
        flag_reason="GRADING_FAILED",
        illegible_regions=illegible_regions,
        routing_reason=f"Grading did not finish ({failure}); no marks were inferred.",
    )


def _save_to_firestore(
    exam_id: str,
    hall_ticket: str,
    result: PaperResult,
) -> None:
    """Save PaperResult to Firestore across grading_results/ and exams/{exam_id}/results."""
    if not db_service.is_connected():
        raise RuntimeError("Firestore is unavailable; grading result was not persisted.")
    db_service.save_grading_result(
        exam_id=exam_id,
        hall_ticket=hall_ticket,
        paper_result=result,
    )
    _logger.info("Persisted evaluation result to Firestore for %s", hall_ticket)
