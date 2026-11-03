"""
AutoGrade — Configuration Module

Loads environment variables and initialises Firebase Admin SDK.
All API keys and Firebase credentials come from environment
variables only — never hardcoded.
"""
from __future__ import annotations

import os
import json
import time
import random
from pathlib import Path
from dotenv import load_dotenv

# Ensure .env is loaded from the backend directory regardless of cwd
_BACKEND_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=_BACKEND_DIR / ".env")
load_dotenv()  # fallback to cwd if present


# =========================================================
# ENVIRONMENT VARIABLES
# =========================================================

AI_PROVIDER: str = os.getenv("AI_PROVIDER", "ollama").strip().lower()
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_SIMPLE_MODEL: str = os.getenv("OLLAMA_SIMPLE_MODEL", "qwen3.5:4b").strip()
OLLAMA_COMPLEX_MODEL: str = os.getenv("OLLAMA_COMPLEX_MODEL", "gemma4:e4b").strip()
OLLAMA_MAX_CONCURRENT_REQUESTS: int = int(os.getenv("OLLAMA_MAX_CONCURRENT_REQUESTS", "1"))
GEMINI_ENABLED: bool = os.getenv("GEMINI_ENABLED", "false").strip().lower() == "true"
GEMINI_FALLBACK_ENABLED: bool = os.getenv("GEMINI_FALLBACK_ENABLED", "false").strip().lower() == "true"

GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

# Ordered fallback chain — tried in sequence if primary model is unavailable
# All names verified against live API (models.list()) on 2026-10-24
MODEL_FALLBACK_CHAIN: list[str] = [
    GEMINI_MODEL,        # gemini-3.8-flash (primary)
    "gemini-3.7-flash",  # one generation back
    "gemini-3.6-flash",  # two generations back
    "gemini-3.5-flash",  # stable older release
    "gemini-flash-latest",  # alias — always resolves to current stable
]

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
    """Return a cached google.genai Client instance (lazy initialization)."""
    api_key = os.getenv("GEMINI_API_KEY") or GEMINI_API_KEY
    if not api_key:
        if AI_PROVIDER == "gemini":
            raise RuntimeError(
                "GEMINI_API_KEY is missing from environment variables."
            )
        return None

    try:
        from google import genai
        return genai.Client(api_key=api_key)
    except Exception as err:
        if AI_PROVIDER == "gemini":
            raise err
        print(f"[Config] Gemini client init deferred: {err}")
        return None


# =========================================================
# ADAPTIVE GENERATE WRAPPER (OLLAMA ROUTER + GEMINI FALLBACK)
# =========================================================

_RETRYABLE_CODES = {503, 429}
_MAX_RETRIES = 4


class _ModelOutputWrapper:
    """Lightweight wrapper providing .text attribute matching Google GenAI response."""
    def __init__(self, text: str, parsed_json=None):
        self.text = text
        self.parsed_json = parsed_json


def gemini_generate_with_retry(
    *,
    contents,
    config: dict | None = None,
    model: str | None = None,
) -> object:
    """
    Generate content with intelligent routing:
    - If AI_PROVIDER == 'ollama' (default): routes through local OllamaClient (Qwen / Gemma)
    - If AI_PROVIDER == 'gemini': uses cloud Gemini API with retry fallback chain
    """
    # -----------------------------------------------------
    # LOCAL OLLAMA ROUTING PATH
    # -----------------------------------------------------
    if AI_PROVIDER == "ollama":
        from services.router import model_router

        # Deconstruct contents into text prompts and image components
        prompt_parts: list[str] = []
        images: list[Any] = []

        if isinstance(contents, str):
            if len(contents) > 500 and (" " not in contents[:200] or contents.startswith("data:image/")):
                images.append(contents)
            else:
                prompt_parts.append(contents)
        elif isinstance(contents, list):
            for part in contents:
                if isinstance(part, str):
                    # Detect base64 image strings vs normal text prompts
                    if len(part) > 500 and (" " not in part[:200] or part.startswith("data:image/")):
                        images.append(part)
                    elif os.path.isfile(part) and Path(part).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
                        images.append(part)
                    else:
                        prompt_parts.append(part)
                elif hasattr(part, "name") or hasattr(part, "tobytes") or isinstance(part, (bytes, Path)):
                    images.append(part)
                elif hasattr(part, "uri"):  # GenAI File
                    images.append(part)
                else:
                    prompt_parts.append(str(part))
        else:
            prompt_parts.append(str(contents))

        full_prompt = "\n".join(prompt_parts)

        # Extract config parameters
        temp = 0.1
        sys_instruction = None
        format_json = False

        if config and isinstance(config, dict):
            temp = float(config.get("temperature", 0.1))
            sys_instruction = config.get("system_instruction")
            if config.get("response_mime_type") == "application/json" or "response_json_schema" in config:
                format_json = True

        # Infer task type based on prompt content
        task_type = "grading"
        q_type = "THEORY"
        illegible_count = 0
        max_tokens = None

        p_lower = full_prompt.lower()
        if "classify this exam question" in p_lower:
            task_type = "classification"
            format_json = False
            max_tokens = 50
        elif "ocr module" in p_lower or "ocr transcription" in p_lower or "extract all handwritten text" in p_lower or "extract all answers" in p_lower or "illegible_regions" in p_lower:
            task_type = "ocr_extraction"
            format_json = True
        elif "student name and hall ticket" in p_lower or "hall_ticket" in p_lower:
            task_type = "student_meta"
            format_json = True
        elif "rubric generation module" in p_lower or ("rubric" in p_lower and "max_marks" in p_lower and "upload" in p_lower):
            task_type = "rubric_generation"
            format_json = True
        elif "fix only latex syntax errors" in p_lower:
            task_type = "math_syntax"
            format_json = False
        elif "review this grading" in p_lower or "critic" in p_lower:
            task_type = "critique"
            format_json = True
        elif "evaluate the student's answer" in p_lower or "grading" in p_lower:
            task_type = "grading"
            format_json = True

        if "diagram" in p_lower:
            q_type = "DIAGRAM"
        elif "numerical" in p_lower:
            q_type = "NUMERICAL"

        res = model_router.generate(
            task_type=task_type,
            prompt=full_prompt,
            images=images if images else None,
            system_instruction=sys_instruction,
            format_json=format_json,
            temperature=temp,
            question_type=q_type,
            illegible_count=illegible_count,
            max_tokens=max_tokens,
        )

        return _ModelOutputWrapper(text=res.text, parsed_json=res.parsed_json)

    # -----------------------------------------------------
    # CLOUD GEMINI PROVIDER PATH
    # -----------------------------------------------------
    client = get_gemini_client()
    if client is None:
        raise RuntimeError("Gemini client is not initialized. Ensure GEMINI_API_KEY is configured.")

    # Build the ordered list of models to try
    primary = model or GEMINI_MODEL
    chain = [primary] + [m for m in MODEL_FALLBACK_CHAIN if m != primary]

    last_error: Exception | None = None

    for current_model in chain:
        for attempt in range(_MAX_RETRIES):
            try:
                kwargs: dict = {"model": current_model, "contents": contents}
                if config:
                    kwargs["config"] = config

                response = client.models.generate_content(**kwargs)
                if current_model != primary:
                    print(f"[Gemini] Using fallback model: {current_model}")
                return response

            except Exception as exc:
                last_error = exc
                err_str = str(exc)

                is_retryable = any(
                    code in err_str for code in ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED")
                )
                is_not_found = any(
                    code in err_str for code in ("404", "NOT_FOUND")
                )
                if is_not_found:
                    print(f"[Gemini] Model {current_model} not found, trying next in chain.")
                    break

                is_daily_exhausted = "per day limit exceeded" in err_str.lower()
                if is_daily_exhausted:
                    print(f"[Gemini] Daily quota exceeded for {current_model}, switching to next model immediately.")
                    break

                if is_retryable and attempt < _MAX_RETRIES - 1:
                    wait = (2 ** attempt) + random.uniform(0, 1)
                    print(
                        f"[Gemini] {current_model} overloaded "
                        f"(attempt {attempt + 1}/{_MAX_RETRIES}), "
                        f"retrying in {wait:.1f}s…"
                    )
                    time.sleep(wait)
                    continue

                if not is_retryable:
                    raise

        print(f"[Gemini] All retries exhausted for {current_model}, trying next model…")

    raise RuntimeError(
        f"All Gemini models in fallback chain failed. Last error: {last_error}"
    )

