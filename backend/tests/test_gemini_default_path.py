"""With the Gemini engine on, no route may reach a local model."""

from fastapi.testclient import TestClient

import config
import main
from routes import review
from services.router import model_router


def boom(*_args, **_kwargs):
    raise AssertionError("A local model was contacted.")


def test_status_page_does_not_contact_ollama(monkeypatch):
    monkeypatch.setattr(config, "GRADING_ENGINE", "gemini")
    monkeypatch.setattr(model_router.ollama, "check_health", boom)
    body = TestClient(main.app).get("/").json()
    assert body["grading_engine"] == "gemini" and "ollama" not in body


def test_old_routes_are_retired(monkeypatch):
    monkeypatch.setattr(config, "GRADING_ENGINE", "gemini")
    monkeypatch.setattr(model_router, "generate", boom)
    client = TestClient(main.app)
    old = client.post("/generate-rubric", files={"question_paper": ("p.pdf", b"x", "application/pdf")})
    assert old.status_code == 410
    regrade = client.post("/review/ocr-regrade", json={
        "exam_id": "e", "hall_ticket": "h", "question_number": 1, "sub_part": "",
    })
    assert regrade.status_code in (410, 422)


def test_requirements_leave_out_local_libraries():
    from pathlib import Path

    text = (Path(__file__).parents[1] / "requirements.txt").read_text().lower()
    for name in ("pix2text", "rapidocr", "chromadb", "ollama"):
        assert name not in text
