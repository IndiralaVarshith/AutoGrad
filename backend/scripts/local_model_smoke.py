"""Run bounded, local-only inference checks against installed Ollama models.

Run from the repository root with:
    .venv/bin/python backend/scripts/local_model_smoke.py

The script never calls Gemini and never prints student transcription text.
It checks both configured local models sequentially, then OCRs page 1 of the
existing student sample. Ollama's one-model memory policy is verified after
each inference. No models are downloaded.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(dotenv_path=BACKEND_DIR / ".env")

from services.ocr import OCR_PROMPT, OCR_RESPONSE_SCHEMA, extract_text  # noqa: E402
from services.provider_registry import TaskCategory  # noqa: E402
from services.router import model_router  # noqa: E402


def _loaded_models() -> list[str]:
    registry = model_router.ollama.discover_models(refresh=True)
    if not registry.get("online"):
        raise RuntimeError("Ollama became unavailable during local inference checks.")
    return [model.name for model in registry["models"] if model.loaded]


def _assert_single_model(expected: str) -> list[str]:
    loaded = _loaded_models()
    if len(loaded) > 1 or (loaded and expected not in loaded):
        raise RuntimeError("Ollama did not maintain the one-model-at-a-time policy.")
    return loaded


def main() -> int:
    registry = model_router.ollama.discover_models(refresh=True)
    if not registry.get("online"):
        raise RuntimeError("Ollama is unavailable; no local model was tested.")
    installed = {model.name: model for model in registry["models"]}
    expected = ("qwen3.5:4b", "gemma4:e4b")
    missing = [name for name in expected if name not in installed]
    if missing:
        raise RuntimeError("Expected local models are missing: " + ", ".join(missing))

    for name in expected:
        model = installed[name]
        if model.supports_structured_json is not True:
            raise RuntimeError(f"{name} has no confirmed structured-JSON capability.")
    if installed["gemma4:e4b"].reasoning_suitable is not True:
        raise RuntimeError("Gemma reasoning capability was not reported by Ollama.")

    local_first = model_router.config.local_first
    if not local_first or not model_router.config.is_local():
        raise RuntimeError("Refusing the smoke test because configured provider policy is not local-first Ollama.")
    # The answer-sheet sample must remain on-device even if local OCR fails.
    model_router.config.gemini_enabled = False
    model_router.config.gemini_fallback_enabled = False

    started = time.perf_counter()
    qwen = model_router.generate(
        task_type="json_formatting",
        prompt='Return JSON with boolean field "ready" set to true.',
        format_json=True,
        response_json_schema={
            "type": "object",
            "properties": {"ready": {"type": "boolean"}},
            "required": ["ready"],
        },
        force_model="qwen3.5:4b",
        max_tokens=24,
        timeout=180,
        task_id="local-smoke-qwen",
    )
    qwen_ok = (
        qwen.provider == "ollama"
        and qwen.model_used == "qwen3.5:4b"
        and isinstance(qwen.parsed_json, dict)
        and qwen.parsed_json.get("ready") is True
    )
    qwen_loaded = _assert_single_model(qwen.model_used)

    started_gemma = time.perf_counter()
    qwen_math = model_router.generate(
        task_type="math_reasoning",
        prompt=(
            "Compute the remainder when 2^10 is divided by 7. Return JSON only with "
            "integer field remainder."
        ),
        format_json=True,
        response_json_schema={
            "type": "object",
            "properties": {"remainder": {"type": "integer"}},
            "required": ["remainder"],
        },
        question_type="NUMERICAL",
        requires_reasoning=True,
        force_model="qwen3.5:4b",
        max_tokens=128,
        timeout=300,
        task_id="local-smoke-qwen-math",
    )
    qwen_math_ok = (
        qwen_math.provider == "ollama"
        and qwen_math.model_used == "qwen3.5:4b"
        and isinstance(qwen_math.parsed_json, dict)
        and qwen_math.parsed_json.get("remainder") == 2
    )
    qwen_math_loaded = _assert_single_model(qwen_math.model_used)

    started_gemma = time.perf_counter()
    gemma = model_router.generate(
        task_type="math_reasoning",
        prompt=(
            "Compute the remainder when 2^10 is divided by 7. Return JSON only with "
            "integer field remainder."
        ),
        format_json=True,
        response_json_schema={
            "type": "object",
            "properties": {"remainder": {"type": "integer"}},
            "required": ["remainder"],
        },
        question_type="NUMERICAL",
        requires_reasoning=True,
        force_model="gemma4:e4b",
        max_tokens=128,
        timeout=300,
        task_id="local-smoke-gemma",
    )
    gemma_ok = (
        gemma.provider == "ollama"
        and gemma.model_used == "gemma4:e4b"
        and isinstance(gemma.parsed_json, dict)
        and gemma.parsed_json.get("remainder") == 2
    )
    gemma_loaded = _assert_single_model(gemma.model_used)

    sample_path = REPOSITORY_ROOT / "test_data" / "student1.pdf"
    sample = sample_path.read_bytes()
    sample_hash = hashlib.sha256(sample).hexdigest()
    page_source_hash = f"{sample_hash}:qwen-ocr-smoke-v2"
    pages = model_router.ollama.render_pdf_to_images(
        sample,
        max_pages=1,
        dpi=110,
        source_hash=page_source_hash,
        job_id="local-smoke-student1-qwen-page1",
    )
    model_router.config.model_priorities[TaskCategory.MEDIUM.value] = ["qwen3.5:4b"]
    started_ocr = time.perf_counter()
    ocr_result, ocr_confidence = extract_text(
        pages,
        source_hash=page_source_hash,
        job_id="local-smoke-student1-qwen-page1",
    )
    active_after_ocr = model_router.ollama._active_model or ""
    ocr_loaded = _assert_single_model(active_after_ocr)
    recognized_count = sum(
        bool(str(item.get("extracted_text", "")).strip())
        for item in ocr_result.values()
    )
    recognized_chars = sum(
        len(str(item.get("extracted_text", "")))
        for item in ocr_result.values()
    )

    started_gemma_ocr = time.perf_counter()
    gemma_ocr_error = ""
    gemma_ocr = None
    try:
        gemma_ocr = model_router.generate(
            task_type="ocr_extraction",
            prompt=OCR_PROMPT,
            images=pages,
            format_json=True,
            response_json_schema=OCR_RESPONSE_SCHEMA,
            force_model="gemma4:e4b",
            avoid_models={"qwen3.5:4b"},
            max_tokens=768,
            timeout=300,
            task_id="local-smoke-student1-gemma-page1",
        )
        gemma_ocr_loaded = _assert_single_model(gemma_ocr.model_used)
        gemma_ocr_answers = (
            gemma_ocr.parsed_json.get("answers", [])
            if isinstance(gemma_ocr.parsed_json, dict)
            else []
        )
    except Exception as exc:
        gemma_ocr_error = type(exc).__name__
        gemma_ocr_loaded = _loaded_models()
        gemma_ocr_answers = []
    gemma_ocr_ok = bool(
        gemma_ocr
        and gemma_ocr.provider == "ollama"
        and gemma_ocr.model_used == "gemma4:e4b"
        and isinstance(gemma_ocr.parsed_json, dict)
        and gemma_ocr_answers
    )

    # Return unified memory to the user after the test run.
    last_active_model = model_router.ollama._active_model or ""
    if last_active_model and not model_router.ollama.unload_model(last_active_model):
        raise RuntimeError("Could not unload the last local model after smoke testing.")
    model_router.ollama._active_model = None
    final_loaded = _loaded_models()

    result = {
        "ollama_version": registry.get("server_version"),
        "models": {
            name: {
                "size_bytes": installed[name].size_bytes,
                "supports_images": installed[name].supports_images,
                "supports_structured_json": installed[name].supports_structured_json,
                "reasoning_suitable": installed[name].reasoning_suitable,
            }
            for name in expected
        },
        "qwen_structured_json": {
            "passed": qwen_ok,
            "model_used": qwen.model_used,
            "duration_seconds": round(qwen.duration_seconds, 2),
            "loaded_after_request": qwen_loaded,
        },
        "qwen_reasoning": {
            "passed": qwen_math_ok,
            "model_used": qwen_math.model_used,
            "duration_seconds": round(qwen_math.duration_seconds, 2),
            "synthetic_remainder": (
                qwen_math.parsed_json.get("remainder")
                if isinstance(qwen_math.parsed_json, dict)
                else None
            ),
            "loaded_after_request": qwen_math_loaded,
        },
        "gemma_reasoning": {
            "passed": gemma_ok,
            "model_used": gemma.model_used,
            "routing_reason": gemma.routing_reason,
            "duration_seconds": round(gemma.duration_seconds, 2),
            "synthetic_remainder": (
                gemma.parsed_json.get("remainder")
                if isinstance(gemma.parsed_json, dict)
                else None
            ),
            "loaded_after_request": gemma_loaded,
        },
        "student1_page1_local_ocr": {
            "answer_regions": len(ocr_result),
            "nonempty_regions": recognized_count,
            "recognized_characters": recognized_chars,
            "reported_ocr_confidence": ocr_confidence,
            "duration_seconds": round(time.perf_counter() - started_ocr, 2),
            "model_used": active_after_ocr,
            "loaded_after_request": ocr_loaded,
            "transcription_printed": False,
        },
        "student1_page1_gemma_ocr": {
            "passed": gemma_ocr_ok,
            "model_used": gemma_ocr.model_used if gemma_ocr else "",
            "answer_regions": len(gemma_ocr_answers),
            "duration_seconds": round(gemma_ocr.duration_seconds, 2) if gemma_ocr else round(
                time.perf_counter() - started_gemma_ocr, 2
            ),
            "error_type": gemma_ocr_error,
            "loaded_after_request": gemma_ocr_loaded,
            "transcription_printed": False,
        },
        "qwen_elapsed_seconds_including_routing": round(time.perf_counter() - started, 2),
        "gemma_elapsed_seconds_including_routing": round(time.perf_counter() - started_gemma, 2),
        "models_left_loaded": final_loaded,
        "gemini_requests": model_router.usage_summary()["external"]["requests"],
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    if not qwen_ok or not qwen_math_ok or not gemma_ok or not gemma_ocr_ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
