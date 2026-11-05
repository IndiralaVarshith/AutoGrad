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


HANDWRITING_DATASET_ENABLED: bool = os.getenv("HANDWRITING_DATASET_ENABLED", "false").strip().lower() == "true"
HANDWRITING_DATASET_RETENTION_DAYS: int = max(
    0, _safe_int_env("HANDWRITING_DATASET_RETENTION_DAYS", 0)
)
HANDWRITING_DATASET_SALT: str = os.getenv("HANDWRITING_DATASET_SALT", "")
HANDWRITING_DATASET_ACCESS_POLICY_CONFIRMED: bool = (
    os.getenv("HANDWRITING_DATASET_ACCESS_POLICY_CONFIRMED", "false").strip().lower() == "true"
)
HANDWRITING_DATASET_VERSION: str = os.getenv("HANDWRITING_DATASET_VERSION", "v1").strip() or "v1"

GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
GEMINI_REQUEST_TIMEOUT_MS: int = max(
    1_000, int(os.getenv("GEMINI_REQUEST_TIMEOUT_MS", "120000"))
)


# --- Gemini-only path: request budget (services/gemini_client.py) ---
# Which .env entry holds the one key this app uses. Only the name is stored here.
GEMINI_KEY_ENV_NAME: str = os.getenv("GEMINI_KEY_ENV_NAME", "GEMINI_API_KEY").strip()
# Stronger model, for flagged answers only. Empty switches it off.
GEMINI_REVIEW_MODEL: str = os.getenv("GEMINI_REVIEW_MODEL", "gemini-3.8-flash").strip()
# Paced below the 15 RPM / 250K TPM that AI Studio shows for 3.5 Flash-Lite (2026-11-06).
GEMINI_RPM_LIMIT: int = max(1, int(os.getenv("GEMINI_RPM_LIMIT", "12")))
GEMINI_TPM_LIMIT: int = max(1, int(os.getenv("GEMINI_TPM_LIMIT", "200000")))
# Unconfirmed: 500 and 20 come from users quoting Google's errors on its forum.
GEMINI_RPD_LIMIT: int = max(1, int(os.getenv("GEMINI_RPD_LIMIT", "500")))
GEMINI_REVIEW_RPD_LIMIT: int = max(1, int(os.getenv("GEMINI_REVIEW_RPD_LIMIT", "20")))
GEMINI_RUN_CALL_CAP: int = max(1, int(os.getenv("GEMINI_RUN_CALL_CAP", "100")))
GEMINI_REVIEW_CALL_CAP: int = min(15, max(0, int(os.getenv("GEMINI_REVIEW_CALL_CAP", "15"))))
GEMINI_PAGES_PER_REQUEST: int = max(1, int(os.getenv("GEMINI_PAGES_PER_REQUEST", "12")))
GEMINI_MAX_OUTPUT_TOKENS: int = max(256, int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "16384")))
GEMINI_RETRY_ATTEMPTS: int = max(1, int(os.getenv("GEMINI_RETRY_ATTEMPTS", "5")))
GEMINI_RETRY_BASE_SECONDS: float = float(os.getenv("GEMINI_RETRY_BASE_SECONDS", "15"))
GEMINI_RETRY_MAX_WAIT_SECONDS: float = float(os.getenv("GEMINI_RETRY_MAX_WAIT_SECONDS", "90"))
# Below this, Gemini's own confidence sends an answer to the teacher.
GEMINI_MIN_CONFIDENCE: float = float(os.getenv("GEMINI_MIN_CONFIDENCE", "0.8"))
# Partial marks on a question worth this much or more go to the teacher.
GEMINI_HIGH_VALUE_MARKS: float = float(os.getenv("GEMINI_HIGH_VALUE_MARKS", "5"))
# Second opinions from the stronger model. Off by default; they use its small daily allowance.
GEMINI_REVIEW_ENABLED: bool = os.getenv("GEMINI_REVIEW_ENABLED", "false").strip().lower() == "true"
GEMINI_USAGE_DIR: str = os.getenv("GEMINI_USAGE_DIR", str(_BACKEND_DIR / "gemini_usage"))
# Paid-tier prices, for the notional cost only. A model missing here reports cost unknown.
GEMINI_PRICES_USD_PER_MILLION: dict[str, dict[str, float]] = {
    "gemini-3.5-flash-lite": {"input": 0.30, "output": 2.50},
    "gemini-3.1-flash-lite": {"input": 0.25, "output": 1.50},
    "gemini-3.8-flash": {"input": 0.75, "output": 3.75},
}
GEMINI_PRICES_AS_OF: str = "Gemini API pricing page, read 2026-11-06"
GEMINI_USD_INR: float = float(os.getenv("GEMINI_USD_INR", "94.48"))
GEMINI_USD_INR_SOURCE: str = os.getenv(
    "GEMINI_USD_INR_SOURCE", "frankfurter.dev, rate dated 2026-11-05"
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
