"""
AutoGrade — Configuration Module

Loads environment variables and initialises Firebase Admin SDK.
All API keys and Firebase credentials come from environment
variables only — never hardcoded.
"""
import os
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

FIREBASE_PROJECT_ID: str = os.getenv("FIREBASE_PROJECT_ID", "")
FIREBASE_STORAGE_BUCKET: str = os.getenv("FIREBASE_STORAGE_BUCKET", "")

# Path to Firebase service account JSON
_sa_env = os.getenv("FIREBASE_SERVICE_ACCOUNT", "")
if _sa_env and not os.path.isabs(_sa_env):
    FIREBASE_SERVICE_ACCOUNT = str((_BACKEND_DIR / _sa_env).resolve())
else:
    FIREBASE_SERVICE_ACCOUNT = _sa_env



# =========================================================
# FIREBASE ADMIN SDK INIT
# =========================================================

_firebase_app = None
_firestore_client = None
_storage_bucket = None


def _init_firebase():
    """Initialise Firebase Admin SDK (called once)."""
    global _firebase_app

    if _firebase_app is not None:
        return

    try:
        import firebase_admin
        from firebase_admin import credentials

        if FIREBASE_SERVICE_ACCOUNT and os.path.isfile(FIREBASE_SERVICE_ACCOUNT):
            cred = credentials.Certificate(FIREBASE_SERVICE_ACCOUNT)
        else:
            # Use Application Default Credentials
            cred = credentials.ApplicationDefault()

        _firebase_app = firebase_admin.initialize_app(
            cred,
            {
                "storageBucket": FIREBASE_STORAGE_BUCKET,
            }
        )

        print("[Config] Firebase Admin SDK initialised.")

    except Exception as error:
        print(f"[Config] Firebase init skipped: {error}")
        print("[Config] Running without Firebase — local dev mode.")


def get_firestore():
    """Return the Firestore client, initialising Firebase if needed."""
    global _firestore_client

    if _firestore_client is not None:
        return _firestore_client

    _init_firebase()

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

    try:
        from firebase_admin import storage
        _storage_bucket = storage.bucket()
        return _storage_bucket
    except Exception as error:
        print(f"[Config] Storage bucket unavailable: {error}")
        return None


# =========================================================
# GEMINI CLIENT
# =========================================================

def get_gemini_client():
    """Return a cached google.genai Client instance."""
    api_key = os.getenv("GEMINI_API_KEY") or GEMINI_API_KEY
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is missing from environment variables."
        )

    from google import genai
    return genai.Client(api_key=api_key)


# =========================================================
# GEMINI RETRY WRAPPER
# =========================================================

_RETRYABLE_CODES = {503, 429}
_MAX_RETRIES = 4


def gemini_generate_with_retry(
    *,
    contents,
    config: dict | None = None,
    model: str | None = None,
) -> object:
    """
    Call client.models.generate_content with automatic retry and
    model fallback.

    Retries up to _MAX_RETRIES times on 503 / 429 with exponential
    back-off + jitter.  If retries for a model are exhausted, the
    next model in MODEL_FALLBACK_CHAIN is tried from scratch.

    Parameters
    ----------
    contents  : prompt / multimodal content passed directly to the API
    config    : GenerateContentConfig dict (optional)
    model     : override model; defaults to GEMINI_MODEL

    Returns
    -------
    The raw Gemini API response object.

    Raises
    ------
    RuntimeError if every model in the fallback chain fails.
    """
    client = get_gemini_client()

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

                # Check if this is a retryable error (503 / 429)
                is_retryable = any(
                    code in err_str for code in ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED")
                )
                # Check if model is permanently gone (404)
                is_not_found = any(
                    code in err_str for code in ("404", "NOT_FOUND")
                )
                if is_not_found:
                    print(f"[Gemini] Model {current_model} not found, trying next in chain.")
                    break  # Skip remaining retries, move to next model

                # Check if daily quota is exhausted (no point in retrying in 2 seconds)
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

                # Non-retryable error — propagate immediately
                if not is_retryable:
                    raise

        # All retries for this model exhausted — try the next one
        print(f"[Gemini] All retries exhausted for {current_model}, trying next model…")

    raise RuntimeError(
        f"All Gemini models in fallback chain failed. Last error: {last_error}"
    )

