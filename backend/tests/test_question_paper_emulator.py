"""Question paper upload, end to end, on the Firestore emulator.

Run through `firebase emulators:exec` (see the command in the project report).
Refuses to run unless Firestore is a local emulator. The model is patched to fail
unless a test supplies a reply, so these tests use no AI and no network.
"""

import io
import os
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageFilter

import config
from routes import exam as exam_route
from services import rubric_cache
from services.db import db_service
from services.marks_validator import validate_and_reconcile_exam_structure

ROOT = Path(__file__).resolve().parents[2]
PAPER = ROOT / "test_data" / "mendeley" / "Question.txt"
SCAN = ROOT / "test_data" / "question_paper.pdf"
EXAM = "test_exam"

CLEAN_REPLY = {
    "declared_maximum_marks": 3,
    "sections": [{
        "section_id": "A",
        "questions": [
            {"question_number": 1, "question_text": "First?", "question_type": "THEORY", "marks": 1,
             "criteria": [{"description": "Correct", "max_marks": 1}]},
            {"question_number": 2, "question_text": "Second?", "question_type": "THEORY", "marks": 2,
             "criteria": [{"description": "Correct", "max_marks": 2}]},
        ],
    }],
}


@pytest.fixture(scope="session")
def firestore_client():
    host = os.environ.get("FIRESTORE_EMULATOR_HOST", "")
    name, _, port = host.partition(":")
    if name not in {"127.0.0.1", "localhost"}:
        pytest.exit(f"Refusing to run: Firestore host '{host}' is not a local emulator.", 2)
    try:
        socket.create_connection((name, int(port)), timeout=2).close()
    except OSError:
        pytest.fail(f"Firestore emulator is not running at {host}.")
    client = config.get_firestore()
    assert client is not None and client.project == config.EMULATOR_PROJECT_ID
    assert client._emulator_host == host
    return client


@pytest.fixture(autouse=True)
def clean_state(firestore_client, monkeypatch):
    host = os.environ["FIRESTORE_EMULATOR_HOST"]
    url = (
        f"http://{host}/emulator/v1/projects/{config.EMULATOR_PROJECT_ID}"
        "/databases/(default)/documents"
    )
    assert requests.delete(url, timeout=10).status_code == 200
    rubric_cache._draft_cache.clear()

    def model_must_not_run(*args, **kwargs):
        raise AssertionError("The model must not be called here.")

    monkeypatch.setattr(exam_route.model_router, "generate", model_must_not_run)
    monkeypatch.setattr(exam_route.model_router, "routing_policy_fingerprint", lambda: "test")


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(exam_route.router)
    return TestClient(app)


def upload(client, content, name="paper.txt", exam_id=EXAM, **fields):
    return client.post(
        "/exam/setup",
        data={"exam_id": exam_id, **fields},
        files={"question_paper": (name, content)},
    )


def model_returns(monkeypatch, reply):
    monkeypatch.setattr(
        exam_route.model_router,
        "generate",
        lambda *a, **k: SimpleNamespace(parsed_json=reply, text="", model_used="fake", routing_reason=""),
    )


def all_documents(firestore_client):
    found = []
    def walk(collections):
        for collection in collections:
            for ref in collection.list_documents():
                snapshot = ref.get()
                if snapshot.exists:
                    found.append(ref.path)
                walk(ref.collections())
    walk(firestore_client.collections())
    return found


def scan_page():
    with fitz.open(SCAN) as document:
        pixmap = document[0].get_pixmap(dpi=150)
    return Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")


def png(image):
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def pdf_of(*images):
    document = fitz.open()
    for image in images:
        page = document.new_page(width=image.width * 0.48, height=image.height * 0.48)
        page.insert_image(page.rect, stream=png(image))
    data = document.tobytes()
    document.close()
    return data


@pytest.fixture(scope="module")
def paper_bytes():
    if not PAPER.is_file():
        pytest.fail(f"Dataset paper not found at {PAPER}. test_data/mendeley is gitignored.")
    return PAPER.read_bytes()


# ---------------------------------------------------------
# Safety
# ---------------------------------------------------------

def test_emulator_mode_never_loads_the_key():
    """Runs in a fresh process so Firebase really starts up with the loaders blocked."""
    script = """
from firebase_admin import credentials
def blocked(*args, **kwargs):
    raise SystemExit("KEY_LOADER_CALLED")
credentials.Certificate = blocked
credentials.ApplicationDefault = blocked
import config
config._resolve_service_account = blocked
client = config.get_firestore()
print("PROJECT=" + client.project)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(config.__file__).parent,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "KEY_LOADER_CALLED" not in result.stderr + result.stdout
    assert f"PROJECT={config.EMULATOR_PROJECT_ID}" in result.stdout, result.stderr


# ---------------------------------------------------------
# Parsed paper is saved correctly
# ---------------------------------------------------------

def test_dataset_paper_is_saved_with_35_questions_and_50_marks(client, firestore_client, paper_bytes):
    response = upload(client, paper_bytes, name="Question.txt")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["saved"] is True and body["duplicate"] is False
    assert body["needs_teacher_review"] is False and body["problems"] == []

    stored = db_service.get_question_paper(EXAM)
    assert stored["question_count"] == 35
    assert len(stored["questions"]) == 35
    assert stored["total_marks"] == 50.0
    assert stored["validation_status"] == "VALID"
    assert stored["declared_maximum_marks"] is None
    assert any("does not print a total" in w for w in stored["extraction_warnings"])
    assert sum(q["max_marks"] for q in stored["questions"]) == 50.0
    assert sum(q["question_format"] == "MCQ" for q in stored["questions"]) == 20
    assert all(q["criteria"] == [] for q in stored["questions"])

    # marks_validator also passes on what was actually stored, not just on the parse.
    check = validate_and_reconcile_exam_structure({
        "questions": stored["questions"], "declared_maximum_marks": 50,
    })
    assert check.validation_status == "VALID"
    assert check.calculated_maximum_marks == 50.0

    exam = firestore_client.collection("exams").document(EXAM).get().to_dict()
    assert exam["status"] == "PROPOSED" and exam["total_marks"] == 50.0
    assert [e["exam_id"] for e in db_service.list_exams()] == [EXAM]


# ---------------------------------------------------------
# No duplicates
# ---------------------------------------------------------

def test_same_paper_twice_creates_no_duplicate(client, firestore_client, paper_bytes):
    first = upload(client, paper_bytes).json()
    documents = all_documents(firestore_client)
    created_at = db_service.get_question_paper(EXAM)["created_at"]

    second = upload(client, paper_bytes)
    assert second.status_code == 200
    assert second.json()["duplicate"] is True and second.json()["saved"] is True
    assert second.json()["rubric"]["questions"] == first["rubric"]["questions"]

    assert all_documents(firestore_client) == documents
    assert len(documents) == 2  # exams/{id} and its question_paper/current
    assert db_service.get_question_paper(EXAM)["created_at"] == created_at


def test_reupload_keeps_approval_and_double_approve_keeps_one_version(client, firestore_client, paper_bytes):
    upload(client, paper_bytes)
    rubric = [{
        "question_number": 1, "question_text": "a", "max_marks": 50,
        "criteria": [{"description": "c", "max_marks": 50}],
    }]
    approve = {"exam_id": EXAM, "rubric": rubric, "total_marks": 50, "doc_fingerprint": "f"}

    first = client.post("/exam/rubric/approve", json=approve)
    second = client.post("/exam/rubric/approve", json=approve)
    assert first.status_code == 200 and second.status_code == 200, second.text
    assert second.json()["rubric_id"] == first.json()["rubric_id"]
    assert len(list(firestore_client.collection("rubrics").stream())) == 1

    assert upload(client, paper_bytes).json()["duplicate"] is True
    exam = firestore_client.collection("exams").document(EXAM).get().to_dict()
    assert exam["status"] == "CONFIGURED"


def test_different_paper_is_refused_and_original_kept(client, paper_bytes):
    upload(client, paper_bytes)
    other = paper_bytes.replace(b"Define Gradient Descent.", b"Define Backpropagation.")

    response = upload(client, other)
    assert response.status_code == 409
    assert "different question paper" in response.json()["detail"]
    assert "Gradient Descent" in str(db_service.get_question_paper(EXAM)["questions"])


def test_force_regenerate_replaces_the_paper(client, firestore_client, paper_bytes):
    upload(client, paper_bytes)
    documents = all_documents(firestore_client)
    other = paper_bytes.replace(b"Define Gradient Descent.", b"Define Backpropagation.")

    response = upload(client, other, force_regenerate="true")
    assert response.status_code == 200 and response.json()["duplicate"] is False
    assert "Backpropagation" in str(db_service.get_question_paper(EXAM)["questions"])
    assert all_documents(firestore_client) == documents


# ---------------------------------------------------------
# Unreadable uploads: clear error, nothing saved
# ---------------------------------------------------------

@pytest.mark.parametrize(
    "make, name, expected",
    [
        (lambda: png(scan_page().filter(ImageFilter.GaussianBlur(8))), "blurry.png", "no text could be read"),
        (lambda: png(Image.new("RGB", (1400, 2000), "white")), "blank.png", "blank"),
        (lambda: b"%PDF-1.7 this is not really a pdf", "corrupt.pdf", "damaged"),
        (lambda: b"not an image", "corrupt.png", "could not be opened"),
        (lambda: b"\xff\xfe\x00\x01\x80", "binary.txt", "not UTF-8"),
        (lambda: b"just some words, no questions", "words.txt", "No numbered questions"),
    ],
    ids=["blurry", "blank", "corrupt-pdf", "corrupt-image", "binary-text", "no-questions"],
)
def test_unreadable_upload_is_rejected_and_nothing_saved(client, firestore_client, make, name, expected):
    response = upload(client, make(), name=name)
    assert response.status_code == 422, response.text
    assert expected in response.json()["detail"]
    assert "nothing was saved" in response.json()["detail"]
    assert all_documents(firestore_client) == []


def test_one_blurred_page_rejects_the_whole_paper(client, firestore_client):
    sharp = scan_page()
    data = pdf_of(sharp, sharp.filter(ImageFilter.GaussianBlur(8)))

    response = upload(client, data, name="two_pages.pdf")
    assert response.status_code == 422
    assert "Page 2" in response.json()["detail"]
    assert "Page 1" not in response.json()["detail"]
    assert all_documents(firestore_client) == []


def test_sharp_scan_passes_the_check_and_reaches_the_model(client, monkeypatch):
    model_returns(monkeypatch, CLEAN_REPLY)
    response = upload(client, SCAN.read_bytes(), name="scan.pdf")
    assert response.status_code == 200, response.text
    assert response.json()["saved"] is True

    stored = db_service.get_question_paper(EXAM)
    assert stored["source_kind"] == "scan"
    assert stored["total_marks"] == 3.0
    assert [q["max_marks"] for q in stored["questions"]] == [1.0, 2.0]


# ---------------------------------------------------------
# Ambiguous papers: flagged for the teacher, nothing saved
# ---------------------------------------------------------

def test_missing_marks_in_text_paper_is_flagged_not_saved(client, firestore_client, paper_bytes):
    response = upload(client, paper_bytes.replace(b"(2 Marks Each)", b""))
    assert response.status_code == 200
    body = response.json()
    assert body["saved"] is False and body["needs_teacher_review"] is True
    assert any("Part II" in problem for problem in body["problems"])
    assert all_documents(firestore_client) == []


@pytest.mark.parametrize(
    "change, expected",
    [
        (lambda q: q.pop("marks"), "Q2"),
        (lambda q: q.update(marks=None), "Q2"),
        (lambda q: q.update(marks="two"), "Q2"),
        (lambda q: q.pop("question_number"), "no question number"),
        (lambda q: q.update(question_number=1), "more than once"),
        (lambda q: q.update(question_text=""), "text was not read"),
    ],
    ids=["no-marks", "null-marks", "word-marks", "no-number", "duplicate-number", "no-text"],
)
def test_ambiguous_model_reply_is_flagged_not_saved(client, firestore_client, monkeypatch, change, expected):
    import copy
    reply = copy.deepcopy(CLEAN_REPLY)
    change(reply["sections"][0]["questions"][1])
    model_returns(monkeypatch, reply)

    response = upload(client, SCAN.read_bytes(), name="scan.pdf")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["saved"] is False and body["needs_teacher_review"] is True
    assert any(expected in problem for problem in body["problems"]), body["problems"]
    assert all_documents(firestore_client) == []


def test_real_shape_missing_section_question_is_flagged_not_saved(client, firestore_client, monkeypatch):
    """The shape the local model really returned for the repo's scan."""
    model_returns(monkeypatch, {
        "declared_maximum_marks": 4,
        "sections": [{
            "section_id": "III",
            "attempt_instruction": "III. ANSWER ANY - TWO OF THE FOLLOWING: 2x4=8M",
            "section_maximum_marks": 4,
            "questions": [{
                "question_number": 9, "question_text": "First? (OR) Second?", "marks": 4,
                "criteria": [{"description": "c", "max_marks": 4}],
            }],
        }],
    })
    body = upload(client, SCAN.read_bytes(), name="scan.pdf").json()
    assert body["saved"] is False and body["needs_teacher_review"] is True
    assert any("requires 2 answers" in problem for problem in body["problems"])
    assert any("merged" in problem for problem in body["problems"])
    assert all_documents(firestore_client) == []


def test_total_mismatch_in_model_reply_is_flagged_not_saved(client, firestore_client, monkeypatch):
    model_returns(monkeypatch, {**CLEAN_REPLY, "declared_maximum_marks": 20})
    body = upload(client, SCAN.read_bytes(), name="scan.pdf").json()
    assert body["saved"] is False and body["needs_teacher_review"] is True
    assert all_documents(firestore_client) == []


@pytest.mark.parametrize(
    "reply",
    [None, "garbage", {}, {"questions": []}, {"sections": ["garbage"]}, {"questions": ["garbage"]}],
    ids=["none", "string", "empty", "no-questions", "bad-section", "bad-question"],
)
def test_garbage_model_reply_is_rejected_and_nothing_saved(client, firestore_client, monkeypatch, reply):
    model_returns(monkeypatch, reply)
    response = upload(client, SCAN.read_bytes(), name="scan.pdf")
    assert response.status_code == 422, response.text
    assert "nothing was saved" in response.json()["detail"]
    assert all_documents(firestore_client) == []


# ---------------------------------------------------------
# No false success
# ---------------------------------------------------------

def test_firestore_offline_is_an_error_not_a_success(client, monkeypatch, paper_bytes):
    monkeypatch.setattr(type(db_service), "is_connected", lambda self: False)
    assert upload(client, paper_bytes).status_code == 503

    response = client.post("/exam/rubric/approve", json={
        "exam_id": EXAM,
        "rubric": [{"question_number": 1, "question_text": "a", "max_marks": 1,
                    "criteria": [{"description": "c", "max_marks": 1}]}],
    })
    assert response.status_code == 503


def test_approve_refuses_questions_without_criteria(client, firestore_client, paper_bytes):
    body = upload(client, paper_bytes).json()
    response = client.post("/exam/rubric/approve", json={
        "exam_id": EXAM, "rubric": body["rubric"]["questions"], "total_marks": 50,
    })
    assert response.status_code == 422
    assert "no marking criteria" in response.json()["detail"]
    assert list(firestore_client.collection("rubrics").stream()) == []


# ---------------------------------------------------------
# Optional: the scan path with the real local model
# ---------------------------------------------------------

@pytest.mark.skipif(os.getenv("RUN_OLLAMA_SMOKE") != "1", reason="Set RUN_OLLAMA_SMOKE=1 to run.")
def test_real_scan_with_local_model(client, firestore_client, monkeypatch):
    monkeypatch.undo()
    response = upload(client, SCAN.read_bytes(), name="scan.pdf")
    print("\nOLLAMA_SMOKE status:", response.status_code)
    if response.status_code != 200:
        print("OLLAMA_SMOKE detail:", response.json().get("detail"))
        assert all_documents(firestore_client) == []
        return

    body = response.json()
    rubric = body["rubric"]
    print("OLLAMA_SMOKE saved:", body["saved"], "total:", rubric["total_marks"],
          "questions:", len(rubric["questions"]), "declared:", rubric["declared_maximum_marks"],
          "problem count:", len(body["problems"]))
    print("OLLAMA_SMOKE problems:", body["problems"])
    # This scan prints "any two" in Section III but shows only one question group.
    assert body["saved"] is False and body["needs_teacher_review"] is True
    assert all_documents(firestore_client) == []
