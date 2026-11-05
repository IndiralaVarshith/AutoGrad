"""
AutoGrade — Rubric Document Fingerprinting and Draft Cache

Provides:
  - SHA-256 fingerprint of the uploaded question paper bytes
  - Generation fingerprint combining doc_fingerprint + config inputs
  - In-memory (and Firestore-backed) cache of rubric drafts
  - Prevents unnecessary re-generation when the same paper is uploaded again
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

# SCHEMA / PROMPT version — bump when rubric schema or prompts change
RUBRIC_SCHEMA_VERSION = "v2"
RUBRIC_PROMPT_VERSION = "v4"


def compute_doc_fingerprint(file_bytes: bytes) -> str:
    """SHA-256 fingerprint of raw file bytes."""
    return hashlib.sha256(file_bytes).hexdigest()


def compute_generation_fingerprint(
    doc_fingerprint: str,
    model_name: str,
    schema_version: str = RUBRIC_SCHEMA_VERSION,
    prompt_version: str = RUBRIC_PROMPT_VERSION,
    ref_fingerprint: str = "",
) -> str:
    """
    Stable fingerprint that combines all generation inputs.
    Identical inputs → identical fingerprint → cache hit.
    """
    payload = json.dumps(
        {
            "doc": doc_fingerprint,
            "model": model_name,
            "schema": schema_version,
            "prompt": prompt_version,
            "ref": ref_fingerprint,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


# -----------------------------------------------------------
# In-process LRU-style draft cache (keyed by generation_fingerprint)
# Cleared on backend restart; Firestore provides cross-restart persistence
# -----------------------------------------------------------

_draft_cache: dict[str, dict[str, Any]] = {}


def get_cached_draft(gen_fingerprint: str) -> dict[str, Any] | None:
    """Return cached rubric draft, or None if not present / expired."""
    entry = _draft_cache.get(gen_fingerprint)
    if not entry:
        return None
    # Expire after 4 hours
    if time.time() - entry.get("cached_at", 0) > 14400:
        del _draft_cache[gen_fingerprint]
        return None
    return entry.get("rubric_data")


def store_draft_cache(gen_fingerprint: str, rubric_data: dict[str, Any]) -> None:
    """Cache a freshly generated rubric draft in process memory."""
    _draft_cache[gen_fingerprint] = {
        "rubric_data": rubric_data,
        "cached_at": time.time(),
    }
    # Bound cache size to 50 entries
    if len(_draft_cache) > 50:
        oldest_key = min(_draft_cache, key=lambda k: _draft_cache[k].get("cached_at", 0))
        del _draft_cache[oldest_key]


def validate_rubric_structure(
    rubric_data: dict[str, Any],
    strict: bool = False,
) -> list[str]:
    """
    Validate a proposed rubric and return a list of warning strings.
    Returns an empty list when the rubric is fully valid.
    Raises ValueError on fatal structural errors when strict=True.
    """
    warnings: list[str] = []

    questions = rubric_data.get("questions", [])
    if not questions:
        if strict:
            raise ValueError("Rubric contains no questions.")
        warnings.append("No questions were extracted from the question paper.")
        return warnings

    seen_ids: set[tuple[Any, str]] = set()
    rubric_total = 0.0

    for idx, q in enumerate(questions):
        q_num = q.get("question_number", idx + 1)

        # Unique question IDs
        # 9A and 9B are different questions; only the same number and part is a duplicate.
        sub_part = str(q.get("sub_part") or "").strip().upper()
        if (q_num, sub_part) in seen_ids:
            warnings.append(f"Duplicate question number: {q_num}{sub_part}.")
        seen_ids.add((q_num, sub_part))

        q_max = float(q.get("max_marks", 0.0))
        rubric_total += q_max

        criteria = q.get("criteria", [])
        if not criteria:
            warnings.append(f"Q{q_num}: No criteria defined.")
            continue

        # Criteria sum vs question max
        c_sum = sum(float(c.get("max_marks", 0.0)) for c in criteria)
        if abs(c_sum - q_max) > 0.05:
            warnings.append(
                f"Q{q_num}: Criteria marks sum ({c_sum:.1f}) ≠ question max_marks ({q_max:.1f})."
            )

        # Each criterion has description
        for c_idx, c in enumerate(criteria):
            if not str(c.get("description", "")).strip():
                warnings.append(f"Q{q_num}, criterion {c_idx + 1}: Empty description.")

    if rubric_total <= 0:
        if strict:
            raise ValueError("Rubric total marks is 0 — no marks detected in question paper.")
        warnings.append("Total rubric marks is 0. Check if marks were detected correctly.")

    return warnings

