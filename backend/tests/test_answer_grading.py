"""A3: grading each answer, flagging low confidence, and capping Gemini."""

from __future__ import annotations

import io
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import pipeline as pipeline_module
from models.schemas import (
    CriterionResult,
    CriticVerdict,
    OCRPageResult,
    OCRRegion,
    QuestionRubric,
    RubricCriterion,
)
from routes import review as review_route
from services import resolver as resolver_module
from services.confidence_engine import route_answer
from services.router import NoSuitableModelError, RouterResponse, model_router
from tests.test_question_paper_emulator import clean_state, firestore_client  # noqa: F401

OPTIONS = [{"label": "A", "text": "x"}, {"label": "B", "text": "y"}]


def mcq(number=1, key="B"):
    return QuestionRubric(
        question_number=number, question_text="Pick", max_marks=1.0, question_type="THEORY",
        question_format="MCQ", options=OPTIONS, correct_option=key,
        criteria=[RubricCriterion(description=f"Selected option {key}", max_marks=1.0)],
    )


def short(number=2):
    return QuestionRubric(
        question_number=number, question_text="Explain", max_marks=2.0, question_type="THEORY",
        question_format="SHORT_ANSWER",
        criteria=[RubricCriterion(description="The point", max_marks=2.0)],
    )


def read(text, illegible=None):
    return {"extracted_text": text, "illegible_regions": illegible or [], "mapping_status": "MAPPED",
            "page_references": []}


def awarded(marks=1.5, region_ids=None):
    return [CriterionResult(criterion="The point", verbatim_quote="the answer", marks_awarded=marks,
                            rationale="ok", quote_verified=True, evidence_region_ids=region_ids or [])]


def run(answers, rubrics, *, grader=None, critic=None, evidence=1.0, pages=None, images=None):
    grader = grader or (lambda **_: (awarded(), 1.5, "local-model", "test"))
    critic = critic or (lambda **_: CriticVerdict(verdict="ACCEPT", suggested_score=1.5))
    criteria = awarded()
    with patch.object(pipeline_module, "extract_page_regions", return_value=pages or []), \
            patch.object(pipeline_module, "extract_text", return_value=(answers, 0.0)), \
            patch.object(pipeline_module, "classify_questions", return_value=["THEORY"] * len(rubrics)), \
            patch.object(pipeline_module, "grade_answer", side_effect=grader), \
            patch.object(pipeline_module, "critique_grade", side_effect=critic), \
            patch.object(pipeline_module, "verify_evidence",
                         side_effect=lambda criteria=None, ocr_transcript=None, **_: (criteria or [], evidence)):
        return pipeline_module.process_paper(
            exam_id="grade-exam", hall_ticket="S1", student_name="", image_parts=images or [b"p"],
            rubric_list=rubrics, image_bytes_list=[b"p"],
        )


@pytest.mark.parametrize("text,selected,score,routing", [
    ("B", "B", 1.0, "AUTO_ACCEPT"),
    ("(b).", "B", 1.0, "AUTO_ACCEPT"),
    ("A", "A", 0.0, "AUTO_ACCEPT"),
    ("A and B", "", 0.0, "MANUAL"),
    ("Z", "", 0.0, "MANUAL"),
])
def test_mcq_is_marked_by_code_without_a_model(firestore_client, text, selected, score, routing):
    grader = lambda **_: pytest.fail("MCQ must not reach the grader")  # noqa: E731
    question = run({(1, ""): read(text)}, [mcq()], grader=grader).questions[0]
    assert (question.selected_option, question.final_score, question.routing) == (selected, score, routing)


def test_uncertain_mcq_reading_is_not_marked(firestore_client):
    question = run({(1, ""): read("B", ["letter overwritten"])}, [mcq()]).questions[0]
    assert question.routing == "MANUAL" and question.final_score == 0


def test_blank_and_missing_answers_are_told_apart_and_never_marked(firestore_client):
    result = run({(1, ""): read("")}, [mcq(1), short(2)])
    assert [q.flag_reason for q in result.questions] == ["BLANK_ANSWER", "NO_ANSWER_FOUND"]
    assert result.total_score == 0 and result.status == "NEEDS_REVIEW"


@pytest.mark.parametrize("kwargs,expected", [
    ({}, ("AUTO_ACCEPT", "")),
    ({"critic_verdict": "REJECT"}, ("FLAGGED", "CRITIC_DISAGREED")),
    ({"force_flag": True}, ("FLAGGED", "CRITIC_DISAGREED")),
    ({"critic_score": 0.5}, ("FLAGGED", "SCORE_GAP")),
    ({"c_evid": 0.5}, ("FLAGGED", "EVIDENCE_NOT_VERIFIED")),
    ({"illegible_count": 1}, ("MANUAL", "UNCLEAR_READING")),
    ({"math_unverified": True}, ("MANUAL", "MATH_NOT_VERIFIED")),
    ({"grader_score": 0.0, "critic_score": 0.0}, ("FLAGGED", "ZERO_ON_WRITTEN_ANSWER")),
])
def test_routing_rules(kwargs, expected):
    base = {"grader_score": 1.5, "critic_score": 1.5, "critic_verdict": "ACCEPT", "c_evid": 1.0}
    assert route_answer(**{**base, **kwargs}) == expected


def test_flagged_draft_does_not_count_toward_the_total(firestore_client):
    critic = lambda **_: CriticVerdict(verdict="ACCEPT", suggested_score=0.0)  # noqa: E731
    result = run({(1, ""): read("B"), (2, ""): read("the answer")}, [mcq(1), short(2)], critic=critic)
    flagged = result.questions[1]
    assert flagged.routing == "FLAGGED" and flagged.final_score == 0 and flagged.suggested_score == 1.5
    assert result.total_score == 1.0


def test_accepted_short_answer_counts(firestore_client):
    result = run({(2, ""): read("the answer")}, [short(2)])
    assert result.questions[0].routing == "AUTO_ACCEPT" and result.total_score == 1.5
    assert result.questions[0].suggested_score is None


@pytest.mark.parametrize("failing", ["grader", "critic"])
def test_a_failed_grade_flags_one_answer_and_keeps_the_sheet(firestore_client, failing):
    def boom(**_):
        raise ValueError("bad JSON")

    kwargs = {failing: boom}
    result = run({(1, ""): read("B"), (2, ""): read("the answer")}, [mcq(1), short(2)], **kwargs)
    assert result.questions[0].final_score == 1.0
    assert result.questions[1].flag_reason == "GRADING_FAILED"
    assert result.questions[1].routing == "MANUAL"


def test_resolver_flags_instead_of_crashing_when_no_model_is_left():
    with patch.object(resolver_module, "grade_answer", side_effect=NoSuitableModelError("none")):
        _, score, force_flag = resolver_module.resolve(
            question_text="q", extracted_text="a", image_parts=[], rubric=short(), max_marks=2.0,
            original_criteria=awarded(), original_score=1.5, avoid_models=["a", "b"],
        )
    assert force_flag is True and score == 1.5


def test_confirming_a_flagged_answer_turns_the_draft_into_a_mark(firestore_client):
    critic = lambda **_: CriticVerdict(verdict="REJECT", suggested_score=0.0)  # noqa: E731
    with patch.object(pipeline_module, "resolve", return_value=(awarded(), 1.5, True)):
        run({(2, ""): read("the answer")}, [short(2)], critic=critic)
    app = FastAPI()
    app.include_router(review_route.router)
    reply = TestClient(app).post("/review/confirm", json={
        "exam_id": "grade-exam", "hall_ticket": "S1", "question_number": 2, "sub_part": "",
    })
    assert reply.status_code == 200, reply.text
    from services.db import db_service

    saved = db_service.get_grading_result("grade-exam", "S1")
    assert saved["questions"][0]["final_score"] == 1.5 and saved["total_score"] == 1.5


# ---------- Gemini ----------

def page_with_region():
    image = Image.new("RGB", (500, 500), "white")
    out = io.BytesIO()
    image.save(out, format="PNG")
    page = OCRPageResult(page_number=1, width=500, height=500, status="PROCESSED", regions=[
        OCRRegion(region_id="r1", page_number=1, bbox=[0.1, 0.5, 0.9, 0.7], extracted_text="the answer"),
        OCRRegion(region_id="head", page_number=1, bbox=[0.1, 0.02, 0.9, 0.08], extracted_text="Name"),
    ])
    return page, out.getvalue()


def gemini_reply(score):
    return RouterResponse(text="{}", model_used="gemini", parsed_json={
        "transcription": "the answer", "addresses_question": True, "evidence_supported": True,
        "missed_relevant_content": False, "suggested_score": score, "reason": "ok",
    })


def run_with_gemini(score, critic_score=0.0, calls_left=20):
    page, image = page_with_region()
    critic = lambda **_: CriticVerdict(verdict="ACCEPT", suggested_score=critic_score)  # noqa: E731
    grader = lambda **_: (awarded(region_ids=["r1", "head"]), 1.5, "local-model", "test")  # noqa: E731
    with patch.dict("os.environ", {"GEMINI_VERIFICATION_ENABLED": "true"}), \
            patch.object(model_router, "gemini_calls_left", return_value=calls_left), \
            patch.object(model_router, "generate_gemini_verification",
                         return_value=gemini_reply(score)) as verify:
        result = run({(2, ""): read("the answer")}, [short(2)], grader=grader, critic=critic,
                     pages=[page], images=[image])
    return result.questions[0], verify


def test_gemini_agreement_clears_a_flag_and_keeps_the_local_mark(firestore_client):
    question, verify = run_with_gemini(1.5)
    verify.assert_called_once()
    assert question.local_routing == "FLAGGED" and question.routing == "AUTO_ACCEPT"
    assert question.final_score == 1.5


def test_gemini_disagreement_stays_with_the_teacher(firestore_client):
    question, _ = run_with_gemini(0.0)
    assert question.routing == "MANUAL" and question.final_score == 0


def test_gemini_is_not_called_for_accepted_answers(firestore_client):
    question, verify = run_with_gemini(1.5, critic_score=1.5)
    verify.assert_not_called()
    assert question.routing == "AUTO_ACCEPT"


def test_gemini_is_skipped_when_the_run_limit_is_used(firestore_client):
    question, verify = run_with_gemini(1.5, calls_left=0)
    verify.assert_not_called()
    assert question.verification_status == "SKIPPED_RUN_LIMIT_REACHED"
    assert question.routing == "FLAGGED"


def test_gemini_crops_never_include_the_page_header():
    page, image = page_with_region()
    assert len(pipeline_module._crop_evidence_regions([page], [image], ["head"])) == 0
    assert len(pipeline_module._crop_evidence_regions([page], [image], ["r1"])) == 1


def test_crop_filter_finds_a_low_header_line_on_any_page():
    from PIL import ImageDraw

    image = Image.new("RGB", (500, 500), "white")
    ImageDraw.Draw(image).line((0, 125, 500, 125), fill="black", width=3)  # header ends at 25%
    out = io.BytesIO()
    image.save(out, format="PNG")
    page = OCRPageResult(page_number=3, width=500, height=500, status="PROCESSED", regions=[
        OCRRegion(region_id="name", page_number=3, bbox=[0.1, 0.15, 0.9, 0.2], extracted_text="Name"),
        OCRRegion(region_id="answer", page_number=3, bbox=[0.1, 0.5, 0.9, 0.6], extracted_text="answer"),
    ])
    pages = [b"", b"", out.getvalue()]
    assert pipeline_module._crop_evidence_regions([page], pages, ["name"]) == []
    assert len(pipeline_module._crop_evidence_regions([page], pages, ["answer"])) == 1


def test_router_stops_at_the_cap():
    def fake_gemini(**_):
        return gemini_reply(1.0)

    model_router.start_gemini_run()
    with patch.dict("os.environ", {"GEMINI_VERIFICATION_ENABLED": "true", "GEMINI_MAX_CALLS_PER_RUN": "2"}), \
            patch.object(model_router, "_gemini_allowed_for_fallback", return_value=True), \
            patch.object(model_router, "_generate_gemini", side_effect=fake_gemini) as call:
        model_router.generate_gemini_verification(task_id="1", prompt="p")
        model_router.generate_gemini_verification(task_id="2", prompt="p")
        with pytest.raises(NoSuitableModelError, match="limit"):
            model_router.generate_gemini_verification(task_id="3", prompt="p")
    assert call.call_count == 2
    model_router.start_gemini_run()


def test_tests_run_with_gemini_off():
    import os

    assert os.environ["GEMINI_ENABLED"] == "false"
    assert os.environ["GEMINI_FALLBACK_ENABLED"] == "false"
    assert os.environ["GEMINI_VERIFICATION_ENABLED"] == "false"


@pytest.mark.parametrize("avoid,expected", [(None, "grader-model"), ("grader-model", None)])
def test_grader_uses_the_configured_model_unless_excluded(monkeypatch, avoid, expected):
    import services.grading_agent as agent
    from models.schemas import QuestionRubric

    seen = {}

    class Reply:
        text = ""
        parsed_json = {"criterion_results": [], "total_score": 0}
        model_used = "m"
        routing_reason = "r"

    def generate(**kwargs):
        seen.update(kwargs)
        return Reply()

    monkeypatch.setattr(agent, "OLLAMA_GRADER_MODEL", "grader-model")
    monkeypatch.setattr(agent, "AI_PROVIDER", "ollama")
    monkeypatch.setattr(agent.model_router, "generate", generate)
    rubric = QuestionRubric(
        question_number=21, question_text="Q", max_marks=2.0,
        criteria=[{"description": "idea", "max_marks": 2.0}],
    )
    agent.grade_answer("Q", "text", [], rubric, 2.0, avoid_model=avoid)
    assert seen["force_model"] == expected
