"""Test safety: runs before any test imports the app.

No pytest run may reach real Firestore or send data to Gemini, so the emulator
host is always set (which stops config.py loading the service account key) and
Gemini is switched off. If no emulator is running, Firestore calls simply fail.
"""

import os
import tempfile

# Some tests wipe the whole emulator, so they get their own on 8099 and never the app's
# emulator on 8080, which holds the real exams. Start it with:
#   firebase emulators:start --only firestore  (on port 8099), or the java jar with --port 8099
os.environ["FIRESTORE_EMULATOR_HOST"] = (
    os.environ.get("AUTOGRAD_TEST_EMULATOR_HOST", "").strip() or "127.0.0.1:8099"
)
if os.environ["FIRESTORE_EMULATOR_HOST"].rsplit(":", 1)[-1] == "8080":
    raise RuntimeError("Tests must not use the emulator on 8080: it holds the app's real data.")
os.environ["AUTOGRAD_BLOCK_REAL_GEMINI"] = "1"
os.environ["TEACHER_PASSWORD"] = "test-password"
# Sample answers are a few words long; the length tests switch the check on themselves.
os.environ["GEMINI_WORDS_PER_MARK"] = "0"
# Tests never touch the real call log, daily count, queue or stored sheets.
_TEST_DATA = tempfile.mkdtemp(prefix="autograd-tests-")
os.environ["QUEUE_DATA_DIR"] = _TEST_DATA
os.environ["GEMINI_USAGE_DIR"] = _TEST_DATA


def signed_in_client():
    """A client for the full app, signed in as the teacher."""
    from fastapi.testclient import TestClient

    import main

    client = TestClient(main.app)
    response = client.post("/auth/login", json={"password": "test-password"})
    assert response.status_code == 200
    return client
