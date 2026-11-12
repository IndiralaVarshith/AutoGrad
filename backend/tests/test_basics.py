"""Step 8: sign-in, CORS, upload limits, pinned versions."""

import io
import re
from pathlib import Path

import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient

import config
import main
from routes import upload as upload_route
from services import teacher_auth
from services.upload_limits import read_limited
from tests.conftest import signed_in_client

BACKEND = Path(__file__).resolve().parents[1]


def test_status_page_needs_no_local_model_or_password():
    body = TestClient(main.app).get("/").json()
    assert "ollama" not in body and body["gemini_model"] == config.GEMINI_MODEL


@pytest.mark.parametrize("method,path", [
    ("get", "/exam"),
    ("get", "/results/e"),
    ("get", "/export/e"),
    ("get", "/queue/e"),
    ("get", "/review/source-page/e/h/1"),
    ("post", "/upload"),
    ("post", "/exam/setup"),
    ("post", "/review/approve"),
])
def test_everything_else_needs_the_teacher(method, path):
    response = getattr(TestClient(main.app), method)(path)
    assert response.status_code == 401


def test_every_route_is_protected_except_the_open_list():
    open_paths = set(main._OPEN_PATHS)
    assert open_paths == {"/", "/health", "/auth/login", "/auth/logout", "/auth/status"}


def test_wrong_password_is_refused_and_right_one_works():
    client = TestClient(main.app)
    assert client.post("/auth/login", json={"password": "nope"}).status_code == 401
    assert client.get("/auth/status").json()["signed_in"] is False
    assert client.post("/auth/login", json={"password": "test-password"}).status_code == 200
    assert client.get("/auth/status").json()["signed_in"] is True
    assert client.post("/auth/logout").status_code == 200
    assert client.get("/auth/status").json()["signed_in"] is False


def test_no_password_set_means_nobody_gets_in(monkeypatch):
    token = teacher_auth.new_session()
    monkeypatch.delenv("TEACHER_PASSWORD")
    client = TestClient(main.app, cookies={teacher_auth.COOKIE_NAME: token})
    assert client.post("/auth/login", json={"password": ""}).status_code == 503
    assert client.get("/exam").status_code == 401


def test_session_cookie_cannot_be_forged_or_outlive_its_time():
    token = teacher_auth.new_session(now=1000)
    assert teacher_auth.session_is_valid(token, now=1001)
    assert not teacher_auth.session_is_valid(token, now=1000 + teacher_auth.SESSION_SECONDS + 1)
    expires, _, signature = token.partition(".")
    assert not teacher_auth.session_is_valid(f"{int(expires) + 999}.{signature}", now=1001)
    assert not teacher_auth.session_is_valid("garbage", now=1001)
    assert not teacher_auth.session_is_valid(None)


def test_changing_the_password_ends_old_sessions(monkeypatch):
    token = teacher_auth.new_session()
    monkeypatch.setenv("TEACHER_PASSWORD", "another")
    assert not teacher_auth.session_is_valid(token)


def test_restarting_the_server_ends_old_sessions(monkeypatch):
    token = teacher_auth.new_session()
    assert teacher_auth.session_is_valid(token)
    # What a fresh start of the server does: a new secret for this run.
    monkeypatch.setattr(teacher_auth, "_RUN_SECRET", "a-later-run")
    assert not teacher_auth.session_is_valid(token)


def test_cookie_is_http_only_and_ends_with_the_browser():
    response = TestClient(main.app).post("/auth/login", json={"password": "test-password"})
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert "max-age" not in cookie and "expires" not in cookie


@pytest.mark.parametrize("origin,allowed", [
    ("http://localhost:5173", True),
    ("http://127.0.0.1:5173", True),
    ("http://localhost", True),
    ("https://evil.example", False),
    ("http://localhost.evil.example", False),
    ("https://my-app.web.app", False),
])
def test_cors_only_allows_this_machine(origin, allowed):
    response = TestClient(main.app).options(
        "/exam", headers={"Origin": origin, "Access-Control-Request-Method": "GET"}
    )
    assert (response.headers.get("access-control-allow-origin") == origin) is allowed


def test_one_file_over_the_limit_is_refused(monkeypatch):
    import asyncio

    from fastapi import HTTPException

    monkeypatch.setattr(config, "MAX_UPLOAD_MB", 1)
    fine = UploadFile(io.BytesIO(b"x" * 1024), filename="ok.pdf")
    assert len(asyncio.run(read_limited(fine))) == 1024
    big = UploadFile(io.BytesIO(b"x" * (1024 * 1024 + 1)), filename="big.pdf")
    with pytest.raises(HTTPException) as caught:
        asyncio.run(read_limited(big))
    assert caught.value.status_code == 413 and "big.pdf" in caught.value.detail


def test_whole_request_over_the_limit_is_refused(monkeypatch):
    monkeypatch.setattr(config, "MAX_REQUEST_MB", 0.001)
    client = signed_in_client()
    response = client.post("/upload", data={"exam_id": "e"}, files={"files": ("a.pdf", b"x" * 5000)})
    assert response.status_code == 413


def test_oversized_sheet_is_refused_by_the_upload_route(monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_MB", 0.001)
    monkeypatch.setattr(
        upload_route, "_load_rubric_bundle",
        lambda exam: ([upload_route.QuestionRubric(question_number=1, question_text="q", max_marks=1, criteria=[])], 1.0, "1", "p"),
    )
    response = signed_in_client().post("/upload", data={"exam_id": "e"}, files={"files": ("a.pdf", b"x" * 5000)})
    assert response.status_code == 413 and "a.pdf" in response.text


def test_requirements_are_pinned_and_have_no_local_models():
    lines = [
        line.strip() for line in (BACKEND / "requirements.txt").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert all(re.fullmatch(r"[A-Za-z0-9_.\-\[\]]+==[\w.]+", line) for line in lines), lines
    text = " ".join(lines).lower()
    assert "pytest==" in text
    for name in ("pix2text", "rapidocr", "chromadb", "ollama", "sympy"):
        assert name not in text


def test_secrets_stay_out_of_git():
    ignored = (BACKEND / ".gitignore").read_text()
    for name in (".env", "queue_data/", "gemini_usage/", "firebase-service-account.json"):
        assert name in ignored


def test_page_can_read_the_export_file_name():
    response = signed_in_client().get(
        "/export/none", headers={"Origin": "http://localhost:5173"}
    )
    assert "content-disposition" in response.headers["access-control-expose-headers"].lower()
