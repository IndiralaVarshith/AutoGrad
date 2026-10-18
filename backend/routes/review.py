"""
AutoGrade — Review Routes

POST /review/confirm — Mark question as teacher-confirmed
POST /review/edit    — Update score, record edit reason and log correction
"""

from __future__ import annotations

import datetime
from fastapi import APIRouter, HTTPException

from config import get_firestore
from models.schemas import ReviewConfirmRequest, ReviewEditRequest


router = APIRouter(prefix="/review", tags=["Review"])


# =========================================================
# POST /review/confirm
# =========================================================

@router.post("/confirm")
async def confirm_question(req: ReviewConfirmRequest):
    """
    Teacher confirms an AI-flagged question score as acceptable.
    Updates the question's routing to AUTO_ACCEPT (or CONFIRMED),
    decrements flagged count, and updates overall paper status.
    """
    db = get_firestore()
    if db is None:
        raise HTTPException(status_code=503, detail="Firestore is unavailable.")

    doc_ref = (
        db.collection("exams")
        .document(req.exam_id)
        .collection("results")
        .document(req.hall_ticket)
    )

    doc = doc_ref.get()
    if not doc.exists:
        raise HTTPException(
            status_code=404,
            detail=f"Result for {req.hall_ticket} not found in exam {req.exam_id}."
        )

    data = doc.to_dict()
    questions = data.get("questions", [])
    found = False

    for q in questions:
        if q.get("question_number") == req.question_number:
            found = True
            q["teacher_confirmed"] = True
            q["routing"] = "AUTO_ACCEPT"
            break

    if not found:
        raise HTTPException(
            status_code=404,
            detail=f"Question {req.question_number} not found in paper."
        )

    # Recalculate flagged count and paper status
    flagged_count = sum(
        1 for q in questions if q.get("routing") in ("FLAGGED", "MANUAL") and not q.get("teacher_confirmed")
    )
    data["questions"] = questions
    data["flagged_count"] = flagged_count
    if flagged_count == 0:
        data["status"] = "COMPLETE"

    doc_ref.set(data)

    # Log review action
    log_entry = {
        "exam_id": req.exam_id,
        "hall_ticket": req.hall_ticket,
        "question_number": req.question_number,
        "action": "CONFIRM",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    db.collection("exams").document(req.exam_id).collection("review_logs").add(log_entry)

    return {
        "success": True,
        "message": f"Question {req.question_number} confirmed for {req.hall_ticket}.",
        "flagged_count": flagged_count,
        "status": data["status"],
    }


# =========================================================
# POST /review/edit
# =========================================================

@router.post("/edit")
async def edit_question_score(req: ReviewEditRequest):
    """
    Teacher overrides the score for a specific question.
    Updates the score, recalculates total, records edit reason,
    and writes to review logs.
    """
    db = get_firestore()
    if db is None:
        raise HTTPException(status_code=503, detail="Firestore is unavailable.")

    doc_ref = (
        db.collection("exams")
        .document(req.exam_id)
        .collection("results")
        .document(req.hall_ticket)
    )

    doc = doc_ref.get()
    if not doc.exists:
        raise HTTPException(
            status_code=404,
            detail=f"Result for {req.hall_ticket} not found in exam {req.exam_id}."
        )

    data = doc.to_dict()
    questions = data.get("questions", [])
    found = False
    old_score = 0.0

    for q in questions:
        if q.get("question_number") == req.question_number:
            found = True
            old_score = float(q.get("final_score", 0.0))
            max_m = float(q.get("max_marks", req.new_score))
            if req.new_score > max_m:
                raise HTTPException(
                    status_code=400,
                    detail=f"Score cannot exceed question maximum marks ({max_m})."
                )
            if req.new_score < 0:
                raise HTTPException(
                    status_code=400,
                    detail="Score cannot be negative."
                )

            q["final_score"] = float(req.new_score)
            q["teacher_override"] = True
            q["teacher_confirmed"] = True
            q["edit_reason"] = req.edit_reason
            q["routing"] = "AUTO_ACCEPT"
            break

    if not found:
        raise HTTPException(
            status_code=404,
            detail=f"Question {req.question_number} not found in paper."
        )

    # Recalculate totals and flagged status
    total_score = sum(float(q.get("final_score", 0.0)) for q in questions)
    flagged_count = sum(
        1 for q in questions if q.get("routing") in ("FLAGGED", "MANUAL") and not q.get("teacher_confirmed")
    )
    data["questions"] = questions
    data["total_score"] = round(total_score, 2)
    data["flagged_count"] = flagged_count
    if flagged_count == 0:
        data["status"] = "COMPLETE"

    doc_ref.set(data)

    # Log review correction
    log_entry = {
        "exam_id": req.exam_id,
        "hall_ticket": req.hall_ticket,
        "question_number": req.question_number,
        "action": "EDIT",
        "old_score": old_score,
        "new_score": req.new_score,
        "edit_reason": req.edit_reason,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    db.collection("exams").document(req.exam_id).collection("review_logs").add(log_entry)

    return {
        "success": True,
        "message": f"Question {req.question_number} score updated to {req.new_score} for {req.hall_ticket}.",
        "new_score": req.new_score,
        "total_score": data["total_score"],
        "flagged_count": flagged_count,
        "status": data["status"],
    }
