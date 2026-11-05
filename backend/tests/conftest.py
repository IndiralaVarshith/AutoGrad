"""Test safety: runs before any test imports the app.

No pytest run may reach real Firestore or send data to Gemini, so the emulator
host is always set (which stops config.py loading the service account key) and
Gemini is switched off. If no emulator is running, Firestore calls simply fail.
"""

import os

os.environ["FIRESTORE_EMULATOR_HOST"] = (
    os.environ.get("FIRESTORE_EMULATOR_HOST", "").strip() or "127.0.0.1:8080"
)
os.environ["GEMINI_ENABLED"] = "false"
os.environ["GEMINI_FALLBACK_ENABLED"] = "false"
os.environ["GEMINI_VERIFICATION_ENABLED"] = "false"
