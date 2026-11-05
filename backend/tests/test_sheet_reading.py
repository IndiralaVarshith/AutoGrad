"""A2: reading a sheet, logging the reader model, flagging what cannot be mapped."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import config
import pipeline as pipeline_module
from models.schemas import QuestionRubric, RubricCriterion
from services import ocr as ocr_module
from services.db import db_service
from services.ocr import OCR_PROMPT, extract_text
from tests.test_question_paper_emulator import clean_state, firestore_client  # noqa: F401


def reply(*answers, model="reader-model"):
    return SimpleNamespace(
        text=json.dumps({"answers": [
            {"question_number": q, "sub_part": "", "extracted_text": text, "illegible_regions": []}
            for q, text in answers
        ]}),
        model_used=model,
    )


def rubric(number, marks=2.0):
    return QuestionRubric(
        question_number=number,
        question_text=f"Question {number}",
        max_marks=marks,
        question_type="THEORY",
        question_format="SHORT_ANSWER",
        criteria=[RubricCriterion(description="Point", max_marks=marks)],
    )


def read_pages(replies, source_hash=""):
    readers = []
    with patch.object(ocr_module, "gemini_generate_with_retry", side_effect=replies) as call:
        results, _ = extract_text([b"p1", b"p2"][: len(replies)], source_hash=source_hash, page_readers=readers)
    return results, readers, call


def test_each_page_logs_its_model_and_uses_the_configured_reader():
    results, readers, call = read_pages([reply((1, "a"), model="m1"), reply((2, "b"), model="m2")])
    assert [(r["page"], r["model"], r["status"]) for r in readers] == [(1, "m1", "READ"), (2, "m2", "READ")]
    assert call.call_args.kwargs["local_model"] == config.OLLAMA_SHEET_READER_MODEL
    assert set(results) == {(1, ""), (2, "")}


def test_configured_reader_reaches_the_router():
    with patch("services.router.model_router.generate", return_value=SimpleNamespace(
        text="{}", parsed_json={}, model_used="x", provider="ollama",
    )) as generate, patch.object(config, "AI_PROVIDER", "ollama"):
        config.gemini_generate_with_retry(contents=["prompt"], local_model="chosen:model")
    assert generate.call_args.kwargs["force_model"] == "chosen:model"


def test_garbage_page_is_flagged_and_the_previous_answer_warned():
    bad = SimpleNamespace(text="not json", model_used="m")
    results, readers, _ = read_pages([reply((1, "start of answer")), bad])
    assert readers[1]["status"] == "UNREADABLE"
    assert "may continue" in results[(1, "")]["illegible_regions"][0]


def test_all_pages_garbage_fails_instead_of_returning_nothing():
    bad = SimpleNamespace(text="", model_used="m")
    with patch.object(ocr_module, "gemini_generate_with_retry", return_value=bad):
        with pytest.raises(RuntimeError, match="could be read"):
            extract_text([b"p1"], source_hash="")


def test_partly_read_sheet_is_not_cached(tmp_path):
    bad = SimpleNamespace(text="not json", model_used="m")
    with patch.object(ocr_module, "_OCR_CACHE_DIR", tmp_path):
        read_pages([reply((1, "a")), bad], source_hash="sheet-1")
        assert list(tmp_path.iterdir()) == []


def test_cached_read_still_reports_readers(tmp_path):
    with patch.object(ocr_module, "_OCR_CACHE_DIR", tmp_path):
        read_pages([reply((1, "a"))], source_hash="sheet-2")
        readers = []
        with patch.object(ocr_module, "gemini_generate_with_retry") as call:
            extract_text([b"p1"], source_hash="sheet-2", page_readers=readers)
        call.assert_not_called()
    assert readers[0]["model"] == "reader-model"


def test_prompt_forbids_correcting_and_struck_text():
    assert "Keep spelling" in OCR_PROMPT and "crossed out" in OCR_PROMPT


def process(results, readers, rubrics):
    def fake_extract(*args, page_readers=None, **kwargs):
        page_readers.extend(readers)
        return results, 0.0

    with patch.object(pipeline_module, "extract_page_regions", return_value=[]), \
            patch.object(pipeline_module, "extract_text", fake_extract), \
            patch.object(pipeline_module, "classify_questions", return_value=["THEORY"] * len(rubrics)), \
            patch.object(pipeline_module, "grade_answer", side_effect=AssertionError("must not grade")):
        return pipeline_module.process_paper(
            exam_id="sheet-exam", hall_ticket="S1", student_name="", image_parts=[b"p"],
            rubric_list=rubrics, image_bytes_list=[b"p"],
        )


def answer(text, status="MAPPED", q=0):
    return {"extracted_text": text, "illegible_regions": [], "mapping_status": status,
            "detected_question_number": q, "sub_part": "", "page_references": []}


def test_unknown_question_number_is_flagged_and_saved(firestore_client):
    result = process({(99, ""): answer("stray", q=99)}, [{"page": 1, "model": "m", "status": "READ"}], [rubric(1)])
    assert result.status == "NEEDS_REVIEW"
    assert result.unmapped_answers[0]["mapping_status"] == "NOT_ON_PAPER"
    assert result.questions[0].routing == "MANUAL" and result.questions[0].final_score == 0
    saved = db_service.get_grading_result("sheet-exam", "S1")
    assert saved["page_readers"][0]["model"] == "m"


def test_unreadable_page_means_needs_review(firestore_client):
    result = process({}, [{"page": 1, "model": "m", "status": "UNREADABLE"}], [rubric(1)])
    assert result.status == "NEEDS_REVIEW"


def test_reading_and_grading_cannot_escalate_to_gemini_by_themselves():
    from services.router import NoSuitableModelError, local_only, model_router

    real_generate = type(model_router).generate
    offline = {"online": False, "server_version": None, "models": [], "error": "down"}
    with patch.object(model_router.ollama, "discover_models", return_value=offline), \
            patch.object(model_router, "_gemini_allowed_for_fallback", return_value=True), \
            patch.object(model_router, "_generate_gemini") as gemini:
        with local_only():
            with pytest.raises(NoSuitableModelError):
                real_generate(model_router, task_type="ocr_extraction", prompt="read", format_json=True)
        gemini.assert_not_called()
        real_generate(model_router, task_type="ocr_extraction", prompt="read", format_json=True)
        gemini.assert_called_once()  # outside the scope, existing behaviour is unchanged


def page_image(ink=False):
    import io

    from PIL import Image, ImageDraw

    image = Image.new("L", (1000, 1400), 240)
    draw = ImageDraw.Draw(image)
    draw.line((30, 0, 30, 1400), fill=0, width=6)  # the printed margin
    draw.line((0, 30, 1000, 30), fill=0, width=6)
    if ink:
        for x in range(200, 800, 40):  # short strokes, like handwriting
            draw.rectangle((x, 200, x + 8, 300), fill=20)
    out = io.BytesIO()
    image.save(out, "JPEG")
    return out.getvalue()


def test_show_through_page_is_skipped_and_logged():
    readers = []
    with patch.object(ocr_module, "gemini_generate_with_retry", return_value=reply((1, "a"))) as call:
        results, _ = extract_text([page_image(ink=True), page_image()], source_hash="", page_readers=readers)
    assert call.call_count == 1
    assert [r["status"] for r in readers] == ["READ", "BLANK"]
    assert set(results) == {(1, "")}


def test_junk_sub_parts_are_dropped_and_real_ones_kept():
    assert ocr_module._clean_sub_part("MULTIPLE_CHOICE") == ""
    assert ocr_module._clean_sub_part("SHORT_ANSWER") == ""
    assert ocr_module._clean_sub_part("the whole answer text") == ""
    assert ocr_module._clean_sub_part("(a)") == "A"
    assert ocr_module._clean_sub_part("ii") == "II"


def test_consecutive_pieces_on_a_page_merge_but_pages_do_not():
    results, _, _ = read_pages([reply((25, "first"), (25, "second")), reply((25, "other page"))])
    merged = [v for k, v in results.items() if k == (25, "")][0]
    assert "first\nsecond" in merged["extracted_text"]
    assert merged["mapping_status"] == "AMBIGUOUS_DUPLICATE"


def test_out_of_turn_number_maps_but_a_repeat_does_not():
    results, _, _ = read_pages([reply((27, "late")), reply((22, "early"), (27, "again"))])
    assert results[(22, "")]["mapping_status"] == "MAPPED"
    assert results[(27, "")]["mapping_status"] == "AMBIGUOUS_DUPLICATE"
