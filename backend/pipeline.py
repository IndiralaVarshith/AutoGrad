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

from models.schemas import (
    PaperResult,
    QuestionGrade,
    CriterionResult,
    QuestionRubric,
    RubricCriterion,
)
from services.ocr import extract_text
from services.classifier import classify_question
from services.grading_agent import grade_answer
from services.critic_agent import critique_grade
from services.resolver import resolve
from services.math_agent import verify_math
from services.evidence_verifier import verify_evidence
from services.confidence_engine import compute_c_agree, compute_confidence
from config import get_firestore


# =========================================================
# MAIN PIPELINE
# =========================================================

def process_paper(
    exam_id: str,
    hall_ticket: str,
    student_name: str,
    image_parts: list,
    rubric_list: list[QuestionRubric],
    image_bytes_list: list[bytes] | None = None,
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
        Raw image bytes for Pix2Text (math questions).

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
    print("[Pipeline] Step 1: OCR extraction...")
    ocr_results, c_ocr = extract_text(image_parts)

    # Build rubric lookup
    rubric_map: dict[int, QuestionRubric] = {
        r.question_number: r for r in rubric_list
    }

    # -------------------------------------------------
    # Step 2: Classification (for questions not already typed)
    # -------------------------------------------------
    print("[Pipeline] Step 2: Question classification...")
    question_types: dict[int, str] = {}
    for rubric in rubric_list:
        q_type = classify_question(rubric.question_text)
        question_types[rubric.question_number] = q_type
        print(f"  Q{rubric.question_number}: {q_type}")

    # -------------------------------------------------
    # Steps 3-4: Per-question grading
    # -------------------------------------------------
    print("[Pipeline] Steps 3-4: Per-question grading...")
    question_grades: list[QuestionGrade] = []

    for rubric in rubric_list:
        q_num = rubric.question_number
        q_type = question_types.get(q_num, "THEORY")
        max_marks = rubric.max_marks

        ocr_data = ocr_results.get(q_num, {})
        extracted_text = ocr_data.get("extracted_text", "")
        illegible_regions = ocr_data.get("illegible_regions", [])

        print(f"\n  [Q{q_num}] Type={q_type}, Max={max_marks}")

        # -------------------------------------------------
        # Step 4a: Math agent for NUMERICAL questions
        # -------------------------------------------------
        if q_type == "NUMERICAL" and image_bytes_list:
            try:
                # Use first image for math extraction
                math_result = verify_math(
                    image_bytes=image_bytes_list[0],
                    model_answer_expr="",  # No model answer provided
                )
                c_math = math_result.c_math
            except Exception as error:
                print(f"  [Q{q_num}] Math agent error: {error}")
                c_math = 0.4
        else:
            c_math = 1.0  # Neutral for non-numerical

        # -------------------------------------------------
        # Step 4b: Grading Agent
        # -------------------------------------------------
        grader_criteria, grader_score = grade_answer(
            question_text=rubric.question_text,
            extracted_text=extracted_text,
            image_parts=image_parts,
            rubric=rubric,
            max_marks=max_marks,
        )

        # -------------------------------------------------
        # Step 4c: Critic Agent
        # -------------------------------------------------
        critic_verdict = critique_grade(
            question_text=rubric.question_text,
            extracted_text=extracted_text,
            image_parts=image_parts,
            rubric=rubric,
            grader_criteria=grader_criteria,
            grader_score=grader_score,
            max_marks=max_marks,
        )
        critic_score = critic_verdict.suggested_score

        # -------------------------------------------------
        # Step 4d: Resolver (if REJECT)
        # -------------------------------------------------
        final_criteria = grader_criteria
        final_score = grader_score
        force_flag = False

        if critic_verdict.verdict == "REJECT":
            final_criteria, final_score, force_flag = resolve(
                question_text=rubric.question_text,
                extracted_text=extracted_text,
                image_parts=image_parts,
                rubric=rubric,
                max_marks=max_marks,
                original_criteria=grader_criteria,
                original_score=grader_score,
            )

        # -------------------------------------------------
        # Step 4e: c_agree
        # -------------------------------------------------
        c_agree = compute_c_agree(grader_score, critic_score, max_marks)

        # -------------------------------------------------
        # Step 4f: Evidence Verifier
        # -------------------------------------------------
        final_criteria, c_evid = verify_evidence(
            criteria=final_criteria,
            ocr_transcript=extracted_text,
        )

        # Recalculate final_score after evidence stripping
        final_score = min(
            sum(c.marks_awarded for c in final_criteria),
            max_marks,
        )

        # -------------------------------------------------
        # Step 4g: Confidence Engine
        # -------------------------------------------------
        c_final, routing = compute_confidence(
            c_ocr=c_ocr,
            c_agree=c_agree,
            c_evid=c_evid,
            c_math=c_math,
            question_type=q_type,
        )

        # Override routing if resolver forced flag
        if force_flag:
            routing = "FLAGGED"

        # Build QuestionGrade
        question_grades.append(
            QuestionGrade(
                question_number=q_num,
                question_type=q_type,
                max_marks=max_marks,
                extracted_text=extracted_text,
                grader_score=grader_score,
                critic_score=critic_score,
                final_score=final_score,
                criteria=final_criteria,
                c_ocr=c_ocr,
                c_agree=c_agree,
                c_evid=c_evid,
                c_math=c_math,
                c_final=c_final,
                routing=routing,
                illegible_regions=illegible_regions,
            )
        )

    # -------------------------------------------------
    # Step 5: Build PaperResult
    # -------------------------------------------------
    total_score = sum(q.final_score for q in question_grades)
    max_total = sum(q.max_marks for q in question_grades)
    flagged_count = sum(
        1 for q in question_grades if q.routing in ("FLAGGED", "MANUAL")
    )
    avg_confidence = (
        sum(q.c_final for q in question_grades) / len(question_grades)
        if question_grades
        else 0.0
    )

    status = "COMPLETE"
    if flagged_count > 0:
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
    )

    # -------------------------------------------------
    # Step 5 (cont): Write to Firestore
    # -------------------------------------------------
    _save_to_firestore(exam_id, hall_ticket, paper_result)

    print(f"\n[Pipeline] Done: {hall_ticket} — "
          f"{total_score}/{max_total}, status={status}\n")

    return paper_result


# =========================================================
# FIRESTORE PERSISTENCE
# =========================================================

def _save_to_firestore(
    exam_id: str,
    hall_ticket: str,
    result: PaperResult,
) -> None:
    """Save PaperResult to Firestore under /exams/{exam_id}/results/{hall_ticket}."""
    try:
        db = get_firestore()
        if db is None:
            print("[Pipeline] Firestore unavailable — skipping save.")
            return

        doc_ref = (
            db.collection("exams")
            .document(exam_id)
            .collection("results")
            .document(hall_ticket)
        )
        doc_ref.set(result.model_dump())
        print(f"[Pipeline] Saved to Firestore: exams/{exam_id}/results/{hall_ticket}")

    except Exception as error:
        print(f"[Pipeline] Firestore write error: {error}")
