"""
AutoGrade — Configuration Module

Loads environment variables and initialises Firebase Admin SDK.
All API keys and Firebase credentials come from environment
variables only — never hardcoded.
"""

import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


# =========================================================
# ENVIRONMENT VARIABLES
# =========================================================

GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL: str = "gemini-2.0-flash"

FIREBASE_PROJECT_ID: str = os.getenv("FIREBASE_PROJECT_ID", "")
FIREBASE_STORAGE_BUCKET: str = os.getenv("FIREBASE_STORAGE_BUCKET", "")

# Path to Firebase service account JSON (optional — if running
# locally without Application Default Credentials)
FIREBASE_SERVICE_ACCOUNT: str = os.getenv("FIREBASE_SERVICE_ACCOUNT", "")


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

@lru_cache(maxsize=1)
def get_gemini_client():
    """Return a cached google.genai Client instance."""
    if not GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY is missing from environment variables."
        )

    from google import genai
    return genai.Client(api_key=GEMINI_API_KEY)
