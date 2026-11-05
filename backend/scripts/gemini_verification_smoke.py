"""One real Gemini vision/JSON call using synthetic, non-student content.

This verifies the explicit independent-check path without sending a student
paper. Run only after Gemini credentials have been explicitly authorized.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(dotenv_path=BACKEND_DIR / ".env")

from services.router import model_router  # noqa: E402


SCHEMA = {
    "type": "object",
    "properties": {
        "transcription": {"type": "string"},
        "addresses_question": {"type": "boolean"},
        "evidence_supported": {"type": "boolean"},
        "missed_relevant_content": {"type": "boolean"},
        "suggested_score": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": [
        "transcription", "addresses_question", "evidence_supported",
        "missed_relevant_content", "suggested_score", "reason",
    ],
}


def main() -> int:
    image = Image.new("RGB", (1100, 260), "white")
    draw = ImageDraw.Draw(image)
    draw.text((36, 70), "The nucleus contains genetic material.", fill="black", font=ImageFont.load_default(size=38))
    prompt = (
        "Independently inspect the supplied synthetic answer image. Transcribe only what is visible. "
        "Question: What does a cell nucleus contain? Approved rubric: 1 mark for identifying genetic material. "
        "Maximum marks: 1. Return the required JSON and do not assume a suggested answer."
    )
    started = time.perf_counter()
    try:
        result = model_router.generate_gemini_verification(
            task_id="synthetic-gemini-verification-smoke",
            prompt=prompt,
            images=[image],
            response_json_schema=SCHEMA,
        )
        payload = result.parsed_json if isinstance(result.parsed_json, dict) else json.loads(result.text)
        required = set(SCHEMA["required"])
        if not isinstance(payload, dict) or not required.issubset(payload):
            raise ValueError("Gemini returned incomplete structured output.")
        passed = (
            result.provider == "gemini"
            and bool(result.credential_id)
            and bool(str(payload.get("transcription", "")).strip())
            and payload.get("addresses_question") is True
            and payload.get("evidence_supported") is True
            and float(payload.get("suggested_score", -1)) == 1.0
        )
        print(json.dumps({
            "passed": passed,
            "provider": result.provider,
            "model": result.model_used,
            "credential_id": result.credential_id,
            "duration_seconds": round(time.perf_counter() - started, 2),
            "image_input": True,
            "structured_json": True,
            "student_content_sent": False,
            "usage": model_router.usage_summary(),
        }, indent=2, sort_keys=True))
        return 0 if passed else 1
    except Exception as error:
        print(json.dumps({
            "passed": False,
            "error_type": type(error).__name__,
            "provider_status_code": getattr(error, "status_code", None),
            "credential_id": getattr(error, "credential_id", ""),
            "model": getattr(error, "model_name", ""),
            "retry_count": getattr(error, "retry_count", 0),
            "duration_seconds": round(time.perf_counter() - started, 2),
            "student_content_sent": False,
        }, indent=2, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

