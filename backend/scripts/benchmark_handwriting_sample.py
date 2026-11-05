"""Run a local handwritten OCR comparison against the repository's reference.

This is an explicit developer benchmark, not part of the automated test suite.
Use bundled RapidOCR weights or point AUTOGRADE_RAPIDOCR_MODEL_DIR at a complete
local model directory. This script refuses to download weights. The fixture
currently has no teacher sign-off, so CER/WER are diagnostic only.
"""

from __future__ import annotations

import hashlib
import json
import sys
import unicodedata
from pathlib import Path

import fitz
from rapidfuzz.distance import Levenshtein


BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from services.ocr import extract_page_regions, resolve_rapidocr_model_root  # noqa: E402


FIXTURE_PATH = BACKEND_DIR / "tests" / "fixtures" / "handwriting_ground_truth.json"


def _has_preinstalled_rapidocr_models(model_root: Path) -> bool:
    """Require pre-existing detector/recognizer/classifier files; never fetch weights here."""
    try:
        required = (
            "PP-OCRv6_det_small.onnx",
            "PP-OCRv6_rec_small.onnx",
            "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
        )
        return all((model_root / filename).is_file() for filename in required)
    except OSError:
        return False


def _reference_is_teacher_verified(fixture: dict) -> bool:
    """Require explicit teacher status and provenance before qualifying metrics."""
    segments = fixture.get("segments") or []
    return bool(segments) and (
        fixture.get("annotation_status") == "TEACHER_VERIFIED"
        and bool(str(fixture.get("verified_by") or "").strip())
        and bool(str(fixture.get("verified_at") or "").strip())
        and all(
            isinstance(segment.get("verification"), dict)
            and segment["verification"].get("status") == "TEACHER_VERIFIED"
            and bool(str(segment["verification"].get("verified_by") or "").strip())
            and bool(str(segment["verification"].get("verified_at") or "").strip())
            for segment in segments
        )
    )


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text).casefold()
    return " ".join(text.split())


def _select_regions(pages, windows: list[dict]) -> tuple[str, list[dict]]:
    selected: list[tuple[int, float, float, object]] = []
    for window in windows:
        page_number = int(window["page"])
        page = next((item for item in pages if item.page_number == page_number), None)
        if page is None:
            continue
        x0, y0, x1, y1 = window["bbox"]
        for region in page.regions:
            if len(region.bbox) != 4:
                continue
            rx0, ry0, rx1, ry1 = region.bbox
            center_x, center_y = (rx0 + rx1) / 2, (ry0 + ry1) / 2
            if x0 <= center_x <= x1 and y0 <= center_y <= y1:
                selected.append((page_number, ry0, rx0, region))

    selected.sort(key=lambda item: (item[0], item[1], item[2]))
    actual_text = " ".join(item[3].extracted_text for item in selected)
    diagnostics = [
        {
            "page": page_number,
            "region_id": region.region_id,
            "characters": len(region.extracted_text),
            "engine_score": region.engine_score,
            "uncertainty_class": region.uncertainty_class,
        }
        for page_number, _, _, region in selected
    ]
    return actual_text, diagnostics


def _metrics(reference: str, hypothesis: str) -> dict[str, float | int]:
    normalized_reference = _normalize(reference)
    normalized_hypothesis = _normalize(hypothesis)
    reference_words = normalized_reference.split()
    hypothesis_words = normalized_hypothesis.split()
    char_edits = Levenshtein.distance(normalized_reference, normalized_hypothesis)
    word_edits = Levenshtein.distance(reference_words, hypothesis_words)
    return {
        "character_edits": char_edits,
        "reference_characters": len(normalized_reference),
        "CER": round(char_edits / max(1, len(normalized_reference)), 4),
        "word_edits": word_edits,
        "reference_words": len(reference_words),
        "WER": round(word_edits / max(1, len(reference_words)), 4),
    }


def run(pdf_path: Path) -> dict:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    try:
        model_root = resolve_rapidocr_model_root()
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Configured RapidOCR ONNX weights are unavailable. "
            "The benchmark refuses to trigger model downloads."
        ) from exc
    if not _has_preinstalled_rapidocr_models(model_root):
        raise RuntimeError("The resolved RapidOCR model directory is incomplete; no downloads were attempted.")
    source_bytes = pdf_path.read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    page_images: list[bytes] = []
    with fitz.open(stream=source_bytes, filetype="pdf") as document:
        for page in document:
            page_images.append(page.get_pixmap(dpi=fixture["render_dpi"], alpha=False).tobytes("png"))
    pages = extract_page_regions(page_images, source_hash=source_hash)
    if any(page.status == "UNAVAILABLE" for page in pages):
        raise RuntimeError(
            "RapidOCR could not initialize. Set AUTOGRADE_RAPIDOCR_MODEL_DIR "
            "to an existing local model directory."
        )

    reference_verified = _reference_is_teacher_verified(fixture)
    results = []
    for segment in fixture["segments"]:
        hypothesis, regions = _select_regions(pages, segment["windows"])
        results.append({
            "id": segment["id"],
            "question": segment["question"],
            "tags": segment["tags"],
            "ocr_characters": len(hypothesis),
            "reference_characters": len(_normalize(segment["reference_text"])),
            "metrics": _metrics(segment["reference_text"], hypothesis),
            "metric_qualification": (
                "TEACHER_VERIFIED" if reference_verified else "DIAGNOSTIC_ONLY_UNVERIFIED_REFERENCE"
            ),
            "regions": regions,
        })

    manual_review = []
    for example in fixture.get("manual_review_examples", []):
        _, regions = _select_regions(
            pages,
            [{"page": example["page"], "bbox": example["bbox"]}],
        )
        manual_review.append({
            "id": example["id"],
            "question": example["question"],
            "page": example["page"],
            "bbox": example["bbox"],
            "reason": example["reason"],
            "ocr_regions": regions,
        })

    return {
        "dataset_id": fixture["dataset_id"],
        "source_file": str(pdf_path),
        "source_sha256": source_hash,
        "engine": "RapidOCR",
        "annotation_status": fixture.get("annotation_status", "UNVERIFIED_CANDIDATE"),
        "metric_qualification": (
            "TEACHER_VERIFIED" if reference_verified else "DIAGNOSTIC_ONLY_UNVERIFIED_REFERENCE"
        ),
        "page_count": len(pages),
        "region_count": sum(len(page.regions) for page in pages),
        "metric_normalization": "NFC, casefold, whitespace collapse; punctuation retained",
        "segments": results,
        "manual_review_examples": manual_review,
        "limitations": fixture["scope_note"],
    }


if __name__ == "__main__":
    sample_path = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT_ROOT / "test_data" / "student1.pdf"
    print(json.dumps(run(sample_path), ensure_ascii=False, indent=2))
