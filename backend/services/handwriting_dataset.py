"""Opt-in storage for teacher-validated handwriting examples.

Examples remain disabled unless an operator explicitly enables retention,
provides a private pseudonymization salt, and confirms a private access policy.
The record references the existing private answer-sheet source plus a page box;
it does not duplicate or make the student's document public.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any


_logger = logging.getLogger(__name__)
_COLLECTION = "handwriting_training_candidates"


def save_teacher_validated_candidate(
    db: Any,
    *,
    exam_id: str,
    hall_ticket: str,
    source_file_reference: str,
    page: dict,
    region: dict,
    action: str,
    corrected_text: str | None,
    question_number: int | None,
    sub_part: str = "",
) -> dict[str, str]:
    """Save an idempotent, teacher-validated candidate without student IDs.

    ``ACCEPT`` uses the original OCR text as the verified transcript;
    ``CORRECT`` stores the teacher's correction separately. Unreadable regions
    are never added as positive training labels.
    """
    if os.getenv("HANDWRITING_DATASET_ENABLED", "false").strip().lower() != "true":
        return {"status": "DISABLED", "reason": "dataset_disabled"}
    if action not in {"ACCEPT", "CORRECT"}:
        return {"status": "SKIPPED", "reason": "not_readable_validation"}
    if os.getenv("HANDWRITING_DATASET_ACCESS_POLICY_CONFIRMED", "false").strip().lower() != "true":
        return {"status": "SKIPPED", "reason": "private_access_policy_not_confirmed"}
    if os.getenv("HANDWRITING_DATASET_TTL_ENFORCED", "false").strip().lower() != "true":
        return {"status": "SKIPPED", "reason": "ttl_enforcement_not_confirmed"}

    try:
        retention_days = int(os.getenv("HANDWRITING_DATASET_RETENTION_DAYS", "0"))
    except (TypeError, ValueError):
        retention_days = 0
    salt = os.getenv("HANDWRITING_DATASET_SALT", "")
    if retention_days < 1:
        return {"status": "SKIPPED", "reason": "retention_not_configured"}
    if len(salt.encode("utf-8")) < 32:
        return {"status": "SKIPPED", "reason": "pseudonym_salt_not_configured"}
    source_reference = str(source_file_reference or "").strip()
    bbox = region.get("bbox") or []
    original_text = str(region.get("extracted_text") or "")
    validated_text = original_text if action == "ACCEPT" else str(corrected_text or "")
    if not source_reference or len(bbox) != 4 or not validated_text.strip():
        return {"status": "SKIPPED", "reason": "required_evidence_missing"}

    normalized_bbox = [round(float(value), 6) for value in bbox]
    page_hash = str(page.get("image_sha256") or "")
    if not page_hash:
        return {"status": "SKIPPED", "reason": "page_hash_missing"}

    writer_hash = hmac.new(
        salt.encode("utf-8"),
        f"writer:{hall_ticket}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    exam_hash = hmac.new(
        salt.encode("utf-8"),
        f"exam:{exam_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:24]
    dedupe_material = "\x1f".join((
        page_hash,
        str(page.get("page_number", "")),
        ",".join(str(value) for value in normalized_bbox),
        original_text,
        validated_text,
        str(region.get("engine") or ""),
        str(region.get("model_version") or ""),
    ))
    candidate_id = hashlib.sha256(dedupe_material.encode("utf-8")).hexdigest()
    created_at = datetime.now(timezone.utc)
    expires_at = created_at + timedelta(days=retention_days)
    dataset_version = os.getenv("HANDWRITING_DATASET_VERSION", "v1").strip() or "v1"

    payload = {
        "candidate_id": candidate_id,
        "dataset_version": dataset_version,
        "status": "TEACHER_VALIDATED_CANDIDATE",
        "source_file_reference": source_reference,
        "source_page_sha256": page_hash,
        "page_number": int(page.get("page_number", 0)),
        "region_id": str(region.get("region_id") or ""),
        "bbox": normalized_bbox,
        "original_ocr_transcription": original_text,
        "teacher_corrected_transcription": validated_text,
        "question_number": question_number,
        "sub_part": (sub_part or "").strip().upper(),
        "ocr_engine": str(region.get("engine") or "unknown"),
        "ocr_model_version": str(region.get("model_version") or "unknown"),
        "processing_configuration": {
            "page_region_pipeline_version": os.getenv(
                "AUTOGRADE_REGION_OCR_VERSION", "rapidocr-page-regions-v6"
            ),
            "preprocessing_variant": str(region.get("preprocessing_variant") or "raw_page"),
            "engine_score_is_calibrated": False,
        },
        "language_or_script": str(region.get("language_or_script") or "unknown"),
        "content_type": str(region.get("content_type") or "UNKNOWN"),
        "teacher_validation_action": action,
        "teacher_validated_at": region.get("validated_at") or created_at.isoformat(),
        "writer_pseudonym": writer_hash,
        "exam_pseudonym": exam_hash,
        "writer_split": "evaluation" if int(writer_hash[:8], 16) % 10 == 0 else "training",
        "created_at": created_at,
        "expires_at": expires_at,
        "retention_days": retention_days,
    }

    try:
        db.collection(_COLLECTION).document(candidate_id).create(payload)
        return {"status": "CREATED", "candidate_id": candidate_id}
    except Exception as error:
        # Firestore create() is idempotent at the document ID. Distinguish an
        # existing row without exposing SDK error text or any payload content.
        try:
            existing = db.collection(_COLLECTION).document(candidate_id).get()
            if existing.exists:
                return {"status": "ALREADY_EXISTS", "candidate_id": candidate_id}
        except Exception:
            pass
        _logger.warning(
            "handwriting_candidate_save_failed error_type=%s",
            type(error).__name__,
        )
        return {"status": "SAVE_FAILED", "reason": type(error).__name__}


def purge_expired_candidates(db: Any, *, limit: int = 500) -> int:
    """Delete expired candidate records; original submissions follow their own policy."""
    now = datetime.now(timezone.utc)
    expired = list(
        db.collection(_COLLECTION)
        .where("expires_at", "<=", now)
        .limit(max(1, min(int(limit), 5000)))
        .stream()
    )
    batch = db.batch()
    for snapshot in expired:
        batch.delete(snapshot.reference)
    if expired:
        batch.commit()
    return len(expired)

