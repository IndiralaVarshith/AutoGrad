"""
AutoGrade — Results Routes

GET /results/{exam_id}               — All results for an exam
GET /results/{exam_id}/{hall_ticket}  — Single student result
GET /results/single/{hall_ticket}     — Single student result via query param
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from config import get_firestore


router = APIRouter(prefix="/results", tags=["Results"])


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
            results.append(doc.to_dict())

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

        return doc.to_dict()

    except HTTPException:
        raise

    except Exception as error:
        raise HTTPException(500, f"Failed to fetch result: {error}")
