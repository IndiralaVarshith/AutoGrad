"""Inspect configured providers without printing secrets or generating content.

Run from the project root with:
    .venv/bin/python backend/scripts/provider_diagnostics.py

Pass --validate-gemini to call the Gemini models-list endpoint independently
for each configured, owner-authorized key. This does not generate model output.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(dotenv_path=BACKEND_DIR / ".env")

from services.router import model_router  # noqa: E402


def _compact_diagnostics(data: dict) -> dict:
    """Summarize per-key model access without dumping dozens of model records."""
    summaries = []
    for credential in data.get("gemini_credentials", []):
        models = credential.get("models", [])
        generation = sorted(
            model.get("name", "")
            for model in models
            if model.get("available")
            and any(
                str(method).lower().replace("_", "") == "generatecontent"
                for method in model.get("supported_methods", [])
            )
        )
        embeddings = sorted(
            model.get("name", "")
            for model in models
            if model.get("available")
            and any(
                str(method).lower().replace("_", "") == "embedcontent"
                for method in model.get("supported_methods", [])
            )
        )
        by_name = {model.get("name"): model for model in models}
        configured_candidates = []
        defaults = {
            "GEMINI_LOW_COMPLEXITY_MODELS": "gemini-3.8-flash",
            "GEMINI_MEDIUM_COMPLEXITY_MODELS": "gemini-3.8-flash",
            "GEMINI_HIGH_COMPLEXITY_MODELS": "gemini-3.8-flash",
            "GEMINI_CRITICAL_VALIDATION_MODELS": "gemini-3.1-pro-preview,gemini-3.8-flash",
            "GEMINI_MODEL_PRIORITY": "gemini-3.8-flash",
            "GEMINI_MODEL": "gemini-3.8-flash",
        }
        for env_name in (
            "GEMINI_LOW_COMPLEXITY_MODELS",
            "GEMINI_MEDIUM_COMPLEXITY_MODELS",
            "GEMINI_HIGH_COMPLEXITY_MODELS",
            "GEMINI_CRITICAL_VALIDATION_MODELS",
            "GEMINI_MODEL_PRIORITY",
            "GEMINI_MODEL",
        ):
            configured_candidates.extend(
                value.strip()
                for value in os.getenv(env_name, defaults.get(env_name, "")).split(",")
                if value.strip()
            )
        candidate_access = []
        for name in dict.fromkeys(configured_candidates):
            model = by_name.get(name)
            candidate_access.append({
                "name": name,
                "available_to_key": bool(model and model.get("available") and "generateContent" in model.get("supported_methods", [])),
                "supports_images": model.get("supports_images") if model else None,
                "supports_structured_json": model.get("supports_structured_json") if model else None,
                "reasoning_suitable": model.get("reasoning_suitable") if model else None,
            })
        summary = {
            "credential_id": credential.get("credential_id"),
            "status": credential.get("status", credential.get("validation_status", "unknown")),
            "error_type": credential.get("error_type", ""),
            "model_count": credential.get("model_count", len(models)),
            "available_generate_content_count": len(generation),
            "available_embed_content_count": len(embeddings),
            "available_generation_models_sample": generation[:12],
            "generation_model_sample_truncated": len(generation) > 12,
            "configured_candidate_access": candidate_access,
            "embedding_models": embeddings[:8],
        }
        summaries.append(summary)
    return {
        "gemini_credentials": summaries,
        "ollama": data.get("ollama", {}),
        "usage": data.get("usage", {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--validate-gemini",
        action="store_true",
        help="Validate each authorized Gemini key using the models-list API.",
    )
    args = parser.parse_args()
    diagnostics = model_router.provider_diagnostics(validate_gemini=args.validate_gemini)
    if args.validate_gemini:
        diagnostics = _compact_diagnostics(diagnostics)
    print(json.dumps(diagnostics, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
