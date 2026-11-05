"""Question paper upload, end to end, on the Firestore emulator.

Run through `firebase emulators:exec` (see the command in the project report).
Refuses to run unless Firestore is a local emulator. The model is patched to fail
unless a test supplies a reply, so these tests use no AI and no network.
"""

import copy
import io
import json
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
from services.provider_registry import OllamaModelCapability

ROOT = Path(__file__).resolve().parents[2]
PAPER = ROOT / "test_data" / "mendeley" / "Question.txt"
SCAN = ROOT / "test_data" / "question_paper.pdf"
# Despite its name, this is a 20-mark Social Studies paper, not the Data Science exam.
FOOD_SECURITY_SCAN = ROOT / "test_data" / "mendeley" / "clean_question_paper.pdf"
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


def fake_reply(reply):
    return SimpleNamespace(
        parsed_json=reply, text="", model_used="fake-reader", routing_reason="",
        provider="ollama", task_id="task-1",
    )


def model_returns(monkeypatch, reply):
    monkeypatch.setattr(
        exam_route.model_router,
        "generate",
        lambda *a, **k: fake_reply(reply),
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
    approve = {
        "exam_id": EXAM, "rubric": rubric, "total_marks": 50, "doc_fingerprint": "f",
        "teacher_confirmed": True,
    }

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
        "teacher_confirmed": True,
        "rubric": [{"question_number": 1, "question_text": "a", "max_marks": 1,
                    "criteria": [{"description": "c", "max_marks": 1}]}],
    })
    assert response.status_code == 503


def test_approve_refuses_questions_without_criteria(client, firestore_client, paper_bytes):
    body = upload(client, paper_bytes).json()
    response = client.post("/exam/rubric/approve", json={
        "exam_id": EXAM, "rubric": body["rubric"]["questions"], "total_marks": 50,
        "teacher_confirmed": True,
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


# ---------------------------------------------------------
# Which model reads the paper
# ---------------------------------------------------------

def installed(*names):
    return {
        "online": True,
        "models": [
            OllamaModelCapability(
                name=name, supports_images=True, supports_structured_json=True,
                reasoning_suitable=True, capabilities=("vision",),
            )
            for name in names
        ],
    }


def use_real_router(monkeypatch, models, chat):
    """Undo the fake generate so the real selection code runs. No model is called."""
    monkeypatch.undo()
    monkeypatch.setattr(exam_route.model_router, "routing_policy_fingerprint", lambda: "test")
    monkeypatch.setattr(exam_route.model_router.ollama, "discover_models", lambda **k: models)
    monkeypatch.setattr(exam_route.model_router.ollama, "chat", chat)


def test_setting_is_what_reads_the_scan_through_real_selection(client, monkeypatch, capsys):
    chosen = []

    def chat(model, **kwargs):
        chosen.append(model)
        return "", copy.deepcopy(CLEAN_REPLY), 1

    use_real_router(monkeypatch, installed("gemma4:e4b", "qwen3.5:4b", "other:1b"), chat)
    monkeypatch.setattr(exam_route, "OLLAMA_PAPER_READER_MODEL", "other:1b")

    response = upload(client, pdf_of(scan_page(), scan_page()), name="scan.pdf")
    assert response.status_code == 200, response.text
    assert chosen == ["other:1b"]

    readers = db_service.get_question_paper(EXAM)["page_readers"]
    assert [(r["page"], r["model"], r["provider"]) for r in readers] == [
        (1, "other:1b", "ollama"), (2, "other:1b", "ollama"),
    ]
    logged = capsys.readouterr().out
    assert f"exam={EXAM} page=1/2 read_by=other:1b" in logged
    assert f"exam={EXAM} page=2/2 read_by=other:1b" in logged


def test_default_setting_is_the_complex_model():
    assert config.OLLAMA_PAPER_READER_MODEL == config.OLLAMA_COMPLEX_MODEL


def test_missing_reader_never_falls_back_to_another_local_model(client, firestore_client, monkeypatch):
    called = []

    def chat(model, **kwargs):
        called.append(model)
        raise AssertionError(f"No other model may be used, but {model} was called.")

    use_real_router(monkeypatch, installed("qwen3.5:4b"), chat)
    monkeypatch.setattr(exam_route, "OLLAMA_PAPER_READER_MODEL", "gemma4:e4b")

    response = upload(client, SCAN.read_bytes(), name="scan.pdf")
    assert response.status_code == 503, response.text
    assert called == []
    assert "gemma4:e4b" in response.json()["detail"]
    assert "nothing was saved" in response.json()["detail"]
    assert all_documents(firestore_client) == []


# ---------------------------------------------------------
# Replacing a paper cancels the old approved rubric
# ---------------------------------------------------------

RUBRIC = [{
    "question_number": 1, "question_text": "a", "max_marks": 50,
    "criteria": [{"description": "c", "max_marks": 50}],
}]


def approve(client, fingerprint):
    response = client.post("/exam/rubric/approve", json={
        "exam_id": EXAM, "rubric": RUBRIC, "total_marks": 50, "doc_fingerprint": fingerprint,
        "teacher_confirmed": True,
    })
    assert response.status_code == 200, response.text
    return response.json()


def other_paper(paper_bytes):
    return paper_bytes.replace(b"Define Gradient Descent.", b"Define Backpropagation.")


def test_forced_replacement_cancels_the_approved_rubric(client, firestore_client, paper_bytes):
    first = upload(client, paper_bytes).json()
    rubric_id = approve(client, first["doc_fingerprint"])["rubric_id"]
    assert db_service.get_approved_rubric(EXAM)["rubric_id"] == rubric_id

    response = upload(client, other_paper(paper_bytes), force_regenerate="true")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["invalidated_rubric_ids"] == [rubric_id]
    assert any("cancelled" in w for w in body["rubric"]["extraction_warnings"])

    # Every route that get_approved_rubric can take now finds nothing.
    assert db_service.get_approved_rubric(EXAM) is None
    assert client.get(f"/exam/{EXAM}/rubric").status_code == 404
    exam = firestore_client.collection("exams").document(EXAM).get().to_dict()
    assert exam["status"] == "PROPOSED"
    assert exam["approved_rubric_id"] is None and exam["rubric_version"] is None

    # The old version stays readable for results already graded with it.
    archived = firestore_client.collection("rubrics").document(rubric_id).get().to_dict()
    assert archived["approval_status"] == "SUPERSEDED" and archived["questions"]
    assert "cancelled" not in str(db_service.get_question_paper(EXAM)["extraction_warnings"])


def test_forcing_the_same_file_cancels_nothing(client, firestore_client, paper_bytes):
    first = upload(client, paper_bytes).json()
    rubric_id = approve(client, first["doc_fingerprint"])["rubric_id"]

    body = upload(client, paper_bytes, force_regenerate="true").json()
    assert body["invalidated_rubric_ids"] == []
    assert db_service.get_approved_rubric(EXAM)["rubric_id"] == rubric_id
    exam = firestore_client.collection("exams").document(EXAM).get().to_dict()
    assert exam["status"] == "CONFIGURED"


def test_going_back_to_the_first_paper_needs_a_new_approval(client, firestore_client, paper_bytes):
    first = upload(client, paper_bytes).json()
    approve(client, first["doc_fingerprint"])
    upload(client, other_paper(paper_bytes), force_regenerate="true")

    upload(client, paper_bytes, force_regenerate="true")
    assert db_service.get_approved_rubric(EXAM) is None

    again = approve(client, first["doc_fingerprint"])
    assert again["version"] == 2
    assert db_service.get_approved_rubric(EXAM)["rubric_id"] == again["rubric_id"]


def test_new_paper_cancels_rubric_approved_without_a_saved_paper(client, firestore_client, paper_bytes):
    """A teacher can approve a corrected flagged draft, which leaves no saved paper."""
    rubric_id = approve(client, "fingerprint-of-another-file")["rubric_id"]

    body = upload(client, paper_bytes).json()
    assert body["invalidated_rubric_ids"] == [rubric_id]
    assert db_service.get_approved_rubric(EXAM) is None


# ---------------------------------------------------------
# Textbook uploads are not silently ignored
# ---------------------------------------------------------

def upload_with_textbook(client, content, name="paper.txt", **fields):
    return client.post(
        "/exam/setup",
        data={"exam_id": EXAM, **fields},
        files={"question_paper": (name, content), "textbook": ("book.pdf", b"book")},
    )


def assert_textbook_warning(body):
    assert any("textbook" in warning for warning in body["warnings"])
    assert any("textbook" in warning for warning in body["rubric"]["extraction_warnings"])


def test_textbook_warning_on_new_and_duplicate_uploads(client, paper_bytes):
    assert_textbook_warning(upload_with_textbook(client, paper_bytes).json())
    second = upload_with_textbook(client, paper_bytes).json()
    assert second["duplicate"] is True
    assert_textbook_warning(second)
    assert "textbook" not in str(db_service.get_question_paper(EXAM)["extraction_warnings"])


def test_textbook_warning_on_flagged_draft(client, firestore_client, paper_bytes):
    body = upload_with_textbook(client, paper_bytes.replace(b"(2 Marks Each)", b"")).json()
    assert body["saved"] is False
    assert_textbook_warning(body)
    assert all_documents(firestore_client) == []


def test_textbook_pages_are_not_sent_to_the_model(client, monkeypatch):
    seen = []

    def generate(*args, **kwargs):
        seen.append(len(kwargs["images"]))
        return fake_reply(copy.deepcopy(CLEAN_REPLY))

    monkeypatch.setattr(exam_route.model_router, "generate", generate)
    body = upload_with_textbook(client, SCAN.read_bytes(), name="scan.pdf").json()
    assert seen == [1]
    assert_textbook_warning(body)


def test_no_warning_without_a_textbook(client, paper_bytes):
    body = upload(client, paper_bytes).json()
    assert body["warnings"] == []


# ---------------------------------------------------------
# Teacher confirmation
# ---------------------------------------------------------

def approve_body(fingerprint, **extra):
    return {
        "exam_id": EXAM, "rubric": RUBRIC, "total_marks": 50,
        "doc_fingerprint": fingerprint, **extra,
    }


@pytest.mark.parametrize("extra", [{}, {"teacher_confirmed": False}], ids=["absent", "false"])
def test_approve_without_confirmation_is_refused(client, firestore_client, paper_bytes, extra):
    first = upload(client, paper_bytes).json()
    before = all_documents(firestore_client)

    response = client.post("/exam/rubric/approve", json=approve_body(first["doc_fingerprint"], **extra))
    assert response.status_code == 422
    assert "must confirm" in response.json()["detail"]
    assert all_documents(firestore_client) == before
    assert db_service.get_question_paper(EXAM)["verification_status"] == "UNVERIFIED"
    assert db_service.get_approved_rubric(EXAM) is None


def test_saved_paper_starts_unverified_and_confirmation_marks_it(client, paper_bytes):
    first = upload(client, paper_bytes).json()
    assert db_service.get_question_paper(EXAM)["verification_status"] == "UNVERIFIED"

    approve(client, first["doc_fingerprint"])
    assert db_service.get_question_paper(EXAM)["verification_status"] == "TEACHER_CONFIRMED"
    assert db_service.get_approved_rubric(EXAM)["teacher_confirmed"] is True


def test_rereading_same_file_needs_confirming_again_but_keeps_the_version(client, paper_bytes):
    first = upload(client, paper_bytes).json()
    fingerprint = first["doc_fingerprint"]
    rubric_id = approve(client, fingerprint)["rubric_id"]

    upload(client, paper_bytes, force_regenerate="true")
    assert db_service.get_question_paper(EXAM)["verification_status"] == "UNVERIFIED"

    again = approve(client, fingerprint)
    assert again["rubric_id"] == rubric_id
    assert db_service.get_question_paper(EXAM)["verification_status"] == "TEACHER_CONFIRMED"


def test_rubric_from_another_file_does_not_confirm_the_saved_paper(client, paper_bytes):
    upload(client, paper_bytes)
    approve(client, "fingerprint-of-another-file")
    assert db_service.get_question_paper(EXAM)["verification_status"] == "UNVERIFIED"


# ---------------------------------------------------------
# Figure questions
# ---------------------------------------------------------

def figure_reply(**question_fields):
    reply = copy.deepcopy(CLEAN_REPLY)
    reply["sections"][0]["questions"][1].update(question_fields)
    return reply


def test_figure_question_is_saved_and_listed_for_review(client, monkeypatch):
    model_returns(monkeypatch, figure_reply(
        question_text="Study the figure and explain it.", has_figure=True, figure_page=2,
    ))
    body = upload(client, SCAN.read_bytes(), name="scan.pdf").json()
    assert body["saved"] is True and body["needs_teacher_review"] is False

    stored = db_service.get_question_paper(EXAM)
    question = stored["questions"][1]
    assert question["has_figure"] is True and question["figure_page"] == 2
    assert [item["question"] for item in stored["review_items"]] == ["Q2"]
    assert "page 2" in stored["review_items"][0]["reason"]


def test_wording_check_catches_a_figure_the_model_missed(client, monkeypatch):
    model_returns(monkeypatch, figure_reply(
        question_text="Observe the given graph and analyze it.", has_figure=False,
    ))
    upload(client, SCAN.read_bytes(), name="scan.pdf")
    stored = db_service.get_question_paper(EXAM)
    assert stored["questions"][1]["has_figure"] is True
    assert [item["question"] for item in stored["review_items"]] == ["Q2"]


def test_saved_text_is_exactly_what_the_model_returned(client, monkeypatch):
    text = "Observe the graph (Tamil Nadu 44.5, Punjab 19.8) and analyze it."
    model_returns(monkeypatch, figure_reply(question_text=text, has_figure=True))
    upload(client, SCAN.read_bytes(), name="scan.pdf")
    assert db_service.get_question_paper(EXAM)["questions"][1]["question_text"] == text


def test_identical_wording_is_listed_for_review(client, monkeypatch):
    model_returns(monkeypatch, figure_reply(question_text="First?"))
    body = upload(client, SCAN.read_bytes(), name="scan.pdf").json()
    assert body["saved"] is True
    items = db_service.get_question_paper(EXAM)["review_items"]
    assert [item["question"] for item in items] == ["Q1, Q2"]


def test_dataset_paper_has_nothing_to_review(client, paper_bytes):
    body = upload(client, paper_bytes).json()
    assert body["rubric"]["review_items"] == []
    assert not any(q["has_figure"] for q in body["rubric"]["questions"])


# ---------------------------------------------------------
# Whole flow: Food Security scan with the real local model
# ---------------------------------------------------------

@pytest.mark.skipif(os.getenv("RUN_CLEAN_SCAN") != "1", reason="Set RUN_CLEAN_SCAN=1 to run.")
def test_food_security_scan_whole_flow(client, firestore_client, monkeypatch, capsys):
    """A real two-page scan. Its wording is reported for a person to check, not asserted."""
    monkeypatch.undo()
    assert FOOD_SECURITY_SCAN.is_file(), f"Missing {FOOD_SECURITY_SCAN}"
    data = FOOD_SECURITY_SCAN.read_bytes()

    first = upload(client, data, name="clean_question_paper.pdf")
    assert first.status_code == 200, first.text
    body = first.json()
    rubric = body["rubric"]
    report = {
        "model_setting": config.OLLAMA_PAPER_READER_MODEL,
        "saved": body["saved"],
        "problems": body["problems"],
        "warnings": rubric["extraction_warnings"],
        "review_items": rubric["review_items"],
        "page_readers": rubric.get("page_readers"),
        "questions": rubric["questions"],
    }
    out = os.getenv("CLEAN_SCAN_REPORT")
    if out:
        Path(out).write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")

    # Printed on the paper: MAX.MARKS 20, Q1-8, 9A/9B and 10A/10B.
    read = [f"{q['question_number']}{q.get('sub_part', '')}" for q in rubric["questions"]]
    assert read == [str(n) for n in range(1, 9)] + ["9A", "9B", "10A", "10B"]
    assert [q["max_marks"] for q in rubric["questions"]] == [1] * 4 + [2] * 4 + [4] * 4
    assert rubric["total_marks"] == 20.0 and rubric["declared_maximum_marks"] == 20.0
    assert [q.get("choice_group") for q in rubric["questions"][8:]] == ["Q9", "Q9", "Q10", "Q10"]
    assert not any("Duplicate question number" in w for w in rubric["extraction_warnings"])
    assert body["saved"] is True

    stored = db_service.get_question_paper(EXAM)
    assert stored["verification_status"] == "UNVERIFIED"
    assert stored["questions"][11]["has_figure"] is True

    before = all_documents(firestore_client)
    assert upload(client, data, name="clean_question_paper.pdf").json()["duplicate"] is True
    assert all_documents(firestore_client) == before

    approval = {
        "exam_id": EXAM,
        "rubric": rubric["questions"],
        "total_marks": rubric["total_marks"],
        "doc_fingerprint": body["doc_fingerprint"],
        "gen_fingerprint": body["gen_fingerprint"],
    }
    assert client.post("/exam/rubric/approve", json=approval).status_code == 422
    confirmed = client.post("/exam/rubric/approve", json={**approval, "teacher_confirmed": True})
    assert confirmed.status_code == 200, confirmed.text
    assert db_service.get_question_paper(EXAM)["verification_status"] == "TEACHER_CONFIRMED"
    approved = client.get(f"/exam/{EXAM}/rubric").json()
    assert approved["total_marks"] == 20.0 and len(approved["questions"]) == 12


def test_health_reports_emulator_mode():
    from main import health_check

    assert health_check()["firestore_emulator"] is True
