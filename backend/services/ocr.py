"""
AutoGrade — OCR Module

Routes handwritten transcription through the shared Model Router. With the
default policy this uses an eligible local Ollama vision model first and only
uses Gemini when configured fallback/verification rules allow it. OCR scores
remain uncalibrated until compared with teacher-verified ground truth.
"""

from __future__ import annotations

import json
import hashlib
import base64
import io
import logging
import os
import re
import tempfile
import threading
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from config import OLLAMA_SHEET_READER_MODEL, gemini_generate_with_retry
from models.schemas import OCRPageResult, OCRRegion
from services.marks_validator import parse_question_number_and_part
from services.timing import log_timing


_logger = logging.getLogger(__name__)
_OCR_CACHE_VERSION = "handwriting-ocr-v4-page-preserving"
_OCR_CACHE_DIR = Path(tempfile.gettempdir()) / "autograde_ocr_cache"
_OCR_CACHE_TTL_SECONDS = int(os.getenv("AUTOGRADE_CACHE_TTL_SECONDS", "86400"))
_OCR_CACHE_LOCK = threading.Lock()
_REGION_OCR_VERSION = "rapidocr-page-regions-v6"
_REGION_CACHE_DIR = Path(tempfile.gettempdir()) / "autograde_ocr_region_cache"
_REGION_CACHE_LOCK = threading.Lock()
_RAPID_OCR_LOCK = threading.RLock()
_RAPID_OCR_ENGINE = None
_RAPID_OCR_VERSION = "unknown"
_RAPIDOCR_REQUIRED_MODELS = (
    "PP-OCRv6_det_small.onnx",
    "PP-OCRv6_rec_small.onnx",
    "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
)


# =========================================================
# GEMINI OCR RESPONSE SCHEMA
# =========================================================

OCR_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question_number": {"type": "integer"},
                    "sub_part": {"type": "string"},
                    "extracted_text": {"type": "string"},
                    "illegible_regions": {
                        "type": "array",
                        "items": {"type": "string"}
                    }
                },
                "required": [
                    "question_number",
                    "sub_part",
                    "extracted_text",
                    "illegible_regions"
                ]
            }
        }
    },
    "required": ["answers"]
}


# =========================================================
# OCR PROMPT
# =========================================================

OCR_PROMPT = """
You are the OCR module of AutoGrade, an AI-powered exam grading system.

Extract all handwritten text from this answer sheet, separated by
question number.

For regions you cannot read with confidence, mark them as [illegible].

For each question the student has answered, return:
- question_number (integer)
- extracted_text (the full handwritten text, preserving the student's
  wording as closely as possible)
- illegible_regions (list of descriptions for any parts you could
  not confidently read)

Important:
- Do NOT grade the answers or assign marks.
- Do NOT summarise or rephrase the student's writing.
- Copy each word exactly as written. Keep spelling and grammar mistakes. Do NOT
  correct, complete or improve anything.
- Leave out words the student crossed out or struck through.
- For a multiple-choice answer such as "1. B", return only the chosen letter as
  extracted_text. If the choice is overwritten, or more than one letter is given,
  describe that in illegible_regions.
- Ignore the page header (name, ID, date) and page numbers, and any ticks or marks
  that are not part of the student's answer.
- Faint mirror-image writing showing through from the back of the page is not an
  answer. Do not transcribe it.
- Leave sub_part empty unless a sub-part label such as (a) is written on the page.
- Preserve mathematical expressions as accurately as possible.
- If a question number is ambiguous, use 0 and describe the mapping uncertainty
  in illegible_regions. Never assign an answer to a question by guesswork.

Return the required JSON only.
"""


# =========================================================
# EXTRACT TEXT FROM IMAGES
# =========================================================

def extract_text(
    image_parts: list,
    *,
    source_hash: str = "",
    job_id: str = "",
    on_stage=None,
    page_regions: list[OCRPageResult] | None = None,
    page_readers: list[dict] | None = None,
) -> tuple[dict[tuple[int, str], dict], float]:
    """
    Run OCR on answer-sheet images through the local-first Model Router.

    Parameters
    ----------
    image_parts : list
        List of image content parts (bytes, Part objects, or
        uploaded file references) representing pages of one answer sheet.
        Each page is processed independently to preserve page provenance.

    Returns
    -------
    results : dict[tuple[int, str], dict]
        Mapping of (question_number, sub_part) to extracted answer data.
    c_ocr : float
        Calibrated OCR confidence; currently zero because verified handwriting
        ground truth is not yet available.

    page_readers : list | None
        If given, filled with one record per page saying which model read it.
    """
    readers: list[dict] = []

    cache_path = _ocr_cache_path(source_hash) if source_hash else None
    if cache_path is not None:
        cached = _load_ocr_cache(cache_path)
        if cached is not None:
            _logger.info(
                "evaluation_cache_hit %s",
                json.dumps({"kind": "ocr", "job_id": job_id}, sort_keys=True),
            )
            if page_readers is not None:
                page_readers.extend(_load_cached_readers(cache_path))
            return cached

    if on_stage is not None:
        on_stage("reading_handwriting", "Transcribing the answer sheet")
    if on_stage is not None:
        on_stage("identifying_answers", "Mapping OCR text to question numbers and sub-parts")
    results: dict[tuple[int, str], dict] = {}
    total_illegible = 0
    previous_page_tail: tuple[int, str] | None = None
    started_at = time.perf_counter()

    for page_number, image_part in enumerate(image_parts, start=1):
        image_bytes = _page_image_bytes(image_part)
        if image_bytes is None:
            image_bytes = (
                bytes(image_part) if isinstance(image_part, (bytes, bytearray))
                else str(image_part).encode("utf-8", errors="replace")
            )
        page_hash = hashlib.sha256(image_bytes).hexdigest()
        prompt = _page_ocr_prompt(page_number, len(image_parts), previous_page_tail)
        page_started = time.perf_counter()
        reader = {"page": page_number, "model": OLLAMA_SHEET_READER_MODEL, "status": "READ"}
        readers.append(reader)
        if _is_blank_or_show_through(image_bytes):
            # Models invent text from these pages. Any answer that goes missing still flags.
            reader.update(model="", status="BLANK")
            print(f"[Sheet] page={page_number}/{len(image_parts)} skipped: no dark ink")
            continue
        try:
            # A timeout or missing model is not an unreadable page: let it fail loudly.
            response = gemini_generate_with_retry(
                contents=[prompt, image_part],
                config={
                    "response_mime_type": "application/json",
                    "response_json_schema": OCR_RESPONSE_SCHEMA,
                    "temperature": 0.1,
                },
                local_model=OLLAMA_SHEET_READER_MODEL,
            )
        finally:
            log_timing(
                "handwriting_ocr_inference",
                page_started,
                job_id=job_id,
                page_number=page_number,
                page_count=len(image_parts),
            )
        reader["model"] = getattr(response, "model_used", "") or OLLAMA_SHEET_READER_MODEL
        try:
            answers = _page_answers(response, page_number)
        except (RuntimeError, ValueError) as error:
            reader.update(status="UNREADABLE", error=str(error))
            if previous_page_tail in results:
                results[previous_page_tail]["illegible_regions"].append(
                    f"Page {page_number} could not be read, so this answer may continue there."
                )
                total_illegible += 1
            continue
        finally:
            print(
                f"[Sheet] page={page_number}/{len(image_parts)} read_by={reader['model']} "
                f"status={reader['status']}"
            )

        page_tail = None
        page_reference = _page_source_reference(page_number, page_hash, page_regions)
        for item_index, item in enumerate(answers, start=1):
            if not isinstance(item, dict):
                continue
            raw_question = item.get("question_number")
            if raw_question is None or not str(raw_question).strip():
                q_num, parsed_part = 0, ""
            else:
                q_num, parsed_part = parse_question_number_and_part(raw_question, default_idx=0)
            sub_part = _clean_sub_part(item.get("sub_part", "") or parsed_part)
            raw_regions = item.get("illegible_regions", [])
            if not isinstance(raw_regions, list):
                raw_regions = [str(raw_regions)] if raw_regions else []
            regions = [str(region) for region in raw_regions]
            total_illegible += len(regions)
            extracted_text = str(item.get("extracted_text", "") or "")

            mapping_status = "MAPPED" if q_num > 0 else "UNRESOLVED"
            candidate_question_number = None
            if q_num <= 0 and previous_page_tail:
                candidate_question_number = previous_page_tail[0]
                mapping_status = "AMBIGUOUS_PAGE_CONTINUATION"
                regions.append(
                    f"Unnumbered content may continue Q{previous_page_tail[0]}{previous_page_tail[1]}; "
                    "question mapping requires teacher validation."
                )
            # Scanned page order is not answer order and students answer out of turn, so a
            # lower number proves nothing. A misread number shows up as a duplicate below.

            tail_before_this_item = page_tail
            if q_num > 0 and mapping_status == "MAPPED":
                page_tail = (q_num, sub_part)

            answer_data = {
                "extracted_text": extracted_text,
                "illegible_regions": regions,
                "detected_question_number": q_num,
                "sub_part": sub_part,
                "mapping_status": mapping_status,
                "candidate_question_number": candidate_question_number,
                "page_references": [page_reference],
                "page_fragments": [{
                    "page_number": page_number,
                    "image_sha256": page_hash,
                    "extracted_text": extracted_text,
                    "illegible_regions": list(regions),
                    "mapping_status": mapping_status,
                }],
            }

            key = (q_num, sub_part)
            if q_num == 0:
                key = (0, f"{sub_part or 'UNSPECIFIED'}@P{page_number}:{item_index}")
            elif key in results and key == tail_before_this_item and mapping_status == "MAPPED" \
                    and results[key]["mapping_status"] == "MAPPED":
                # The reader split one answer into consecutive pieces on the same page.
                kept = results[key]
                kept["extracted_text"] = f"{kept['extracted_text']}\n{extracted_text}".strip()
                kept["illegible_regions"].extend(regions)
                kept["page_fragments"][-1]["extracted_text"] = kept["extracted_text"]
                continue
            elif key in results:
                results[key]["mapping_status"] = "AMBIGUOUS_DUPLICATE"
                results[key]["illegible_regions"].append(
                    f"Q{q_num}{sub_part} was independently detected on multiple pages; fragments were kept separate for teacher review."
                )
                answer_data["mapping_status"] = "AMBIGUOUS_DUPLICATE"
                answer_data["candidate_question_number"] = (
                    answer_data.get("candidate_question_number") or q_num
                )
                answer_data["illegible_regions"].append(
                    f"Duplicate Q{q_num}{sub_part} detection; this fragment was not merged with another page."
                )
                key = (0, f"DUPLICATE_Q{q_num}{sub_part}@P{page_number}:{item_index}")
            results[key] = answer_data

        if page_tail:
            previous_page_tail = page_tail

    if page_readers is not None:
        page_readers.extend(readers)
    failed = [reader for reader in readers if reader["status"] == "UNREADABLE"]
    if failed and not any(reader["status"] == "READ" for reader in readers):
        raise RuntimeError(
            f"None of the {len(readers)} answer-sheet pages could be read: {failed[0]['error']}"
        )

    c_ocr = _compute_c_ocr(None, total_illegible, len(results))
    log_timing(
        "question_answer_extraction",
        started_at,
        job_id=job_id,
        answer_count=len(results),
        illegible_region_count=total_illegible,
    )

    print(f"[OCR] Extracted {len(results)} answers, c_ocr={c_ocr:.4f}")

    # A partly read sheet is never cached, so a retry can read the missing page.
    if cache_path is not None and not failed:
        _store_ocr_cache(cache_path, results, c_ocr, readers)
    return results, c_ocr


_SUB_PART_RE = re.compile(r"^(?:[A-Z]|[IVX]{1,4}|\d)$")
# Share of dark pixels inside the margins, ignoring ruled lines. Set from the tuning
# sheets: show-through pages reach 0.082%, the sparsest real page 0.163%.
_MIN_DARK_INK_SHARE = 0.0012


def _clean_sub_part(value) -> str:
    """Models sometimes put a label or the answer itself here. Keep real sub-parts only."""
    text = str(value or "").strip().strip("().").upper()
    return text if _SUB_PART_RE.match(text) else ""


def _is_blank_or_show_through(image_bytes: bytes) -> bool:
    import numpy as np
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            gray = np.asarray(image.convert("L"))
    except (UnidentifiedImageError, OSError, ValueError):
        return False
    dark = gray < 110
    # Ruled lines can sit anywhere on these sheets, so remove them rather than cropping.
    keep = dark.copy()
    for row in np.flatnonzero(dark.mean(axis=1) > 0.3):
        keep[max(0, row - 4):row + 5, :] = False
    for column in np.flatnonzero(dark.mean(axis=0) > 0.3):
        keep[:, max(0, column - 4):column + 5] = False
    height, width = gray.shape[:2]
    inner = keep[int(height * 0.05):int(height * 0.97), int(width * 0.06):int(width * 0.97)]
    return inner.size > 0 and float(inner.mean()) < _MIN_DARK_INK_SHARE


def _page_answers(response, page_number: int) -> list:
    if not getattr(response, "text", ""):
        raise RuntimeError(f"OCR returned empty output for answer-sheet page {page_number}.")
    try:
        data = json.loads(response.text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"OCR returned invalid JSON for answer-sheet page {page_number}.") from exc
    answers = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(answers, list):
        raise ValueError(f"OCR response for answer-sheet page {page_number} has no answers list.")
    return answers


def _ocr_cache_path(source_hash: str) -> Path:
    from services.router import model_router

    identity = {
        "source": source_hash,
        "version": _OCR_CACHE_VERSION,
        "prompt": hashlib.sha256(OCR_PROMPT.encode()).hexdigest(),
        "routing_policy": model_router.routing_policy_fingerprint(),
        "sheet_reader": OLLAMA_SHEET_READER_MODEL,
    }
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return _OCR_CACHE_DIR / f"{key}.json"


def _load_ocr_cache(path: Path) -> tuple[dict[tuple[int, str], dict], float] | None:
    with _OCR_CACHE_LOCK:
        try:
            if not path.is_file():
                return None
            if time.time() - path.stat().st_mtime > _OCR_CACHE_TTL_SECONDS:
                path.unlink(missing_ok=True)
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            answers = {}
            for item in data["answers"]:
                key = (int(item["question_number"]), str(item.get("sub_part", "")))
                answers[key] = {
                    field: value for field, value in item.items()
                    if field not in {"question_number", "sub_part"}
                }
                answers[key].setdefault("sub_part", key[1])
            return answers, float(data["c_ocr"])
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            _logger.warning("Ignoring invalid OCR cache entry %s", path.name)
            return None


def _load_cached_readers(path: Path) -> list[dict]:
    with _OCR_CACHE_LOCK:
        try:
            return list(json.loads(path.read_text(encoding="utf-8")).get("page_readers", []))
        except (OSError, ValueError, TypeError):
            return []


def _store_ocr_cache(
    path: Path,
    results: dict[tuple[int, str], dict],
    confidence: float,
    readers: list[dict] | None = None,
) -> None:
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = {
            "answers": [
                {
                    "question_number": question_number,
                    "sub_part": sub_part,
                    **answer,
                }
                for (question_number, sub_part), answer in results.items()
            ],
            "c_ocr": confidence,
            "page_readers": readers or [],
        }
        with _OCR_CACHE_LOCK:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f"{path.stem}.",
                suffix=".tmp",
                delete=False,
            ) as cache_file:
                os.chmod(cache_file.name, 0o600)
                json.dump(payload, cache_file)
                temporary_path = Path(cache_file.name)
            temporary_path.replace(path)
    except OSError:
        _logger.exception("Could not persist OCR cache entry")


def _page_ocr_prompt(
    page_number: int,
    page_count: int,
    previous_page_tail: tuple[int, str] | None,
) -> str:
    if previous_page_tail:
        context = (
            f"The previous page's last clearly numbered answer was Q{previous_page_tail[0]}"
            f"{previous_page_tail[1]}. This is context only. If this page has unnumbered "
            "continuation writing, do not assign it to that question; use question_number 0 "
            "and explain the possible continuation in illegible_regions."
        )
    else:
        context = "There is no prior page context."
    return (
        f"{OCR_PROMPT}\n\nProcess source page {page_number} of {page_count} only. "
        "Do not infer a question number from page order or from another page. "
        f"{context} Keep every answer found on this page in the JSON output."
    )


def _page_source_reference(
    page_number: int,
    image_sha256: str,
    page_regions: list[OCRPageResult] | None,
) -> dict:
    page = next(
        (item for item in (page_regions or []) if int(getattr(item, "page_number", 0)) == page_number),
        None,
    )
    regions = list(getattr(page, "regions", []) or []) if page is not None else []
    return {
        "page_number": page_number,
        "image_sha256": image_sha256,
        # These IDs point to reviewable OCR boxes on the page, not to exact
        # answer-aligned boxes; RapidOCR's line segmentation is independent.
        "source_region_ids": [
            str(getattr(region, "region_id", ""))
            for region in regions
            if getattr(region, "region_id", "")
        ],
        "source_region_scope": "page_candidates_not_answer_aligned",
    }


# =========================================================
# LOGPROB-BASED OCR CONFIDENCE
# =========================================================

def _compute_c_ocr(
    response,
    illegible_count: int,
    total_questions: int,
) -> float:
    """Return zero until handwriting confidence has been calibrated.

    Provider token probabilities and detector scores are useful diagnostics,
    but they are not calibrated estimates of transcription correctness. The
    pipeline therefore treats OCR confidence as unavailable and routes the
    resulting grade for review instead of manufacturing a numeric estimate.
    """
    del response, illegible_count, total_questions
    return 0.0


def extract_page_regions(
    image_parts: list,
    *,
    source_hash: str = "",
    job_id: str = "",
    on_stage=None,
) -> list[OCRPageResult]:
    """Run bounded, page-wise RapidOCR and retain original normalized boxes.

    Raw images are preserved. No thresholding, contrast change, or deskew is
    silently applied: the existing sample benchmark showed those variants can
    trade CER against WER. Raw engine scores are never converted to confidence
    classes until verified handwriting data supports calibration.
    """
    if not image_parts:
        return []

    pages: list[OCRPageResult] = []
    engine = None
    engine_initialized = False
    engine_error = ""
    started_at = time.perf_counter()
    for page_number, image_part in enumerate(image_parts, start=1):
        if on_stage is not None:
            on_stage(
                "detecting_text_regions",
                f"Inspecting page {page_number} of {len(image_parts)}",
            )
        image_bytes = _page_image_bytes(image_part)
        if image_bytes is None:
            pages.append(OCRPageResult(
                page_number=page_number,
                status="UNAVAILABLE",
                error="The page image could not be decoded for local region OCR.",
            ))
            continue

        image_sha256 = hashlib.sha256(image_bytes).hexdigest()
        cache_path = _region_cache_path(source_hash, page_number, image_sha256)
        if cache_path is not None:
            cached = _load_region_cache(cache_path)
            if cached is not None:
                pages.append(cached)
                continue

        width, height, quality_flags, blank_candidate = _inspect_page_image(image_bytes)
        if blank_candidate:
            page = OCRPageResult(
                page_number=page_number,
                width=width,
                height=height,
                image_sha256=image_sha256,
                status="BLANK_CANDIDATE",
                blank_candidate=True,
                quality_flags=quality_flags,
            )
        else:
            if not engine_initialized:
                engine_initialized = True
                try:
                    engine = _get_rapidocr_engine()
                except Exception as exc:
                    engine_error = (
                        f"RapidOCR unavailable ({type(exc).__name__}); "
                        "local page-region OCR could not initialize."
                    )
                    _logger.exception("Could not initialize RapidOCR page-region extraction")
            if engine is None:
                page = OCRPageResult(
                    page_number=page_number,
                    width=width,
                    height=height,
                    image_sha256=image_sha256,
                    status="UNAVAILABLE",
                    quality_flags=quality_flags,
                    error=engine_error,
                )
            else:
                page = _recognize_page_regions(
                    engine,
                    image_bytes,
                    page_number=page_number,
                    width=width,
                    height=height,
                    image_sha256=image_sha256,
                    quality_flags=quality_flags,
                    source_hash=source_hash,
                )

        if cache_path is not None and page.status != "UNAVAILABLE":
            _store_region_cache(cache_path, page)
        pages.append(page)

    log_timing(
        "page_region_ocr",
        started_at,
        job_id=job_id,
        page_count=len(pages),
        region_count=sum(len(page.regions) for page in pages),
        unavailable_page_count=sum(page.status == "UNAVAILABLE" for page in pages),
    )
    return pages


def _recognize_page_regions(
    engine,
    image_bytes: bytes,
    *,
    page_number: int,
    width: int,
    height: int,
    image_sha256: str,
    quality_flags: list[str],
    source_hash: str,
) -> OCRPageResult:
    try:
        with _RAPID_OCR_LOCK:
            output = engine(image_bytes)
        raw_texts = getattr(output, "txts", None)
        raw_scores = getattr(output, "scores", None)
        raw_boxes = getattr(output, "boxes", None)
        texts = list(raw_texts) if raw_texts is not None else []
        scores = list(raw_scores) if raw_scores is not None else []
        boxes = list(raw_boxes) if raw_boxes is not None else []
        regions: list[OCRRegion] = []
        for index, text in enumerate(texts):
            text = str(text or "").strip()
            if not text:
                continue
            box = boxes[index] if index < len(boxes) else []
            try:
                coords = [float(value) for point in box for value in point]
                x_values, y_values = coords[0::2], coords[1::2]
                x0 = max(0.0, min(x_values))
                y0 = max(0.0, min(y_values))
                x1 = min(float(width), max(x_values))
                y1 = min(float(height), max(y_values))
                bbox = [
                    round(x0 / max(width, 1), 6),
                    round(y0 / max(height, 1), 6),
                    round(x1 / max(width, 1), 6),
                    round(y1 / max(height, 1), 6),
                ]
            except (TypeError, ValueError, IndexError):
                bbox = []
            score = None
            if index < len(scores):
                try:
                    candidate_score = float(scores[index])
                    if candidate_score == candidate_score:
                        score = max(0.0, min(1.0, candidate_score))
                except (TypeError, ValueError):
                    pass
            uncertainty_class = "UNCALIBRATED"
            candidate_match = re.fullmatch(
                r"\s*(?:q(?:uestion)?\s*)?(\d{1,2})\s*[.):\-]?\s*",
                text,
                re.IGNORECASE,
            )
            question_candidate = int(candidate_match.group(1)) if candidate_match else None
            # Answer numbering is normally in the left answer margin. This
            # excludes common false detections from the cover header, logo,
            # marks, and footer while retaining the text region itself.
            if (
                question_candidate is not None
                and (len(bbox) != 4 or bbox[0] > 0.15 or bbox[1] < 0.12 or bbox[1] > 0.92)
            ):
                question_candidate = None
            region_seed = f"{source_hash}:{page_number}:{image_sha256}:{bbox}:{text}"
            reason = (
                "RapidOCR raw score is low, but it is not calibrated against manually "
                "verified handwriting; teacher validation is required."
                if score is not None and score < 0.70
                else "RapidOCR raw score is not calibrated against manually verified "
                "handwriting; teacher validation is required."
            )
            regions.append(OCRRegion(
                region_id=hashlib.sha256(region_seed.encode()).hexdigest()[:20],
                page_number=page_number,
                bbox=bbox,
                extracted_text=text,
                question_number_candidate=question_candidate,
                question_mapping_status=(
                    "UNVERIFIED_CANDIDATE" if question_candidate is not None else "UNRESOLVED"
                ),
                engine="RapidOCR",
                model_version=_RAPID_OCR_VERSION,
                engine_score=score,
                uncertainty_class=uncertainty_class,
                uncertainty_reason=reason,
            ))
        return OCRPageResult(
            page_number=page_number,
            width=width,
            height=height,
            image_sha256=image_sha256,
            status="PROCESSED" if regions else "UNREADABLE",
            quality_flags=quality_flags,
            regions=regions,
            error="" if regions else "RapidOCR returned no text regions.",
        )
    except Exception as exc:
        _logger.exception("RapidOCR failed on page %s", page_number)
        return OCRPageResult(
            page_number=page_number,
            width=width,
            height=height,
            image_sha256=image_sha256,
            status="UNAVAILABLE",
            quality_flags=quality_flags,
            error=f"RapidOCR failed ({type(exc).__name__}); teacher validation is required.",
        )


def _get_rapidocr_engine():
    global _RAPID_OCR_ENGINE, _RAPID_OCR_VERSION
    with _RAPID_OCR_LOCK:
        if _RAPID_OCR_ENGINE is None:
            from rapidocr import RapidOCR
            model_root = resolve_rapidocr_model_root()
            _RAPID_OCR_ENGINE = RapidOCR(
                params={"Global.model_root_dir": str(model_root)}
            )
            try:
                _RAPID_OCR_VERSION = version("rapidocr")
            except PackageNotFoundError:
                _RAPID_OCR_VERSION = "unknown"
        return _RAPID_OCR_ENGINE


def resolve_rapidocr_model_root(
    configured_root: str | Path | None = None,
    *,
    bundled_root: str | Path | None = None,
    cache_root: str | Path | None = None,
) -> Path:
    """Select only a directory already containing the configured ONNX weights.

    RapidOCR 3.9.2 bundles the currently configured PP-OCRv6 detector and
    recognizer plus PP-OCRv4 classifier under ``rapidocr/models``. The old
    application default overrode that valid package path with an empty temp
    directory, which made RapidOCR attempt its download resolver.
    """
    explicit_root = configured_root or os.getenv("AUTOGRADE_RAPIDOCR_MODEL_DIR")
    if bundled_root is None:
        import rapidocr

        bundled_root = Path(rapidocr.__file__).resolve().parent / "models"
    candidates: list[Path] = []
    if explicit_root:
        candidates.append(Path(explicit_root).expanduser())
    candidates.append(Path(bundled_root))
    if cache_root is not None:
        candidates.append(Path(cache_root))
    elif not explicit_root:
        candidates.append(Path(tempfile.gettempdir()) / "autograde_rapidocr_models")

    seen: set[str] = set()
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if str(resolved) in seen:
            continue
        seen.add(str(resolved))
        if all((resolved / filename).is_file() for filename in _RAPIDOCR_REQUIRED_MODELS):
            return resolved
    required = ", ".join(_RAPIDOCR_REQUIRED_MODELS)
    checked = ", ".join(seen) or "no model directories"
    raise FileNotFoundError(
        "RapidOCR local model weights are unavailable; refusing to download. "
        f"Expected {required} in one model directory. Checked: {checked}."
    )


def _page_image_bytes(image_part) -> bytes | None:
    if isinstance(image_part, bytes):
        return image_part
    if isinstance(image_part, bytearray):
        return bytes(image_part)
    if isinstance(image_part, str):
        value = image_part.strip()
        if value.startswith("data:") and "," in value:
            value = value.split(",", 1)[1]
        try:
            return base64.b64decode(value, validate=True)
        except (ValueError, base64.binascii.Error):
            return None
    if hasattr(image_part, "save"):
        buffer = io.BytesIO()
        try:
            image_part.save(buffer, format="PNG")
            return buffer.getvalue()
        except Exception:
            return None
    return None


def _inspect_page_image(image_bytes: bytes) -> tuple[int, int, list[str], bool]:
    from PIL import Image
    import numpy as np

    with Image.open(io.BytesIO(image_bytes)) as image:
        gray = np.asarray(image.convert("L"))
    height, width = gray.shape[:2]
    mean = float(gray.mean()) if gray.size else 0.0
    contrast = float(gray.std()) if gray.size else 0.0
    ink_ratio = float((gray < 245).mean()) if gray.size else 1.0
    quality_flags = []
    if max(width, height) < 1000 or min(width, height) < 600:
        quality_flags.append("LOW_RESOLUTION")
    if contrast < 20:
        quality_flags.append("LOW_CONTRAST")
    blank_candidate = mean > 252 and ink_ratio < 0.0002
    if blank_candidate:
        quality_flags.append("POSSIBLY_BLANK")
    return width, height, quality_flags, blank_candidate


def _region_cache_path(source_hash: str, page_number: int, image_sha256: str) -> Path | None:
    if not source_hash:
        return None
    identity = {
        "source": source_hash,
        "page_number": page_number,
        "image": image_sha256,
        "version": _REGION_OCR_VERSION,
        "rapidocr": _RAPID_OCR_VERSION,
    }
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return _REGION_CACHE_DIR / f"{key}.json"


def _load_region_cache(path: Path) -> OCRPageResult | None:
    with _REGION_CACHE_LOCK:
        try:
            if not path.is_file():
                return None
            if time.time() - path.stat().st_mtime > _OCR_CACHE_TTL_SECONDS:
                path.unlink(missing_ok=True)
                return None
            return OCRPageResult.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            _logger.warning("Ignoring invalid OCR region cache entry %s", path.name)
            return None


def _store_region_cache(path: Path, page: OCRPageResult) -> None:
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with _REGION_CACHE_LOCK:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f"{path.stem}.",
                suffix=".tmp",
                delete=False,
            ) as cache_file:
                os.chmod(cache_file.name, 0o600)
                json.dump(page.model_dump(mode="json"), cache_file)
                temporary_path = Path(cache_file.name)
            temporary_path.replace(path)
    except OSError:
        _logger.exception("Could not persist OCR region cache entry")


# =========================================================
# STUDENT METADATA EXTRACTION
# =========================================================

_META_SCHEMA = {
    "type": "object",
    "properties": {
        "hall_ticket": {"type": "string"},
        "student_name": {"type": "string"},
    },
    "required": ["hall_ticket", "student_name"],
}

_META_PROMPT = """
You are reading the cover/header section of a student's handwritten exam answer sheet.

Extract ONLY:
1. hall_ticket — the student's roll number, hall ticket number, register number, or exam ID
   (usually a number like "5069140" or alphanumeric like "21CS001")
2. student_name — the student's full name as written on the sheet

Rules:
- If you cannot find the hall ticket, return an empty string "".
- If you cannot find the name, return an empty string "".
- Do NOT guess or invent values. Only return what is clearly written.
- Return JSON only.
"""


def extract_student_meta(image_parts: list) -> dict:
    """
    Use the shared local-first vision router to extract student name and hall
    ticket from the answer-sheet cover page.

    Parameters
    ----------
    image_parts : list
        Image content parts (same format as extract_text).

    Returns
    -------
    dict with keys:
        hall_ticket : str  (empty string if not found)
        student_name : str (empty string if not found)
    """
    try:
        response = gemini_generate_with_retry(
            contents=[_META_PROMPT] + image_parts,
            config={
                "response_mime_type": "application/json",
                "response_json_schema": _META_SCHEMA,
                "temperature": 0.0,
            }
        )

        if not response.text:
            return {"hall_ticket": "", "student_name": ""}

        data = json.loads(response.text)
        hall_ticket = str(data.get("hall_ticket", "")).strip()
        student_name = str(data.get("student_name", "")).strip()

        print(f"[OCR] Student meta → hall_ticket={hall_ticket!r}, name={student_name!r}")
        return {"hall_ticket": hall_ticket, "student_name": student_name}

    except Exception as exc:
        print(f"[OCR] Student meta extraction failed: {exc}")
        return {"hall_ticket": "", "student_name": ""}
