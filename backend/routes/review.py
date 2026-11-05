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
    OCRQuestionRegradeRequest,
    OCRRegionReviewRequest,
    QuestionRubric,
    ReviewConfirmRequest,
    ReviewEditRequest,
)
from pipeline import regrade_question_text
from services.db import db_service
from services.handwriting_dataset import save_teacher_validated_candidate
from services.router import model_router


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
    if flagged_count == 0 and not pending_region and not pending_page and not regrade_required:
        data["status"] = "COMPLETE"
        data["finality_status"] = "FINAL"
    else:
        data["status"] = "NEEDS_REVIEW"
        data["finality_status"] = "PENDING_VALIDATION"


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


def _load_region_crops(source_reference: str, ocr_pages: list[dict], region_ids: list[str]) -> list[str]:
    """Load only mapped source regions for visual/math regrading."""
    if not source_reference or not region_ids:
        return []
    bucket = get_storage_bucket()
    if bucket is None:
        raise HTTPException(status_code=503, detail="Firebase Storage is unavailable.")
    try:
        blob = bucket.blob(source_reference)
        if not blob.exists():
            raise HTTPException(status_code=404, detail="Original answer sheet was not found.")
        source_bytes = blob.download_as_bytes()
        from PIL import Image

        regions = {
            region.get("region_id"): (page, region)
            for page in ocr_pages
            for region in page.get("regions", [])
        }
        if any(region_id not in regions for region_id in region_ids):
            raise HTTPException(status_code=409, detail="A mapped OCR evidence region is missing.")

        crops: list[str] = []
        if Path(source_reference).suffix.lower() == ".pdf":
            import fitz

            with fitz.open(stream=source_bytes, filetype="pdf") as document:
                page_images: dict[int, Image.Image] = {}
                for region_id in dict.fromkeys(region_ids):
                    page_record, region = regions[region_id]
                    page_number = int(page_record.get("page_number", 0))
                    if page_number not in page_images:
                        if not 1 <= page_number <= document.page_count:
                            raise HTTPException(status_code=409, detail="Mapped region page is outside the source PDF.")
                        png_bytes = document[page_number - 1].get_pixmap(dpi=180, alpha=False).tobytes("png")
                        page_images[page_number] = Image.open(io.BytesIO(png_bytes)).convert("RGB")
                    crops.append(_crop_region_payload(page_images[page_number], region))
        else:
            with Image.open(io.BytesIO(source_bytes)) as image:
                page_image = image.convert("RGB")
                for region_id in dict.fromkeys(region_ids):
                    page_record, region = regions[region_id]
                    if int(page_record.get("page_number", 0)) != 1:
                        raise HTTPException(status_code=409, detail="Image source has no matching OCR page.")
                    crops.append(_crop_region_payload(page_image, region))
        return crops
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=502, detail="Could not load mapped handwriting evidence.") from error


def _crop_region_payload(page_image, region: dict) -> str:
    bbox = region.get("bbox") or []
    if len(bbox) != 4:
        raise HTTPException(status_code=409, detail="Mapped OCR region has no usable page coordinates.")
    x0, y0, x1, y1 = [max(0.0, min(1.0, float(value))) for value in bbox]
    width, height = page_image.size
    left, right = sorted((int(x0 * width), int(x1 * width)))
    top, bottom = sorted((int(y0 * height), int(y1 * height)))
    box_width, box_height = max(1, right - left), max(1, bottom - top)
    pad_x, pad_y = max(12, int(box_width * 0.05)), max(16, int(box_height * 1.25))
    crop = page_image.crop((
        max(0, left - pad_x),
        max(0, top - pad_y),
        min(width, right + pad_x),
        min(height, bottom + pad_y),
    ))
    output = io.BytesIO()
    crop.save(output, format="JPEG", quality=90)
    return model_router.ollama.prepare_image_payload(output.getvalue())


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


@router.post("/ocr-regrade")
async def regrade_validated_ocr(req: OCRQuestionRegradeRequest):
    """Re-run the existing grading chain on teacher-validated text, without OCR."""
    db = get_firestore()
    if db is None:
        raise HTTPException(status_code=503, detail="Firestore is unavailable.")
    doc_ref = _result_ref(db, req.exam_id, req.hall_ticket)
    doc = doc_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Paper result was not found.")
    data = doc.to_dict() or {}
    question = _find_question(data.get("questions", []), req.question_number, req.sub_part)
    if question is None:
        raise HTTPException(status_code=404, detail="Question was not found in this paper.")
    if not question.get("regrade_required"):
        raise HTTPException(status_code=409, detail="This question has no pending OCR correction to regrade.")
    answer_text = str(question.get("teacher_corrected_text") or "").strip()
    if not answer_text:
        raise HTTPException(status_code=409, detail="Validate readable text for this question before regrading.")

    region_ids = list(question.get("ocr_region_ids") or [])
    all_regions = {
        region.get("region_id"): region
        for page in data.get("ocr_pages", [])
        for region in page.get("regions", [])
    }
    for region_id in region_ids:
        region = all_regions.get(region_id)
        if (
            region is None
            or region.get("validation_status") not in {"ACCEPTED", "CORRECTED"}
            or region.get("question_mapping_status") != "TEACHER_CONFIRMED"
            or region.get("teacher_question_number") != req.question_number
            or str(region.get("teacher_sub_part", "")).strip().upper() != req.sub_part.strip().upper()
        ):
            raise HTTPException(
                status_code=409,
                detail="Every supporting region must be validated and mapped to this question before regrading.",
            )
    mapped_region_data = [all_regions[region_id] for region_id in region_ids]
    if not region_ids:
        raise HTTPException(status_code=409, detail="Map at least one validated answer region before regrading.")

    stored_version = str(data.get("rubric_version", ""))
    if not stored_version:
        raise HTTPException(
            status_code=409,
            detail="This result has no approved rubric version recorded; regrading requires a new evaluation.",
        )
    approved = db_service.get_approved_rubric(req.exam_id)
    if approved and str(approved.get("version", "")) == stored_version:
        rubric_source = approved
    else:
        # Results retain the version that was approved when they were graded.
        # Use that immutable archived rubric if a later version is now active.
        archived_ref = db.collection("rubrics").document(f"{req.exam_id}_v{stored_version}")
        archived_doc = archived_ref.get()
        rubric_source = archived_doc.to_dict() if archived_doc.exists else None
    if (
        not rubric_source
        or str(rubric_source.get("exam_id", req.exam_id)) != req.exam_id
        or str(rubric_source.get("version", "")) != stored_version
        or str(rubric_source.get("approval_status", "APPROVED")).upper() != "APPROVED"
    ):
        raise HTTPException(status_code=409, detail="The approved rubric version for this result is unavailable.")

    rubric_data = next(
        (
            item for item in rubric_source.get("questions", [])
            if item.get("question_number") == req.question_number
            and str(item.get("sub_part", "")).strip().upper() == req.sub_part.strip().upper()
        ),
        None,
    )
    if rubric_data is None:
        raise HTTPException(status_code=409, detail="The question is missing from the approved rubric.")
    rubric = QuestionRubric.model_validate(rubric_data)
    if abs(float(question.get("max_marks", 0.0)) - rubric.max_marks) > 1e-6:
        raise HTTPException(status_code=409, detail="Question maximum differs from the approved rubric.")

    question_type = str(question.get("question_type", rubric.question_type or "THEORY")).upper()
    image_parts = []
    if question_type in {"DIAGRAM", "MIXED", "NUMERICAL"}:
        image_parts = _load_region_crops(
            str(data.get("source_file_reference", "")),
            data.get("ocr_pages", []),
            region_ids,
        )
    if question_type in {"DIAGRAM", "MIXED"} and not image_parts:
        raise HTTPException(
            status_code=409,
            detail="Diagram grading requires original image evidence from the mapped answer regions.",
        )

    try:
        grade = regrade_question_text(
            rubric,
            answer_text,
            question_type=question_type,
            image_parts=image_parts,
            ocr_regions=mapped_region_data,
            illegible_count=len(question.get("illegible_regions", [])),
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    question.update({
        "grading_input_text": answer_text,
        "grader_score": grade["grader_score"],
        "critic_score": grade["critic_score"],
        "final_score": grade["final_score"],
        "criteria": [item.model_dump() for item in grade["criteria"]],
        "c_ocr": grade["c_ocr"],
        "c_agree": grade["c_agree"],
        "c_evid": grade["c_evid"],
        "c_math": grade["c_math"],
        "c_final": grade["c_final"],
        "routing": grade["routing"],
        "routing_reason": grade["routing_reason"],
        "model_used": grade["model_used"],
        "ocr_confidence_class": "UNCALIBRATED",
        "regrade_required": False,
        "teacher_confirmed": False,
    })
    questions = data.get("questions", [])
    total_score = _validated_total_score(data, questions)
    data["questions"] = questions
    data["total_score"] = round(total_score, 2)
    data["avg_confidence"] = round(
        sum(float(item.get("c_final", 0.0)) for item in questions) / max(1, len(questions)),
        4,
    )
    data["updated_at"] = now
    _refresh_validation_status(data)
    _write_review_result(db, doc_ref, req.exam_id, req.hall_ticket, data)
    db.collection("exams").document(req.exam_id).collection("review_logs").add({
        "exam_id": req.exam_id,
        "hall_ticket": req.hall_ticket,
        "question_number": req.question_number,
        "sub_part": req.sub_part.strip().upper(),
        "action": "OCR_REGRADE",
        "rubric_version": stored_version,
        "timestamp": now,
    })
    return {
        "success": True,
        "question_number": req.question_number,
        "sub_part": req.sub_part.strip().upper(),
        "final_score": question["final_score"],
        "max_marks": question["max_marks"],
        "status": data["status"],
        "finality_status": data["finality_status"],
    }


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
    question["teacher_confirmed"] = True
    question["routing"] = "AUTO_ACCEPT"

    data["questions"] = questions
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
    max_m = float(question.get("max_marks", req.new_score))
    if req.new_score > max_m:
        raise HTTPException(
            status_code=400,
            detail=f"Score cannot exceed question maximum marks ({max_m})."
        )
    if req.new_score < 0:
        raise HTTPException(status_code=400, detail="Score cannot be negative.")
    question["final_score"] = float(req.new_score)
    question["teacher_override"] = True
    question["teacher_confirmed"] = True
    question["edit_reason"] = req.edit_reason
    question["routing"] = "AUTO_ACCEPT"
    question["regrade_required"] = False

    # Recalculate totals and flagged status
    total_score = _validated_total_score(data, questions)
    data["questions"] = questions
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
