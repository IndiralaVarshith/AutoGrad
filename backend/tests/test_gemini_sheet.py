"""Answer sheets on the Gemini path. Fake Gemini replies; nothing reaches Google."""

import json
import uuid
from types import SimpleNamespace

import fitz
import pytest

import config
from models.schemas import QuestionRubric
from services.db import db_service
from services.gemini_client import DailyLimitReached, GeminiClient
from services.gemini_sheet import (
    SheetExtraction,
    grade_readings,
    grade_sheet,
    rubric_for_prompt,
    second_opinions,
)


def mcq(number=1, key="B"):
    return QuestionRubric(
        question_number=number, question_text="Pick", max_marks=1, section="A",
        question_format="MCQ", correct_option=key,
        options=[{"label": "A", "text": "x"}, {"label": "B", "text": "y"}],
        criteria=[{"description": f"Selected {key}", "max_marks": 1}],
    )


def written(number=2, marks=2.0, part="", **extra):
    return QuestionRubric(
        question_number=number, sub_part=part, question_text="Explain", max_marks=marks,
        section=extra.pop("section", "A"), question_format="SHORT_ANSWER",
        criteria=[{"description": "Idea", "max_marks": marks}], **extra,
    )


def answer(number=2, part="", text="an answer here", marks=2.0, **extra):
    return {
        "question_number": number, "sub_part": part, "number_source": extra.pop("number_source", "WRITTEN"),
        "read_text": text, "page_number": extra.pop("page", 1), "read_confidence": extra.pop("read", 0.95),
        "marks_awarded": marks, "reason": "Fits the rubric",
        "evidence_quote": extra.pop("quote", text), "grading_confidence": extra.pop("grade", 0.95),
        **extra,
    }


def mcq_answer(number=1, letter="B", read=0.95, **extra):
    return {
        "question_number": number, "number_source": "WRITTEN", "read_text": letter,
        "selected_option": letter, "page_number": 1, "read_confidence": read,
        "marks_awarded": None, **extra,
    }


def reading(*answers, readable=True, unmatched=(), offset=0):
    return (
        SheetExtraction.model_validate({
            "readable": readable, "answers": list(answers), "unmatched": list(unmatched),
        }),
        offset,
    )


def grade_one(rubric, *answers, **kwargs):
    grades, unmapped = grade_readings(rubric, [reading(*answers, **kwargs)], [], "m")
    return {f"{g.question_number}{g.sub_part}": g for g in grades}, unmapped


def test_clean_written_answer_counts():
    grades, _ = grade_one([written()], answer())
    g = grades["2"]
    assert g.routing == "AUTO_ACCEPT" and g.final_score == 2 and not g.flag_reason
    assert g.routing_reason == "Fits the rubric"
    assert g.criteria[0].verbatim_quote == "an answer here" and g.criteria[0].quote_verified


def test_mcq_is_marked_by_code():
    grades, _ = grade_one([mcq(1, "B"), mcq(2, "A")], mcq_answer(1, "b"), mcq_answer(2, "B"))
    assert grades["1"].final_score == 1 and grades["1"].selected_option == "B"
    assert grades["2"].final_score == 0 and grades["2"].routing == "AUTO_ACCEPT"


@pytest.mark.parametrize("letter", ["A,B", "Z", ""])
def test_unclear_mcq_is_flagged_with_no_mark(letter):
    rubric = [mcq()]
    extraction = mcq_answer(1, letter)
    if not letter:
        extraction["unreadable"] = True
    grades, _ = grade_one(rubric, extraction)
    assert grades["1"].routing == "MANUAL" and grades["1"].final_score == 0
    assert grades["1"].suggested_score is None
    assert grades["1"].flag_reason in {"UNCLEAR_OPTION", "UNREADABLE"}


def test_low_confidence_mcq_is_flagged_and_keeps_a_suggestion():
    grades, _ = grade_one([mcq()], mcq_answer(read=0.5))
    g = grades["1"]
    assert g.flag_reason == "LOW_CONFIDENCE" and g.final_score == 0 and g.suggested_score == 1


def test_missing_key_is_flagged():
    grades, _ = grade_one([mcq(key="")], mcq_answer())
    assert grades["1"].flag_reason == "NO_KEY"


def test_unreadable_is_flagged_with_no_mark():
    grades, _ = grade_one([written()], answer(unreadable=True, text="", marks=1.0))
    g = grades["2"]
    assert g.flag_reason == "UNREADABLE" and g.routing == "MANUAL"
    assert g.final_score == 0 and g.suggested_score is None


def test_missing_answer_is_flagged_never_zero_marked_silently():
    grades, _ = grade_one([written()], )
    g = grades["2"]
    assert g.flag_reason == "MISSING" and g.routing == "MANUAL" and g.final_score == 0


def test_low_grading_or_reading_confidence_is_flagged():
    for kwargs in ({"read": 0.4}, {"grade": 0.4}):
        grades, _ = grade_one([written()], answer(**kwargs))
        g = grades["2"]
        assert g.flag_reason == "LOW_CONFIDENCE" and g.final_score == 0 and g.suggested_score == 2


def test_partial_on_high_value_is_flagged_but_not_on_low_value():
    grades, _ = grade_one([written(marks=5)], answer(marks=3.0))
    assert grades["2"].flag_reason == "PARTIAL_HIGH_VALUE" and grades["2"].suggested_score == 3
    grades, _ = grade_one([written(marks=2)], answer(marks=1.0))
    assert grades["2"].routing == "AUTO_ACCEPT" and grades["2"].final_score == 1
    grades, _ = grade_one([written(marks=5)], answer(marks=5.0))
    assert grades["2"].routing == "AUTO_ACCEPT"


@pytest.mark.parametrize("marks", [None, 3.0, -1.0, 0.3])
def test_impossible_marks_are_flagged_never_clipped(marks):
    grades, _ = grade_one([written()], answer(marks=marks))
    g = grades["2"]
    assert g.flag_reason == "INVALID_MARK" and g.final_score == 0 and g.suggested_score is None


def test_evidence_must_be_in_the_answer():
    grades, _ = grade_one([written()], answer(quote="words the student never wrote at all"))
    assert grades["2"].flag_reason == "EVIDENCE_NOT_FOUND" and grades["2"].final_score == 0
    grades, _ = grade_one([written()], answer(quote=""))
    assert grades["2"].flag_reason == "EVIDENCE_NOT_FOUND"
    grades, _ = grade_one([written()], answer(marks=0.0, quote=""))
    assert grades["2"].routing == "AUTO_ACCEPT" and grades["2"].final_score == 0


def test_inferred_number_is_flagged():
    grades, _ = grade_one([written()], answer(number_source="INFERRED"))
    assert grades["2"].flag_reason == "NUMBER_INFERRED" and grades["2"].suggested_score == 2


def test_answer_for_a_question_not_on_the_paper_is_unmatched():
    grades, unmapped = grade_one([written()], answer(number=99))
    assert unmapped[0]["mapping_status"] == "NOT_ON_PAPER" and unmapped[0]["detected_question_number"] == 99
    assert grades["2"].flag_reason == "MISSING"


def test_writing_that_fits_nothing_is_unmatched():
    _, unmapped = grade_one([written()], answer(), unmatched=[{"text": "stray", "page_number": 2}])
    assert unmapped[0]["extracted_text"] == "stray"
    assert unmapped[0]["page_references"] == [{"page_number": 2}]


def test_sub_parts_match_however_they_are_written():
    grades, unmapped = grade_one([written(3, part="a"), written(3, part="b")],
                                 answer(3, part="(a)"), answer(3, part="B."))
    assert not unmapped
    assert grades["3A"].routing == "AUTO_ACCEPT" and grades["3B"].routing == "AUTO_ACCEPT"


def test_or_choice_with_one_side_answered():
    rubric = [written(4, choice_group="g"), written(5, choice_group="g")]
    grades, _ = grade_one(rubric, answer(4))
    assert grades["4"].routing == "AUTO_ACCEPT"
    assert grades["5"].routing == "NOT_ATTEMPTED" and not grades["5"].flag_reason


def test_or_choice_with_both_sides_answered_goes_to_the_teacher():
    rubric = [written(4, choice_group="g"), written(5, choice_group="g")]
    grades, _ = grade_one(rubric, answer(4), answer(5))
    for key in "45":
        assert grades[key].flag_reason == "ATTEMPT_RULE" and grades[key].final_score == 0


def test_or_choice_with_neither_side_answered_is_missing():
    rubric = [written(4, choice_group="g"), written(5, choice_group="g")]
    grades, _ = grade_one(rubric)
    assert grades["4"].flag_reason == "MISSING" and grades["5"].flag_reason == "MISSING"


def test_or_choice_whose_alternatives_have_parts():
    rubric = [
        written(5, part="a", choice_group="g", choice_alternative="5"),
        written(5, part="b", choice_group="g", choice_alternative="5"),
        written(6, part="a", choice_group="g", choice_alternative="6"),
    ]
    grades, _ = grade_one(rubric, answer(5, part="a"))
    assert grades["5A"].routing == "AUTO_ACCEPT"
    assert grades["5B"].flag_reason == "MISSING"  # Chose this side, so its parts are due.
    assert grades["6A"].routing == "NOT_ATTEMPTED"


def test_any_k_of_n():
    rubric = [written(n, section_attempt_count=2) for n in (1, 2, 3)]
    grades, _ = grade_one(rubric, answer(1), answer(3))
    assert grades["1"].routing == grades["3"].routing == "AUTO_ACCEPT"
    assert grades["2"].routing == "NOT_ATTEMPTED"

    grades, _ = grade_one(rubric, answer(1), answer(2), answer(3))
    assert all(g.flag_reason == "ATTEMPT_RULE" for g in grades.values())

    grades, _ = grade_one(rubric, answer(1))
    assert grades["2"].flag_reason == "MISSING" and grades["3"].flag_reason == "MISSING"


def test_unreadable_sheet_flags_everything():
    grades, _ = grade_one([written(), mcq()], readable=False)
    assert all(g.flag_reason == "UNREADABLE" and g.final_score == 0 for g in grades.values())


def test_reply_problems_flag_unread_questions_as_not_read():
    grades, _ = grade_readings([written(), mcq()], [reading(answer())], ["stopped early"], "m")
    by_number = {g.question_number: g for g in grades}
    assert by_number[2].routing == "AUTO_ACCEPT"
    assert by_number[1].flag_reason == "SHEET_NOT_READ"


def test_prompt_never_contains_the_mcq_key():
    text = rubric_for_prompt([mcq(key="B"), written()])
    assert "correct_option" not in text and "Selected B" not in text
    assert "max_marks" in text and "Idea" in text


# -------------------------------------------------------- whole sheet, with fake Gemini

def reply(payload, finish="STOP"):
    return SimpleNamespace(
        text=json.dumps(payload) if isinstance(payload, dict) else payload,
        parsed=None,
        usage_metadata=SimpleNamespace(prompt_token_count=100, candidates_token_count=50, thoughts_token_count=0),
        candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name=finish))],
    )


def pdf(pages):
    document = fitz.open()
    for index in range(pages):
        document.new_page().insert_text((50, 50), f"page {index + 1}")
    return document.tobytes()


class Fake:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, model, contents, generation_config):
        self.calls.append((model, contents))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def client_for(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_RPM_LIMIT", 6000)

    def build(*outcomes):
        fake = Fake(*outcomes)
        return GeminiClient(fake, usage_dir=tmp_path, sleep=lambda _s: None), fake

    return build


def sheet(client, pages=2, rubric=None, run=None):
    return grade_sheet(
        exam_id="e", hall_ticket="s1", student_name="", file_bytes=pdf(pages), extension=".pdf",
        rubric_list=rubric or [mcq(), written()], total_marks=3, client=client, run=run,
    )


def good_reply(*answers):
    return reply({"readable": True, "answers": list(answers)})


def test_clean_sheet_uses_one_request_and_records_usage(client_for):
    client, fake = client_for(good_reply(mcq_answer(), answer()))
    result = sheet(client)
    assert len(fake.calls) == 1
    assert result.total_score == 3 and result.max_total == 3
    assert result.status == "COMPLETE" and result.finality_status == "PENDING_VALIDATION"
    assert result.flagged_count == 0
    assert result.gemini_usage["calls"] == 1 and result.gemini_usage["input_tokens"] == 100
    assert result.gemini_usage["notional_cost_inr"] is not None


def test_flagged_marks_do_not_count_toward_the_total(client_for):
    client, _ = client_for(good_reply(mcq_answer(), answer(read=0.2)))
    result = sheet(client)
    assert result.total_score == 1 and result.flagged_count == 1 and result.status == "NEEDS_REVIEW"


def test_twelve_pages_one_request_thirteen_pages_two(client_for):
    client, fake = client_for(good_reply(mcq_answer(), answer()))
    sheet(client, pages=12)
    assert len(fake.calls) == 1

    client, fake = client_for(
        good_reply(mcq_answer(page=1)),
        good_reply(answer(page=2)),
    )
    result = sheet(client, pages=13)
    assert len(fake.calls) == 2
    assert result.total_score == 3
    by_number = {g.question_number: g for g in result.questions}
    # Page 2 of the second piece, which starts after page 7, is page 9 of the sheet.
    assert by_number[2].source_page_references == [{"page_number": 9}]


def test_answer_crossing_the_split_is_flagged_not_merged(client_for):
    client, _ = client_for(good_reply(mcq_answer(), answer(text="first half")),
                           good_reply(answer(text="second half")))
    result = sheet(client, pages=13)
    g = next(q for q in result.questions if q.question_number == 2)
    assert "DUPLICATE_ANSWER" in g.flag_reason and g.final_score == 0


def test_bad_reply_is_not_sent_again_and_flags_everything(client_for):
    client, fake = client_for(reply("not json"))
    result = sheet(client)
    assert len(fake.calls) == 1
    assert all(q.flag_reason == "SHEET_NOT_READ" and q.final_score == 0 for q in result.questions)
    assert result.status == "NEEDS_REVIEW" and result.sheet_problems


def test_daily_limit_raises_so_the_queue_can_pause(client_for, monkeypatch):
    client, _ = client_for()
    monkeypatch.setattr(client, "generate", lambda *a, **k: (_ for _ in ()).throw(DailyLimitReached("x")))
    with pytest.raises(DailyLimitReached):
        sheet(client)


def test_second_opinion_changes_nothing_and_stops_at_the_cap(client_for):
    flagged = answer(read=0.2)
    client, fake = client_for(
        good_reply(mcq_answer(), flagged, answer(number=3, read=0.2)),
        good_reply(answer(marks=1.0, text="better reading")),
    )
    rubric = [mcq(), written(), written(3)]
    run = client.new_run(review_call_cap=1)
    result = sheet(client, rubric=rubric, run=run)
    assert len(fake.calls) == 1  # Off by default.
    second_opinions(result, pdf(2), ".pdf", rubric, client, run)
    assert len(fake.calls) == 2 and fake.calls[1][0] == config.GEMINI_REVIEW_MODEL
    g2, g3 = (next(q for q in result.questions if q.question_number == n) for n in (2, 3))
    assert g2.second_opinion["marks"] == 1.0 and g2.second_opinion["read_text"] == "better reading"
    assert g2.routing == "FLAGGED" and g2.final_score == 0 and g2.suggested_score == 2
    assert g3.second_opinion is None and g3.routing == "FLAGGED"


def test_queue_job_grades_and_saves_on_the_emulator(client_for, monkeypatch, tmp_path):
    if not db_service.is_connected():
        pytest.skip("The Firestore emulator is not connected.")
    from routes import upload as upload_route
    from services import gemini_sheet
    from services.queue_manager import QueueJob

    client, fake = client_for(good_reply(mcq_answer(), answer()))
    monkeypatch.setattr(gemini_sheet, "get_client", lambda: client)
    monkeypatch.setattr(upload_route.queue_manager, "update_job_stage", lambda *a, **k: None)
    monkeypatch.setattr(config, "GRADING_ENGINE", "gemini")
    exam_id = f"sheet-{uuid.uuid4().hex[:8]}"
    path = tmp_path / "s.pdf"
    path.write_bytes(pdf(2))
    job = QueueJob(
        job_id="j1", exam_id=exam_id, hall_ticket="HT1", student_name="", file_path=str(path),
        file_ext=".pdf", mime_type="application/pdf", status="PROCESSING", rubric_data=[mcq().model_dump(), written().model_dump()], rubric_total_marks=3,
    )
    result = upload_route._execute_grading_job(job)
    assert result.total_score == 3 and len(fake.calls) == 1
    saved = db_service.get_grading_result(exam_id, "HT1")
    assert saved["total_score"] == 3
    assert saved["questions"][1]["criteria"][0]["verbatim_quote"] == "an answer here"
    assert saved["gemini_usage"]["calls"] == 1
