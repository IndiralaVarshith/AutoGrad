"""
AutoGrade — Review Routes

POST /review/confirm — Mark question as teacher-confirmed
POST /review/edit    — Update score, record edit reason and log correction
"""

from __future__ import annotations

import datetime
import io
import math
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response

from config import get_firestore, get_storage_bucket
from models.schemas import (
    OCRPageReviewRequest,
    OCRRegionReviewRequest,
    ReviewApproveRequest,
    ReviewConfirmRequest,
    ReviewEditRequest,
)
from services import sheet_store
from services.handwriting_dataset import save_teacher_validated_candidate


router = APIRouter(prefix="/review", tags=["Review"])


def _result_ref(db, exam_id: str, hall_ticket: str):
    return (
        db.collection("exams")
        .document(exam_id)
        .collection("results")
        .document(hall_ticket)
    )


def _write_review_result(db, doc_ref, exam_id: str, hall_ticket: str, data: dict) -> None:
    doc_ref.set(data)
    # Keep both existing result records in sync for clients that read either path.
    db.collection("grading_results").document(f"{exam_id}_{hall_ticket}").set(data)


def _refresh_validation_status(data: dict) -> None:
    questions = data.get("questions", [])
    flagged_count = sum(
        1
        for question in questions
        if question.get("routing") in ("FLAGGED", "MANUAL")
        and not question.get("teacher_confirmed")
    )
    pending_region = any(
        region.get("validation_status", "PENDING") == "PENDING"
        or region.get("question_mapping_status", "UNRESOLVED") != "TEACHER_CONFIRMED"
        or (
            region.get("validation_status") == "UNREADABLE"
            and region.get("teacher_question_number") is None
        )
        for page in data.get("ocr_pages", [])
        for region in page.get("regions", [])
    )
    pending_page = any(
        (
            page.get("status") == "BLANK_CANDIDATE"
            and page.get("validation_status") != "ACCEPTED_BLANK"
        )
        or (
            page.get("status") in ("UNAVAILABLE", "UNREADABLE")
            and not (
                page.get("validation_status") == "UNREADABLE"
                and page.get("teacher_question_number") is not None
            )
        )
        for page in data.get("ocr_pages", [])
    )
    regrade_required = any(question.get("regrade_required") for question in questions)
    data["flagged_count"] = flagged_count
    clear = flagged_count == 0 and not pending_region and not pending_page and not regrade_required
    data["status"] = "COMPLETE" if clear else "NEEDS_REVIEW"
    # Only a teacher's approval makes a sheet final, and any later change undoes it.
    data["finality_status"] = "FINAL" if clear and data.get("teacher_approved") else "PENDING_VALIDATION"


def _find_question(questions: list[dict], question_number: int, sub_part: str = ""):
    normalized_part = sub_part.strip().upper()
    return next(
        (
            question
            for question in questions
            if question.get("question_number") == question_number
            and str(question.get("sub_part", "")).strip().upper() == normalized_part
        ),
        None,
    )


def _validated_total_score(data: dict, questions: list[dict]) -> float:
    scores = [float(question.get("final_score", 0.0)) for question in questions]
    maxima = [float(question.get("max_marks", 0.0)) for question in questions]
    if any(
        not math.isfinite(score)
        or score < 0
        or score > maximum + 1e-6
        for score, maximum in zip(scores, maxima)
    ):
        raise HTTPException(status_code=400, detail="A question score is outside its approved mark range.")
    total_score = sum(scores)
    if not math.isfinite(total_score):
        raise HTTPException(status_code=400, detail="Updated exam total must be finite.")
    max_total = float(data.get("max_total", 0.0) or 0.0)
    if not math.isfinite(max_total) or max_total <= 0:
        raise HTTPException(status_code=409, detail="The approved exam maximum is unavailable.")
    if total_score > max_total + 1e-6:
        raise HTTPException(
            status_code=400,
            detail=f"Updated total {total_score:.2f} exceeds the approved exam maximum ({max_total:.2f}).",
        )
    return total_score






def _render_page(source_bytes: bytes, extension: str, page_number: int) -> bytes:
    if extension == ".pdf":
        import fitz

        with fitz.open(stream=source_bytes, filetype="pdf") as document:
            if page_number > document.page_count:
                raise HTTPException(status_code=404, detail="Page number is outside the document.")
            return document[page_number - 1].get_pixmap(dpi=180, alpha=False).tobytes("png")
    from PIL import Image

    if page_number != 1:
        raise HTTPException(status_code=404, detail="Image uploads contain one page.")
    with Image.open(io.BytesIO(source_bytes)) as image:
        output = io.BytesIO()
        image.convert("RGB").save(output, format="PNG")
        return output.getvalue()


@router.get("/source-page/{exam_id}/{hall_ticket}/{page_number}")
async def get_source_page(exam_id: str, hall_ticket: str, page_number: int):
    """Render the stored original answer-sheet page for evidence review."""
    if page_number < 1:
        raise HTTPException(status_code=400, detail="Page numbers start at 1.")
    db = get_firestore()
    if db is None:
        raise HTTPException(status_code=503, detail="Firestore is unavailable.")
    doc = _result_ref(db, exam_id, hall_ticket).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Paper result was not found.")
    source_reference = str((doc.to_dict() or {}).get("source_file_reference", ""))
    local = sheet_store.load(exam_id, hall_ticket)
    if local:
        return Response(
            content=_render_page(local[0], local[1], page_number),
            media_type="image/png",
            headers={"Cache-Control": "private, max-age=300"},
        )
    if not source_reference:
        raise HTTPException(status_code=404, detail="Original answer-sheet reference is missing.")
    bucket = get_storage_bucket()
    if bucket is None:
        raise HTTPException(status_code=503, detail="Firebase Storage is unavailable.")
    try:
        blob = bucket.blob(source_reference)
        if not blob.exists():
            raise HTTPException(status_code=404, detail="Original answer sheet was not found.")
        source_bytes = blob.download_as_bytes()
        if Path(source_reference).suffix.lower() == ".pdf":
            import fitz

            with fitz.open(stream=source_bytes, filetype="pdf") as document:
                if page_number > document.page_count:
                    raise HTTPException(status_code=404, detail="Page number is outside the document.")
                pixmap = document[page_number - 1].get_pixmap(dpi=180, alpha=False)
                page_bytes = pixmap.tobytes("png")
        else:
            from PIL import Image

            with Image.open(io.BytesIO(source_bytes)) as image:
                if page_number != 1:
                    raise HTTPException(status_code=404, detail="Image uploads contain one page.")
                output = io.BytesIO()
                image.convert("RGB").save(output, format="PNG")
                page_bytes = output.getvalue()
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=502, detail="Could not load the original page image.") from error
    return Response(
        content=page_bytes,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.post("/ocr-region")
async def review_ocr_region(req: OCRRegionReviewRequest):
    """Save a teacher correction without replacing the original OCR evidence."""
    action = req.action.strip().upper()
    if action not in {"ACCEPT", "CORRECT", "UNREADABLE"}:
        raise HTTPException(status_code=400, detail="Action must be ACCEPT, CORRECT, or UNREADABLE.")
    if action == "CORRECT" and not req.corrected_text.strip():
        raise HTTPException(status_code=400, detail="Corrected text cannot be empty.")

    db = get_firestore()
    if db is None:
        raise HTTPException(status_code=503, detail="Firestore is unavailable.")
    doc_ref = _result_ref(db, req.exam_id, req.hall_ticket)
    doc = doc_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Paper result was not found.")
    data = doc.to_dict() or {}
    pages = data.get("ocr_pages", [])
    page = next((item for item in pages if item.get("page_number") == req.page_number), None)
    if page is None:
        raise HTTPException(status_code=404, detail="OCR page was not found.")
    region = next(
        (item for item in page.get("regions", []) if item.get("region_id") == req.region_id),
        None,
    )
    if region is None:
        raise HTTPException(status_code=404, detail="OCR region was not found.")

    questions = data.get("questions", [])
    mapped_question = None
    if req.question_number is not None:
        mapped_question = _find_question(questions, req.question_number, req.sub_part)
        if mapped_question is None:
            raise HTTPException(status_code=400, detail="Question mapping is not in this paper's rubric.")

    previous_question_key = (
        region.get("teacher_question_number"),
        str(region.get("teacher_sub_part", "")).strip().upper(),
    )
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    region["validation_status"] = {
        "ACCEPT": "ACCEPTED",
        "CORRECT": "CORRECTED",
        "UNREADABLE": "UNREADABLE",
    }[action]
    if action == "CORRECT":
        region["teacher_corrected_text"] = req.corrected_text
    elif action == "UNREADABLE":
        region["teacher_corrected_text"] = None
    region["teacher_question_number"] = req.question_number
    region["teacher_sub_part"] = req.sub_part.strip().upper()
    region["question_mapping_status"] = "TEACHER_CONFIRMED" if req.question_number is not None else "UNRESOLVED"
    region["validated_at"] = now

    affected_question_keys = {previous_question_key}
    if req.question_number is not None:
        affected_question_keys.add((req.question_number, req.sub_part.strip().upper()))
    for mapped_number, mapped_part in affected_question_keys:
        affected_question = _find_question(questions, mapped_number, mapped_part) if mapped_number is not None else None
        if affected_question is None:
            continue
        mapped_regions = [
            candidate
            for ocr_page in pages
            for candidate in ocr_page.get("regions", [])
            if candidate.get("teacher_question_number") == mapped_number
            and str(candidate.get("teacher_sub_part", "")).strip().upper() == mapped_part
        ]
        affected_question["ocr_region_ids"] = [item.get("region_id", "") for item in mapped_regions]
        affected_question["teacher_corrected_text"] = "\n".join(
            str(item.get("teacher_corrected_text") or item.get("extracted_text") or "")
            for item in mapped_regions
            if item.get("validation_status") in {"ACCEPTED", "CORRECTED"}
        ).strip()
        affected_question["regrade_required"] = bool(affected_question["teacher_corrected_text"])
        if mapped_regions:
            affected_question["teacher_confirmed"] = False
            affected_question["routing"] = "MANUAL"

    data["ocr_pages"] = pages
    data["questions"] = questions
    data["updated_at"] = now
    _refresh_validation_status(data)
    _write_review_result(db, doc_ref, req.exam_id, req.hall_ticket, data)
    db.collection("exams").document(req.exam_id).collection("review_logs").add({
        "exam_id": req.exam_id,
        "hall_ticket": req.hall_ticket,
        "page_number": req.page_number,
        "region_id": req.region_id,
        "question_number": req.question_number,
        "sub_part": req.sub_part.strip().upper(),
        "action": f"OCR_{action}",
        "original_text": region.get("extracted_text", ""),
        "corrected_text": region.get("teacher_corrected_text"),
        "timestamp": now,
    })
    dataset_candidate = save_teacher_validated_candidate(
        db,
        exam_id=req.exam_id,
        hall_ticket=req.hall_ticket,
        source_file_reference=str(data.get("source_file_reference") or ""),
        page=page,
        region=region,
        action=action,
        corrected_text=region.get("teacher_corrected_text"),
        question_number=req.question_number,
        sub_part=req.sub_part,
    )
    return {
        "success": True,
        "region_id": req.region_id,
        "validation_status": region["validation_status"],
        "status": data["status"],
        "finality_status": data["finality_status"],
        "training_candidate": dataset_candidate["status"],
    }


@router.post("/ocr-page")
async def review_ocr_page(req: OCRPageReviewRequest):
    """Record a teacher disposition for a blank or OCR-unavailable page."""
    action = req.action.strip().upper()
    if action not in {"ACCEPT_BLANK", "UNREADABLE"}:
        raise HTTPException(status_code=400, detail="Action must be ACCEPT_BLANK or UNREADABLE.")
    db = get_firestore()
    if db is None:
        raise HTTPException(status_code=503, detail="Firestore is unavailable.")
    doc_ref = _result_ref(db, req.exam_id, req.hall_ticket)
    doc = doc_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Paper result was not found.")
    data = doc.to_dict() or {}
    pages = data.get("ocr_pages", [])
    page = next((item for item in pages if item.get("page_number") == req.page_number), None)
    if page is None:
        raise HTTPException(status_code=404, detail="OCR page was not found.")
    if action == "ACCEPT_BLANK" and not page.get("blank_candidate"):
        raise HTTPException(status_code=400, detail="Only a blank-page candidate can be accepted as blank.")
    if action == "UNREADABLE" and req.question_number is None:
        raise HTTPException(status_code=400, detail="Map an unreadable page to a question before saving.")

    questions = data.get("questions", [])
    mapped_question = None
    if req.question_number is not None:
        mapped_question = _find_question(questions, req.question_number, req.sub_part)
        if mapped_question is None:
            raise HTTPException(status_code=400, detail="Question mapping is not in this paper's rubric.")
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    page["validation_status"] = "ACCEPTED_BLANK" if action == "ACCEPT_BLANK" else "UNREADABLE"
    page["teacher_question_number"] = req.question_number
    page["teacher_sub_part"] = req.sub_part.strip().upper()
    page["question_mapping_status"] = "TEACHER_CONFIRMED" if req.question_number is not None else "UNRESOLVED"
    page["validated_at"] = now
    if mapped_question is not None:
        mapped_question["regrade_required"] = True
        mapped_question["teacher_confirmed"] = False
        mapped_question["routing"] = "MANUAL"
    data["ocr_pages"] = pages
    data["questions"] = questions
    data["updated_at"] = now
    _refresh_validation_status(data)
    _write_review_result(db, doc_ref, req.exam_id, req.hall_ticket, data)
    db.collection("exams").document(req.exam_id).collection("review_logs").add({
        "exam_id": req.exam_id,
        "hall_ticket": req.hall_ticket,
        "page_number": req.page_number,
        "question_number": req.question_number,
        "sub_part": req.sub_part.strip().upper(),
        "action": f"OCR_PAGE_{action}",
        "timestamp": now,
    })
    return {"success": True, "status": data["status"], "finality_status": data["finality_status"]}




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
    question = _find_question(questions, req.question_number, req.sub_part)
    if question is None:
        raise HTTPException(
            status_code=404,
            detail=f"Question {req.question_number}{req.sub_part} not found in paper."
        )
    if question.get("regrade_required"):
        raise HTTPException(
            status_code=409,
            detail="Review the corrected transcript and set a teacher score before confirming this question.",
        )
    if question.get("routing") == "MANUAL" and question.get("suggested_score") is None:
        # Nothing to confirm: a zero here would be a guessed mark.
        raise HTTPException(
            status_code=409,
            detail="This answer has no suggested mark. Enter the mark yourself.",
        )
    question["teacher_confirmed"] = True
    question["routing"] = "AUTO_ACCEPT"
    if question.get("suggested_score") is not None:
        # Confirming a flagged answer is what turns its draft into a mark.
        question["final_score"] = float(question["suggested_score"])

    data["questions"] = questions
    data["teacher_approved"] = False
    data["total_score"] = round(_validated_total_score(data, questions), 2)
    _refresh_validation_status(data)
    flagged_count = data["flagged_count"]
    _write_review_result(db, doc_ref, req.exam_id, req.hall_ticket, data)

    # Log review action
    log_entry = {
        "exam_id": req.exam_id,
        "hall_ticket": req.hall_ticket,
        "question_number": req.question_number,
        "sub_part": req.sub_part.strip().upper(),
        "action": "CONFIRM",
        "flag_reason": question.get("flag_reason", ""),
        "accepted_score": question.get("final_score"),
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
    old_score = 0.0
    if not math.isfinite(float(req.new_score)):
        raise HTTPException(status_code=400, detail="Score must be finite.")
    question = _find_question(questions, req.question_number, req.sub_part)
    if question is None:
        raise HTTPException(
            status_code=404,
            detail=f"Question {req.question_number}{req.sub_part} not found in paper."
        )
    old_score = float(question.get("final_score", 0.0))
    gemini_score = question.get("suggested_score")
    if gemini_score is None:
        gemini_score = question.get("grader_score")
    old_text = str(question.get("teacher_corrected_text") or question.get("extracted_text") or "")
    max_m = float(question.get("max_marks", req.new_score))
    if req.new_score > max_m:
        raise HTTPException(
            status_code=400,
            detail=f"Score cannot exceed question maximum marks ({max_m})."
        )
    if req.new_score < 0:
        raise HTTPException(status_code=400, detail="Score cannot be negative.")
    question["final_score"] = float(req.new_score)
    if req.corrected_text is not None:
        question["teacher_corrected_text"] = req.corrected_text
    question["teacher_override"] = True
    question["teacher_confirmed"] = True
    question["edit_reason"] = req.edit_reason
    question["routing"] = "AUTO_ACCEPT"
    question["regrade_required"] = False

    # Recalculate totals and flagged status
    total_score = _validated_total_score(data, questions)
    data["questions"] = questions
    data["teacher_approved"] = False
    data["total_score"] = round(total_score, 2)
    _refresh_validation_status(data)
    flagged_count = data["flagged_count"]
    _write_review_result(db, doc_ref, req.exam_id, req.hall_ticket, data)

    # Log review correction
    log_entry = {
        "exam_id": req.exam_id,
        "hall_ticket": req.hall_ticket,
        "question_number": req.question_number,
        "sub_part": req.sub_part.strip().upper(),
        "action": "EDIT",
        "old_score": old_score,
        "gemini_score": gemini_score,
        "new_score": req.new_score,
        "old_text": old_text,
        "new_text": req.corrected_text,
        "flag_reason": question.get("flag_reason", ""),
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


# =========================================================
# POST /review/confirm-blanks, /review/approve, /review/approve-unflagged
# =========================================================

def _log(db, exam_id: str, entry: dict) -> None:
    db.collection("exams").document(exam_id).collection("review_logs").add({
        "exam_id": exam_id,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        **entry,
    })


def _load_result(db, exam_id: str, hall_ticket: str) -> tuple:
    doc_ref = _result_ref(db, exam_id, hall_ticket)
    doc = doc_ref.get()
    if not doc.exists:
        raise HTTPException(404, f"Result for {hall_ticket} not found in exam {exam_id}.")
    return doc_ref, doc.to_dict() or {}


@router.post("/confirm-blanks")
async def confirm_blanks(req: ReviewApproveRequest):
    """The teacher has checked the pages: every answer flagged MISSING really is blank, so 0."""
    db = get_firestore()
    if db is None:
        raise HTTPException(status_code=503, detail="Firestore is unavailable.")
    doc_ref, data = _load_result(db, req.exam_id, req.hall_ticket)
    changed = []
    for question in data.get("questions", []):
        if question.get("routing") == "MANUAL" and question.get("flag_reason") == "MISSING":
            question.update(
                final_score=0.0, routing="AUTO_ACCEPT", teacher_confirmed=True, teacher_override=True,
                edit_reason="Teacher confirmed blank",
            )
            changed.append(f"{question.get('question_number')}{question.get('sub_part') or ''}")
    data["teacher_approved"] = False
    data["total_score"] = round(_validated_total_score(data, data.get("questions", [])), 2)
    _refresh_validation_status(data)
    _write_review_result(db, doc_ref, req.exam_id, req.hall_ticket, data)
    _log(db, req.exam_id, {"hall_ticket": req.hall_ticket, "action": "CONFIRM_BLANKS", "questions": changed})
    return {"success": True, "confirmed": changed, "flagged_count": data["flagged_count"], "status": data["status"]}


def _approval_blockers(data: dict, unmatched_checked: bool) -> list[str]:
    blockers = []
    if data.get("flagged_count"):
        blockers.append(f"{data['flagged_count']} flagged answers still need a decision.")
    if data.get("unmapped_answers") and not unmatched_checked:
        blockers.append("Check the writing that fit no question.")
    return blockers


def _approve(db, doc_ref, exam_id: str, hall_ticket: str, data: dict, action: str) -> None:
    data["teacher_approved"] = True
    _refresh_validation_status(data)
    _write_review_result(db, doc_ref, exam_id, hall_ticket, data)
    _log(db, exam_id, {
        "hall_ticket": hall_ticket, "action": action, "total_score": data.get("total_score"),
    })


@router.post("/approve")
async def approve_sheet(req: ReviewApproveRequest):
    """Only this makes a sheet final."""
    db = get_firestore()
    if db is None:
        raise HTTPException(status_code=503, detail="Firestore is unavailable.")
    doc_ref, data = _load_result(db, req.exam_id, req.hall_ticket)
    _refresh_validation_status(data)
    blockers = _approval_blockers(data, req.unmatched_checked)
    if blockers:
        raise HTTPException(409, " ".join(blockers))
    _approve(db, doc_ref, req.exam_id, req.hall_ticket, data, "APPROVE")
    return {"success": True, "status": data["status"], "finality_status": data["finality_status"]}


@router.post("/approve-unflagged/{exam_id}")
async def approve_unflagged(exam_id: str):
    """Approve every sheet with nothing left to decide. Unflagged marks can still be wrong."""
    db = get_firestore()
    if db is None:
        raise HTTPException(status_code=503, detail="Firestore is unavailable.")
    approved, skipped = [], []
    for doc in db.collection("exams").document(exam_id).collection("results").stream():
        data = doc.to_dict() or {}
        hall_ticket = str(data.get("hall_ticket", doc.id))
        if data.get("finality_status") == "FINAL":
            continue
        doc_ref = _result_ref(db, exam_id, hall_ticket)
        _refresh_validation_status(data)
        if _approval_blockers(data, False) or data.get("sheet_problems"):
            skipped.append(hall_ticket)
            continue
        _approve(db, doc_ref, exam_id, hall_ticket, data, "APPROVE_UNFLAGGED")
        approved.append(hall_ticket)
    return {"success": True, "approved": approved, "still_need_review": skipped}
