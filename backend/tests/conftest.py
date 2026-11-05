"""Test safety: runs before any test imports the app.

No pytest run may reach real Firestore or send data to Gemini, so the emulator
host is always set (which stops config.py loading the service account key) and
Gemini is switched off. If no emulator is running, Firestore calls simply fail.
"""

import os
import tempfile

os.environ["FIRESTORE_EMULATOR_HOST"] = (
    os.environ.get("FIRESTORE_EMULATOR_HOST", "").strip() or "127.0.0.1:8080"
)
os.environ["AUTOGRAD_BLOCK_REAL_GEMINI"] = "1"
os.environ["TEACHER_PASSWORD"] = "test-password"
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
