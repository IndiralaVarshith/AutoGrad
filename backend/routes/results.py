"""
AutoGrade — Results Routes

GET    /results/{exam_id}               — All results for an exam
GET    /results/{exam_id}/{hall_ticket}  — Single student result
DELETE /results/{exam_id}/{hall_ticket}  — Delete one answer sheet everywhere it is kept
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from config import get_firestore
from services import sheet_store
from services.db import db_service
from services.queue_manager import JobBusy, queue_manager


router = APIRouter(prefix="/results", tags=["Results"])


def _without_negative_rows(result: dict | None) -> dict | None:
    """Sheets marked before negative rows were removed still hold "Taken off" rows.

    Fold each into the points above it, so the rows add up to the same mark and none
    is below 0. The saved mark and the saved record are not changed.
    """
    for question in (result or {}).get("questions") or []:
        rows = question.get("criteria") or []
        if not any((row.get("marks_awarded") or 0) < 0 for row in rows):
            continue
        kept = [dict(row) for row in rows if (row.get("marks_awarded") or 0) >= 0]
        for taken in rows:
            amount = -float(taken.get("marks_awarded") or 0)
            if amount <= 0:
                continue
            why = str(taken.get("criterion") or "").removeprefix("Taken off: ").strip()
            for row in reversed(kept):
                cut = min(float(row.get("marks_awarded") or 0), amount)
                if cut <= 0:
                    continue
                row["marks_awarded"] = float(row["marks_awarded"]) - cut
                row["rationale"] = f"{row.get('rationale') or ''} {why}.".strip()
                amount -= cut
                if amount <= 1e-6:
                    break
        question["criteria"] = kept
    return result


# =========================================================
# GET /results/{exam_id}
# =========================================================

@router.get("/{exam_id}")
async def get_all_results(exam_id: str):
    """Return all PaperResult documents for an exam."""

    db = get_firestore()
    if db is None:
        return {"exam_id": exam_id, "results": [], "message": "Firestore unavailable."}

    try:
        docs = (
            db.collection("exams")
            .document(exam_id)
            .collection("results")
            .stream()
        )

        results = []
        for doc in docs:
            results.append(_without_negative_rows(doc.to_dict()))

        return {
            "exam_id": exam_id,
            "count": len(results),
            "results": results,
        }

    except Exception as error:
        raise HTTPException(500, f"Failed to fetch results: {error}")


# =========================================================
# GET /results/{exam_id}/{hall_ticket}
# =========================================================

@router.get("/{exam_id}/{hall_ticket}")
async def get_single_result(exam_id: str, hall_ticket: str):
    """Return a single PaperResult for one student."""

    db = get_firestore()
    if db is None:
        raise HTTPException(503, "Firestore unavailable.")

    try:
        doc = (
            db.collection("exams")
            .document(exam_id)
            .collection("results")
            .document(hall_ticket)
            .get()
        )

        if not doc.exists:
            raise HTTPException(
                404,
                f"No result found for hall ticket {hall_ticket} in exam {exam_id}."
            )

        return _without_negative_rows(doc.to_dict())

    except HTTPException:
        raise

    except Exception as error:
        raise HTTPException(500, f"Failed to fetch result: {error}")


# =========================================================
# DELETE /results/{exam_id}/{hall_ticket}
# =========================================================

@router.delete("/{exam_id}/{hall_ticket}")
async def delete_single_result(exam_id: str, hall_ticket: str):
    """Delete one student's answer sheet: its marks, review log, stored pages and queue entry."""
    if not db_service.is_connected():
        raise HTTPException(503, "Firestore is unavailable, so nothing was deleted.")
    try:
        jobs = queue_manager.remove_jobs(exam_id, hall_ticket)
    except JobBusy as error:
        raise HTTPException(409, f"{error} Nothing was deleted.")
    try:
        existed = db_service.delete_result(exam_id, hall_ticket)
    except Exception as error:
        print(f"[ResultDelete] Error: {type(error).__name__}: {error}")
        raise HTTPException(503, "The answer sheet could not be fully deleted. Please try again.")
    if not existed and not jobs:
        raise HTTPException(404, f"There is no answer sheet {hall_ticket} in exam {exam_id}.")
    sheet_store.delete(exam_id, hall_ticket)
    _delete_cloud_copies(exam_id, hall_ticket)
    # A calibration learned from this sheet goes with it.
    calibration = db_service.get_calibration(exam_id)
    calibration_removed = bool(calibration and calibration.get("hall_ticket") == hall_ticket)
    if calibration_removed:
        db_service.delete_calibration(exam_id)
    return {
        "success": True,
        "exam_id": exam_id,
        "hall_ticket": hall_ticket,
        "calibration_removed": calibration_removed,
    }


def _delete_cloud_copies(exam_id: str, hall_ticket: str | None = None) -> None:
    """Best effort: the database is already clean, so a Storage hiccup only leaves a file."""
    try:
        sheet_store.delete_cloud_copies(exam_id, hall_ticket)
    except Exception as error:
        print(f"[Delete] Cloud copy not removed for {exam_id}/{hall_ticket or '*'}: {type(error).__name__}")
