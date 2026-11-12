"""
AutoGrade — Calibration Routes

One teacher-marked sheet that later sheets of the same exam are marked to.

GET    /calibration/{exam_id}                — what the exam is calibrated with, if anything
POST   /calibration/{exam_id}/sheet/{ticket} — use this marked sheet (approving it if it is ready)
DELETE /calibration/{exam_id}                — stop using it; marks already given stay
POST   /calibration/{exam_id}/remark         — mark again, to the calibration, sheets the teacher
                                              has not touched yet (one Gemini request each)
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import get_firestore
from routes.review import _approval_blockers, _approve, _load_result, _log, _refresh_validation_status
from routes.upload import ALLOWED_MIME, _execute_grading_job, _load_rubric_bundle
from services import calibration, sheet_store
from services.db import db_service
from services.queue_manager import JobBusy, queue_manager

router = APIRouter(prefix="/calibration", tags=["Calibration"])


class UseSheetRequest(BaseModel):
    unmatched_checked: bool = False  # the teacher looked at writing that fit no question


def _require_db():
    db = get_firestore()
    if db is None:
        raise HTTPException(503, "Firestore is unavailable.")
    return db


def _remark_candidates(exam_id: str, chosen: dict) -> tuple[list[dict], list[str]]:
    """(sheets that can be marked again, sheets kept as they are).

    A sheet is kept when the teacher has touched it or it was marked with other approved
    marks; one already marked to this calibration needs nothing.
    """
    ready: list[dict] = []
    kept: list[str] = []
    for result in db_service.list_exam_results(exam_id):
        ticket = str(result.get("hall_ticket") or "")
        if not ticket or ticket == chosen.get("hall_ticket"):
            continue
        if result.get("calibration_id") == chosen.get("calibration_id"):
            continue
        if (
            calibration.has_teacher_decisions(result)
            or str(result.get("rubric_version", "")) != str(chosen.get("rubric_version", ""))
        ):
            kept.append(ticket)
            continue
        ready.append(result)
    return ready, kept


def _status(exam_id: str, chosen: dict | None) -> dict:
    info = calibration.summary(chosen)
    if not chosen:
        return info
    rubric = db_service.get_approved_rubric(exam_id) or {}
    ready, kept = _remark_candidates(exam_id, chosen)
    return {
        **info,
        # Made with other approved marks: new sheets no longer use it.
        "current": str(rubric.get("version", "")) == info["rubric_version"],
        "can_remark": sorted(str(result.get("hall_ticket")) for result in ready),
        "kept": sorted(kept),
    }


@router.get("/{exam_id}")
def calibration_status(exam_id: str):
    _require_db()
    return _status(exam_id, db_service.get_calibration(exam_id))


@router.post("/{exam_id}/sheet/{hall_ticket}")
def use_sheet(exam_id: str, hall_ticket: str, req: UseSheetRequest | None = None):
    """Learn from this sheet. It must be fully marked; if it is, it is approved here too."""
    req = req or UseSheetRequest()
    db = _require_db()
    doc_ref, data = _load_result(db, exam_id, hall_ticket)
    if data.get("finality_status") != "FINAL":
        _refresh_validation_status(data)
        blockers = _approval_blockers(data, req.unmatched_checked)
        if blockers:
            raise HTTPException(409, "Finish marking this sheet first. " + " ".join(blockers))
        _approve(db, doc_ref, exam_id, hall_ticket, data, "APPROVE")
    rubric = db_service.get_approved_rubric(exam_id)
    if not rubric:
        raise HTTPException(409, f"Exam '{exam_id}' has no approved marks to calibrate.")
    if str(rubric.get("version", "")) != str(data.get("rubric_version", "")):
        raise HTTPException(
            409,
            "This sheet was marked with an older version of the exam's marks. "
            "Choose a sheet marked with the current marks.",
        )
    record = calibration.build({**data, "exam_id": exam_id, "hall_ticket": hall_ticket}, rubric.get("questions") or [])
    if not record["examples"]:
        raise HTTPException(409, "This sheet has no written answers to learn from. Choose a sheet with written answers.")
    db_service.save_calibration(exam_id, record)
    _log(db, exam_id, {
        "hall_ticket": hall_ticket,
        "action": "CALIBRATE",
        "answers": len(record["examples"]),
        "corrections": record["corrections"],
    })
    return {"success": True, "finality_status": "FINAL", **_status(exam_id, record)}


@router.delete("/{exam_id}")
def stop_calibration(exam_id: str):
    db = _require_db()
    if not db_service.delete_calibration(exam_id):
        raise HTTPException(404, f"Exam '{exam_id}' is not calibrated.")
    _log(db, exam_id, {"action": "CALIBRATION_REMOVED"})
    return {"success": True, "active": False}


@router.post("/{exam_id}/remark")
def remark_sheets(exam_id: str):
    """Mark again the sheets that came before the calibration, if the teacher has not touched them."""
    db = _require_db()
    chosen = db_service.get_calibration(exam_id)
    if not chosen:
        raise HTTPException(404, f"Exam '{exam_id}' is not calibrated.")
    rubric_list, total_marks, version, fingerprint = _load_rubric_bundle(exam_id)
    if not rubric_list or not calibration.applies_to(chosen, version):
        raise HTTPException(
            409,
            "The calibration was made with an older version of the exam's marks. Choose a calibration sheet again.",
        )
    ready, kept = _remark_candidates(exam_id, chosen)
    queued: list[str] = []
    missing: list[str] = []
    busy: list[str] = []
    for result in ready:
        ticket = str(result["hall_ticket"])
        stored = sheet_store.load(exam_id, ticket)
        if not stored:
            missing.append(ticket)
            continue
        file_bytes, extension = stored
        try:
            queue_manager.remove_jobs(exam_id, ticket)
        except JobBusy:
            busy.append(ticket)
            continue
        queue_manager.enqueue(
            exam_id=exam_id,
            hall_ticket=ticket,
            student_name=str(result.get("student_name") or ""),
            file_bytes=file_bytes,
            file_ext=extension,
            mime_type=ALLOWED_MIME[extension],
            request_key=hashlib.sha256(
                f"{exam_id}\0{ticket}\0{chosen['calibration_id']}\0{fingerprint}".encode()
            ).hexdigest(),
            original_filename=Path(str(result.get("source_file_reference") or "")).name or f"{ticket}{extension}",
            rubric_fingerprint=fingerprint,
            rubric_version=version,
            rubric_total_marks=total_marks,
            rubric_data=[question.model_dump() for question in rubric_list],
        )
        queued.append(ticket)
    if queued:
        queue_manager.start_worker(_execute_grading_job)
    _log(db, exam_id, {"action": "CALIBRATION_REMARK", "queued": queued, "kept": kept})
    return {"success": True, "queued": queued, "kept": sorted(kept), "missing": missing, "busy": busy}
