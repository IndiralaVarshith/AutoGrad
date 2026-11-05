"""
AutoGrade — Configuration Module

Loads environment variables and initialises Firebase Admin SDK.
All API keys and Firebase credentials come from environment
variables only — never hardcoded.
"""
from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Any
from dotenv import load_dotenv

# Ensure .env is loaded from the backend directory regardless of cwd
_BACKEND_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=_BACKEND_DIR / ".env")
load_dotenv()  # fallback to cwd if present


def _safe_float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _safe_int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# =========================================================
# ENVIRONMENT VARIABLES
# =========================================================

AI_PROVIDER: str = os.getenv("AI_PROVIDER", "ollama").strip().lower()
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_SIMPLE_MODEL: str = os.getenv("OLLAMA_SIMPLE_MODEL", "qwen3.5:4b").strip()
OLLAMA_COMPLEX_MODEL: str = os.getenv("OLLAMA_COMPLEX_MODEL", "gemma4:e4b").strip()
# The one setting for which local model reads question paper scans.
OLLAMA_PAPER_READER_MODEL: str = (
    os.getenv("OLLAMA_PAPER_READER_MODEL", "").strip() or OLLAMA_COMPLEX_MODEL
)
# The one setting for which local model reads student answer sheets.
OLLAMA_SHEET_READER_MODEL: str = (
    os.getenv("OLLAMA_SHEET_READER_MODEL", "").strip() or OLLAMA_COMPLEX_MODEL
)
# The one setting for which local model gives the first mark on a written answer.
# The second marker is always a different model.
OLLAMA_GRADER_MODEL: str = (
    os.getenv("OLLAMA_GRADER_MODEL", "").strip() or OLLAMA_COMPLEX_MODEL
)
OLLAMA_MAX_CONCURRENT_REQUESTS: int = int(os.getenv("OLLAMA_MAX_CONCURRENT_REQUESTS", "1"))
GEMINI_ENABLED: bool = os.getenv("GEMINI_ENABLED", "false").strip().lower() == "true"
GEMINI_FALLBACK_ENABLED: bool = os.getenv("GEMINI_FALLBACK_ENABLED", "false").strip().lower() == "true"
GEMINI_VERIFICATION_ENABLED: bool = os.getenv("GEMINI_VERIFICATION_ENABLED", "false").strip().lower() == "true"
GEMINI_VERIFICATION_SAMPLE_RATE: float = min(
    1.0, max(0.0, _safe_float_env("GEMINI_VERIFICATION_SAMPLE_RATE", 0.0))
)

HANDWRITING_DATASET_ENABLED: bool = os.getenv("HANDWRITING_DATASET_ENABLED", "false").strip().lower() == "true"
HANDWRITING_DATASET_RETENTION_DAYS: int = max(
    0, _safe_int_env("HANDWRITING_DATASET_RETENTION_DAYS", 0)
)
HANDWRITING_DATASET_SALT: str = os.getenv("HANDWRITING_DATASET_SALT", "")
HANDWRITING_DATASET_ACCESS_POLICY_CONFIRMED: bool = (
    os.getenv("HANDWRITING_DATASET_ACCESS_POLICY_CONFIRMED", "false").strip().lower() == "true"
)
HANDWRITING_DATASET_VERSION: str = os.getenv("HANDWRITING_DATASET_VERSION", "v1").strip() or "v1"

GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
GEMINI_REQUEST_TIMEOUT_MS: int = max(
    1_000, int(os.getenv("GEMINI_REQUEST_TIMEOUT_MS", "120000"))
)
GEMINI_MAX_RETRIES: int = max(
    1, min(4, int(os.getenv("GEMINI_MAX_RETRIES", "2")))
)
GEMINI_MAX_FALLBACK_MODELS: int = max(
    0, min(4, int(os.getenv("GEMINI_MAX_FALLBACK_MODELS", "2")))
)

FIREBASE_PROJECT_ID: str = os.getenv("FIREBASE_PROJECT_ID", "").strip()
FIREBASE_STORAGE_BUCKET: str = os.getenv("FIREBASE_STORAGE_BUCKET", "").strip()
FIREBASE_SERVICE_ACCOUNT: str = os.getenv("FIREBASE_SERVICE_ACCOUNT", "").strip()


def _resolve_service_account() -> str | dict | None:
    """
    Locate and resolve the Firebase service account credentials.

    Supports:
    1. Raw JSON string in FIREBASE_SERVICE_ACCOUNT, FIREBASE_SERVICE_ACCOUNT_JSON, or FIREBASE_KEY_JSON
    2. Explicit path in FIREBASE_SERVICE_ACCOUNT (absolute or relative to backend/ or project root)
    3. Auto-probing standard well-known locations:
       - backend/firebase-service-account.json
       - root firebase-service-account.json
       - backend/serviceAccountKey.json
       - root serviceAccountKey.json
    """
    raw_env = (
        os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
        or os.getenv("FIREBASE_KEY_JSON", "").strip()
        or FIREBASE_SERVICE_ACCOUNT
    )

    # Check if raw env variable contains the direct JSON dictionary
    if raw_env.startswith("{") and raw_env.endswith("}"):
        try:
            parsed = json.loads(raw_env)
            if isinstance(parsed, dict) and "project_id" in parsed:
                return parsed
        except Exception:
            pass

    # Check explicit path from env if specified
    if FIREBASE_SERVICE_ACCOUNT:
        p = Path(FIREBASE_SERVICE_ACCOUNT)
        if p.is_file():
            return str(p.resolve())
        bp = (_BACKEND_DIR / FIREBASE_SERVICE_ACCOUNT).resolve()
        if bp.is_file():
            return str(bp)
        rp = (_BACKEND_DIR.parent / FIREBASE_SERVICE_ACCOUNT).resolve()
        if rp.is_file():
            return str(rp)

    # Auto-probing standard filenames
    candidates = [
        _BACKEND_DIR / "firebase-service-account.json",
        _BACKEND_DIR.parent / "firebase-service-account.json",
        _BACKEND_DIR / "serviceAccountKey.json",
        _BACKEND_DIR.parent / "serviceAccountKey.json",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())

    return None


# Resolve initial path for inspection
_resolved_initial = _resolve_service_account()
if isinstance(_resolved_initial, str):
    FIREBASE_SERVICE_ACCOUNT = _resolved_initial


# =========================================================
# FIREBASE ADMIN SDK INIT
# =========================================================

# "demo-" projects are emulator-only: they cannot reach real Firebase resources.
EMULATOR_PROJECT_ID = "demo-autograd"

_firebase_app = None
_firestore_client = None
_storage_bucket = None


def _init_firebase():
    """Initialise Firebase Admin SDK (called once)."""
    global _firebase_app, FIREBASE_PROJECT_ID, FIREBASE_STORAGE_BUCKET

    if _firebase_app is not None:
        return

    try:
        import firebase_admin
        from firebase_admin import credentials

        # Emulator mode: never touch the service account key or a real project.
        if os.getenv("FIRESTORE_EMULATOR_HOST", "").strip():
            from google.auth.credentials import AnonymousCredentials

            class _EmulatorCredential(credentials.Base):
                def get_credential(self):
                    return AnonymousCredentials()

            FIREBASE_PROJECT_ID = EMULATOR_PROJECT_ID
            _firebase_app = firebase_admin.initialize_app(
                _EmulatorCredential(), {"projectId": EMULATOR_PROJECT_ID}
            )
            print(f"[Config] Using the Firestore emulator (project '{EMULATOR_PROJECT_ID}').")
            return

        sa_target = _resolve_service_account()
        cred = None

        if isinstance(sa_target, dict):
            cred = credentials.Certificate(sa_target)
        elif isinstance(sa_target, str) and os.path.isfile(sa_target):
            cred = credentials.Certificate(sa_target)
        elif os.getenv("GOOGLE_APPLICATION_CREDENTIALS") and os.path.isfile(os.getenv("GOOGLE_APPLICATION_CREDENTIALS")):
            cred = credentials.ApplicationDefault()

        if cred is None:
            print("[Config] No Firebase service account credentials found.")
            print("[Config] Running without Firebase — local dev mode.")
            return

        # Auto-detect project ID from certificate if not explicitly set
        detected_project_id = getattr(cred, "project_id", None)
        if not FIREBASE_PROJECT_ID and detected_project_id:
            FIREBASE_PROJECT_ID = detected_project_id

        # Auto-detect storage bucket if not set
        options: dict[str, str] = {}
        if FIREBASE_STORAGE_BUCKET:
            options["storageBucket"] = FIREBASE_STORAGE_BUCKET
        elif FIREBASE_PROJECT_ID:
            # Default GCP / Firebase bucket convention
            FIREBASE_STORAGE_BUCKET = f"{FIREBASE_PROJECT_ID}.appspot.com"
            options["storageBucket"] = FIREBASE_STORAGE_BUCKET

        if FIREBASE_PROJECT_ID:
            options["projectId"] = FIREBASE_PROJECT_ID

        _firebase_app = firebase_admin.initialize_app(cred, options if options else None)
        print(
            f"[Config] Firebase Admin SDK initialised successfully "
            f"(Project: '{FIREBASE_PROJECT_ID or 'auto'}', Storage: '{options.get('storageBucket', 'none')}')."
        )

    except Exception as error:
        print(f"[Config] Firebase init skipped/error: {error}")
        print("[Config] Running without Firebase — local dev mode.")


def get_firestore():
    """Return the Firestore client, initialising Firebase if needed."""
    global _firestore_client

    if _firestore_client is not None:
        return _firestore_client

    _init_firebase()

    if _firebase_app is None:
        return None

    try:
        from firebase_admin import firestore as fb_firestore
        _firestore_client = fb_firestore.client()
        return _firestore_client
    except Exception as error:
        print(f"[Config] Firestore unavailable: {error}")
        return None


def get_storage_bucket():
    """Return the Firebase Storage bucket, initialising Firebase if needed."""
    global _storage_bucket

    if _storage_bucket is not None:
        return _storage_bucket

    _init_firebase()

    if _firebase_app is None:
        return None

    try:
        from firebase_admin import storage
        if FIREBASE_STORAGE_BUCKET:
            _storage_bucket = storage.bucket(FIREBASE_STORAGE_BUCKET)
        else:
            _storage_bucket = storage.bucket()
        return _storage_bucket
    except Exception as error:
        print(f"[Config] Storage bucket unavailable: {error}")
        return None


# =========================================================
# GEMINI CLIENT
# =========================================================

def get_gemini_client():
    """Return the selected, owner-authorized Gemini client without exposing its key."""
    from services.provider_registry import GeminiCredentialError, gemini_credentials

    if AI_PROVIDER != "gemini" and not GEMINI_ENABLED and not GEMINI_FALLBACK_ENABLED:
        return None
    try:
        credential_id = gemini_credentials.select_credential()
        return gemini_credentials.client_for(credential_id)
    except GeminiCredentialError:
        if AI_PROVIDER == "gemini" or GEMINI_ENABLED or GEMINI_FALLBACK_ENABLED:
            raise RuntimeError("No eligible Gemini credential is configured and owner-authorized.") from None
        return None


# =========================================================
# ADAPTIVE GENERATE WRAPPER (OLLAMA ROUTER + GEMINI FALLBACK)
# =========================================================

class _ModelOutputWrapper:
    """Lightweight wrapper providing .text attribute matching Google GenAI response."""
    def __init__(self, text: str, parsed_json=None, model_used: str = ""):
        self.text = text
        self.parsed_json = parsed_json
        self.model_used = model_used


def gemini_generate_with_retry(
    *,
    contents,
    config: dict | None = None,
    model: str | None = None,
    task_category: str | None = None,
    requires_vision: bool | None = None,
    requires_reasoning: bool | None = None,
    avoid_model: str | None = None,
    avoid_models: list[str] | set[str] | None = None,
    task_id: str | None = None,
    local_model: str | None = None,
) -> object:
    """Route existing OCR/agent calls through the shared capability router."""
    from services.router import model_router

    prompt_parts: list[str] = []
    images: list[Any] = []
    incoming = contents if isinstance(contents, list) else [contents]
    for part in incoming:
        if isinstance(part, str):
            path = Path(part)
            if part.startswith("data:image/") or (
                len(part) > 256 and not any(char.isspace() for char in part)
                and all(char.isalnum() or char in "+/=_-" for char in part[:256])
            ):
                images.append(part)
            elif path.is_file() and path.suffix.lower() in {
                ".jpg", ".jpeg", ".png", ".webp", ".heic", ".pdf"
            }:
                images.append(path)
            else:
                prompt_parts.append(part)
        elif (
            isinstance(part, (bytes, Path))
            or hasattr(part, "tobytes")
            or hasattr(part, "inline_data")
            or hasattr(part, "file_data")
            or hasattr(part, "uri")
            or hasattr(part, "name")
        ):
            images.append(part)
        else:
            prompt_parts.append(str(part))

    full_prompt = "\n".join(prompt_parts)
    cfg = config if isinstance(config, dict) else {}
    temp = float(cfg.get("temperature", 0.1))
    system_instruction = cfg.get("system_instruction")
    format_json = (
        cfg.get("response_mime_type") == "application/json"
        or "response_json_schema" in cfg
        or "response_schema" in cfg
    )
    response_schema = cfg.get("response_json_schema") or cfg.get("response_schema")

    prompt_lower = full_prompt.lower()
    task_type = "grading"
    if "classify this exam question" in prompt_lower:
        task_type = "classification"
        format_json = bool(response_schema)
    elif any(term in prompt_lower for term in (
        "ocr module", "ocr transcription", "extract all handwritten text",
        "extract all answers", "illegible_regions",
    )):
        task_type = "ocr_extraction"
        format_json = True
    elif "student name and hall ticket" in prompt_lower or "hall_ticket" in prompt_lower:
        task_type = "student_meta"
        format_json = True
    elif "rubric generation module" in prompt_lower or (
        "rubric" in prompt_lower and "max_marks" in prompt_lower and "upload" in prompt_lower
    ):
        task_type = "rubric_generation"
        format_json = True
    elif "fix only latex syntax errors" in prompt_lower:
        task_type = "math_syntax"
        format_json = False
    elif "review this grading" in prompt_lower or "critic" in prompt_lower:
        task_type = "critique"
        format_json = True
    elif "resolve" in prompt_lower or "arbitration" in prompt_lower:
        task_type = "resolution"
        format_json = True

    question_type = "THEORY"
    if "diagram" in prompt_lower:
        question_type = "DIAGRAM"
    elif "numerical" in prompt_lower or "equation" in prompt_lower or "derivation" in prompt_lower:
        question_type = "NUMERICAL"

    response = model_router.generate(
        task_type=task_type,
        prompt=full_prompt,
        images=images or None,
        system_instruction=system_instruction,
        format_json=format_json,
        temperature=temp,
        question_type=question_type,
        task_category=task_category,
        requires_vision=requires_vision,
        requires_reasoning=requires_reasoning,
        avoid_model=avoid_model,
        avoid_models=avoid_models,
        task_id=task_id,
        response_json_schema=response_schema,
        force_model=model if AI_PROVIDER == "gemini" else local_model,
    )
    return _ModelOutputWrapper(
        text=response.text,
        parsed_json=response.parsed_json,
        model_used=response.model_used,
    )
