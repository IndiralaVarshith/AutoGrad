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
    MCQ_INSTRUCTIONS,
    McqSheet,
    SheetExtraction,
    sheet_instructions,
    grade_readings,
    grade_sheet,
    rubric_for_prompt,
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
    assert g.flag_reason == "LOW_CONFIDENCE" and g.final_score == 1 and g.suggested_score == 1


def test_missing_key_is_flagged():
    grades, _ = grade_one([mcq(key="")], mcq_answer())
    assert grades["1"].flag_reason == "NO_KEY"


def test_unreadable_is_flagged_with_no_mark():
    grades, _ = grade_one([written()], answer(unreadable=True, text="", marks=1.0))
    g = grades["2"]
    assert g.flag_reason == "UNREADABLE" and g.routing == "MANUAL"
    assert g.final_score == 0 and g.suggested_score is None


def test_missing_answer_gets_zero_and_can_go_to_the_teacher_instead(monkeypatch):
    g = grade_one([written()])[0]["2"]
    assert g.blank and g.routing == "AUTO_ACCEPT" and g.final_score == 0 and not g.flag_reason
    monkeypatch.setattr(config, "GEMINI_FLAG_BLANKS", True)
    g = grade_one([written()])[0]["2"]
    assert g.flag_reason == "MISSING" and g.routing == "MANUAL" and g.final_score == 0


def test_low_grading_or_reading_confidence_is_flagged():
    for kwargs in ({"read": 0.4}, {"grade": 0.4}):
        grades, _ = grade_one([written()], answer(**kwargs))
        g = grades["2"]
        assert g.flag_reason == "LOW_CONFIDENCE" and g.routing == "FLAGGED"
        assert g.final_score == 2 and g.suggested_score == 2  # Counted, as a suggestion.


def test_by_default_partial_marks_and_zeros_stand_without_a_teacher():
    grades, _ = grade_one([written(marks=2)], answer(marks=1.0))
    assert grades["2"].routing == "AUTO_ACCEPT" and grades["2"].final_score == 1
    grades, _ = grade_one([written(marks=2)], answer(marks=0.0, quote=""))
    assert grades["2"].routing == "AUTO_ACCEPT" and grades["2"].final_score == 0
    grades, _ = grade_one([written(marks=2)], answer(marks=2.0))
    assert grades["2"].routing == "AUTO_ACCEPT" and grades["2"].final_score == 2


def test_zero_on_a_written_answer_can_still_go_to_the_teacher(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_FLAG_ZERO_ON_WRITTEN", True)
    grades, _ = grade_one([written(marks=2)], answer(marks=0.0, quote=""))
    assert grades["2"].flag_reason == "ZERO_ON_WRITTEN" and grades["2"].suggested_score == 0


def pointed(number=2, *maxima):
    return QuestionRubric(
        question_number=number, question_text="Explain", max_marks=sum(maxima), section="A",
        question_format="LONG_ANSWER",
        criteria=[{"description": f"Point {i}", "max_marks": m} for i, m in enumerate(maxima, 1)],
    )


def test_code_adds_up_the_points_and_ignores_the_models_own_total():
    text = "definition here. [diagram: a cell with nucleus labelled]"
    points = [{"n": 1, "marks": 1, "quote": "definition here"}, {"n": 2, "marks": 0, "quote": ""},
              {"n": 3, "marks": 1.5, "quote": "nucleus labelled"}]
    g = grade_one([pointed(2, 1, 1, 2)], answer(text=text, marks=4.0, points=points))[0]["2"]
    assert g.routing == "AUTO_ACCEPT" and g.final_score == 2.5
    assert [c.marks_awarded for c in g.criteria] == [1, 0, 1.5]
    assert [c.criterion for c in g.criteria] == ["Point 1", "Point 2", "Point 3"]
    assert g.criteria[2].quote_verified and not g.criteria[1].quote_verified


def test_a_point_left_out_earns_nothing():
    points = [{"n": 2, "marks": 1, "quote": "an answer"}]
    g = grade_one([pointed(2, 1, 1)], answer(points=points))[0]["2"]
    assert g.final_score == 1 and g.routing == "AUTO_ACCEPT"


@pytest.mark.parametrize("points", [
    [{"n": 1, "marks": 2, "quote": "an answer"}],      # More than the point is worth.
    [{"n": 3, "marks": 1, "quote": "an answer"}],      # Not a point of this question.
    [{"n": 1, "marks": 0.3, "quote": "an answer"}],    # Not a half-mark step.
    [{"n": 1, "marks": 1, "quote": "an answer"}, {"n": 1, "marks": 1, "quote": "an answer"}],
])
def test_impossible_point_marks_are_flagged_never_clipped(points):
    g = grade_one([pointed(2, 1, 1)], answer(points=points))[0]["2"]
    assert g.flag_reason == "INVALID_MARK" and g.final_score == 0 and g.suggested_score is None


def test_there_is_no_negative_marking_even_if_gemini_sends_deductions():
    # An older reply shape: a point marked 0, then the same mistake "taken off" again.
    points = [{"n": 1, "marks": 1, "quote": "an answer"}, {"n": 2, "marks": 0, "quote": ""}]
    deductions = [{"reason": "Wrong condition", "marks": 1, "quote": "an answer"}]
    g = grade_one([pointed(2, 1, 1)], answer(points=points, deductions=deductions))[0]["2"]
    assert g.final_score == 1.0 and g.routing == "AUTO_ACCEPT"
    assert [c.marks_awarded for c in g.criteria] == [1, 0]
    assert not any("Taken off" in c.criterion for c in g.criteria)


def test_gemini_is_not_asked_for_deductions():
    from services.gemini_sheet import SheetAnswer
    assert "deductions" not in SheetAnswer.model_fields
    for style in ("GENEROUS", "BALANCED", "STRICT"):
        text = sheet_instructions([written()], style)
        assert "There is no negative marking" in text and "deductions" not in text


def long_question(marks=4.0, text="Explain the causes of the French Revolution"):
    return QuestionRubric(
        question_number=2, question_text=text, max_marks=marks, section="A",
        question_format="LONG_ANSWER", criteria=[{"description": "Causes", "max_marks": marks}],
    )


def words(count):
    return " ".join(["reason"] * count)


@pytest.fixture
def length_on(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_WORDS_PER_MARK", 20)


def test_a_one_line_answer_to_a_four_mark_question_cannot_earn_full_marks(length_on):
    reply = answer(text=words(12), marks=4.0, quote=words(12), length="FULL")
    g = grade_one([long_question()], reply)[0]["2"]
    # 12 of about 80 words: far too short, so a generous marker gives at most half.
    assert g.final_score == 2.0 and g.routing == "AUTO_ACCEPT"
    # The cap lowers the point itself: the rows add up to the mark and none is negative.
    assert [c.marks_awarded for c in g.criteria] == [2.0] and "far shorter than" in g.criteria[0].rationale


def test_a_length_cap_lowers_the_last_points_first_and_never_below_zero(length_on):
    text = words(12)
    points = [{"n": 1, "marks": 2, "quote": text}, {"n": 2, "marks": 1, "quote": text}, {"n": 3, "marks": 1, "quote": text}]
    g = grade_one([pointed(2, 2, 1, 1)], answer(text=text, marks=4.0, points=points))[0]["2"]
    assert g.final_score == 2.0 and [c.marks_awarded for c in g.criteria] == [2, 0, 0]


@pytest.mark.parametrize("style,length,cap", [
    ("GENEROUS", "SHORT", 4.0), ("GENEROUS", "HALF", 3.0),
    ("BALANCED", "SHORT", 3.0), ("BALANCED", "HALF", 2.0), ("BALANCED", "VERY_SHORT", 1.0),
    ("STRICT", "SHORT", 2.0), ("STRICT", "HALF", 2.0), ("STRICT", "VERY_SHORT", 1.0),
])
def test_geminis_length_judgement_caps_marks_by_style(length_on, style, length, cap):
    # 70 of about 80 words: the count does not prove it full, so Gemini's judgement decides.
    reply = answer(text=words(70), marks=4.0, quote=words(70), length=length)
    readings = [(SheetExtraction.model_validate({"readable": True, "answers": [reply]}), 0)]
    grades, _ = grade_readings([long_question()], readings, [], "m", style)
    assert grades[0].final_score == cap


@pytest.mark.parametrize("style", ["GENEROUS", "BALANCED", "STRICT"])
def test_an_answer_as_long_as_its_marks_need_is_never_cut_for_length(length_on, style):
    # Found on a real sheet: 44 to 66 words for a 2 mark answer, called SHORT, halved under Strict.
    reply = answer(number=2, text=words(50), marks=2.0, quote=words(50), length="SHORT")
    readings = [(SheetExtraction.model_validate({"readable": True, "answers": [reply]}), 0)]
    grades, _ = grade_readings([long_question(marks=2.0)], readings, [], "m", style)
    assert grades[0].final_score == 2.0


@pytest.mark.parametrize("question,text", [
    # Found on real maths sheets: a full proof is few words, so it was capped at half.
    ("Show that the points A(-1,-2), B(4,3), C(2,5) and D(-3,0) form a rectangle",
     "AB = sqrt((4+1)^2+(3+2)^2) = sqrt(50) BC = sqrt(8) CD = sqrt(50) AD = sqrt(8) "
     "AC = sqrt(58) BD = sqrt(58) opposite sides and diagonals are equal so ABCD is a rectangle"),
    ("Work out the area of the triangle with base 6 and height 4",
     "A = 1/2 x b x h = 1/2 x 6 x 4 = 12 sq units"),
])
def test_working_in_symbols_is_not_cut_for_length(length_on, question, text):
    reply = answer(text=text, marks=4.0, quote=text, length="NOT_APPLICABLE")
    assert grade_one([long_question(text=question)], reply)[0]["2"].final_score == 4.0


def test_a_full_length_answer_keeps_its_marks(length_on):
    reply = answer(text=words(85), marks=4.0, quote=words(85), length="FULL")
    assert grade_one([long_question()], reply)[0]["2"].final_score == 4.0


@pytest.mark.parametrize("question,text", [
    ("Calculate the area of the circle", "A = 3.14 x 7 x 7 = 153.86 sq cm"),
    ("Locate Delhi on the map", "Delhi marked"),
    ("Define photosynthesis", "Plants make food using sunlight"),
    ("Explain the water cycle", "Evaporation [diagram: water cycle with labelled arrows]"),
])
def test_brief_answers_are_fine_for_sums_maps_definitions_and_drawings(length_on, question, text):
    reply = answer(text=text, marks=4.0, quote=text, length="NOT_APPLICABLE")
    assert grade_one([long_question(text=question)], reply)[0]["2"].final_score == 4.0


def test_the_word_count_backs_up_gemini_when_it_skips_the_length(length_on):
    reply = answer(text=words(15), marks=4.0, quote=words(15), length="NOT_APPLICABLE")
    assert grade_one([long_question()], reply)[0]["2"].final_score == 2.0


def test_low_marks_are_not_raised_and_one_mark_questions_are_not_checked(length_on):
    reply = answer(text=words(5), marks=1.0, quote=words(5), length="VERY_SHORT")
    assert grade_one([long_question()], reply)[0]["2"].final_score == 1.0
    one = answer(text="yes", marks=1.0, quote="yes", length="VERY_SHORT")
    assert grade_one([long_question(marks=1.0)], one)[0]["2"].final_score == 1.0


def test_the_rubric_tells_gemini_how_long_a_full_answer_is(length_on):
    sent = json.loads(rubric_for_prompt([long_question(), long_question(marks=1.0)]))
    assert sent[0]["expected_words"] == 80 and "expected_words" not in sent[1]


def test_a_quote_that_joins_two_phrases_with_dots_is_found():
    # Found on real sheets: both phrases were in the answer, but the joined quote was not.
    text = "1) since 1989 governments were coalitions. 2) parties came together. 7) Many coalitions did not last their full time."
    for joined in ("since 1989 governments were coalitions... Many coalitions did not last their full time",
                   "since 1989 governments were coalitions … Many coalitions did not last"):
        points = [{"n": 1, "marks": 2, "quote": joined}]
        g = grade_one([pointed(2, 2)], answer(text=text, points=points))[0]["2"]
        assert g.flag_reason == "" and g.criteria[0].quote_verified
    invented = [{"n": 1, "marks": 2, "quote": "since 1989 governments were coalitions... parties became dictators"}]
    assert grade_one([pointed(2, 2)], answer(text=text, points=invented))[0]["2"].flag_reason == "EVIDENCE_NOT_FOUND"


def test_a_point_whose_quote_is_not_in_the_answer_is_flagged():
    points = [{"n": 1, "marks": 1, "quote": "an answer"}, {"n": 2, "marks": 1, "quote": "never written"}]
    g = grade_one([pointed(2, 1, 1)], answer(points=points))[0]["2"]
    assert g.flag_reason == "EVIDENCE_NOT_FOUND" and g.suggested_score == 2


def test_partial_on_high_value_is_flagged_but_not_on_low_value(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_HIGH_VALUE_MARKS", 5)
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


def test_evidence_must_be_in_the_answer(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_FLAG_ZERO_ON_WRITTEN", False)
    grades, _ = grade_one([written()], answer(quote="words the student never wrote at all"))
    assert grades["2"].flag_reason == "EVIDENCE_NOT_FOUND" and grades["2"].suggested_score == 2
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
    assert grades["2"].blank


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


def test_or_choice_with_both_sides_answered_counts_the_better_answer():
    rubric = [written(4, choice_group="g"), written(5, choice_group="g")]
    grades, _ = grade_one(rubric, answer(4, marks=1.0), answer(5, marks=2.0))
    assert grades["5"].routing == "AUTO_ACCEPT" and grades["5"].final_score == 2
    assert grades["4"].extra and grades["4"].routing == "NOT_ATTEMPTED" and grades["4"].final_score == 0
    assert grades["4"].grader_score == 1 and "earned 1" in grades["4"].routing_reason
    grades, _ = grade_one(rubric, answer(4), answer(5))  # Equal marks: the first on the paper.
    assert grades["4"].final_score == 2 and grades["5"].extra


def test_extra_answers_go_to_the_teacher_when_one_has_no_mark_or_the_rule_is_off(monkeypatch):
    rubric = [written(4, choice_group="g"), written(5, choice_group="g")]
    grades, _ = grade_one(rubric, answer(4), answer(5, unreadable=True, text="", marks=None))
    assert grades["4"].flag_reason == "ATTEMPT_RULE" and grades["4"].final_score == 0
    assert "ATTEMPT_RULE" in grades["5"].flag_reason and grades["5"].routing == "MANUAL"
    monkeypatch.setattr(config, "GEMINI_COUNT_BEST_ANSWERS", False)
    grades, _ = grade_one(rubric, answer(4), answer(5))
    for key in "45":
        assert grades[key].flag_reason == "ATTEMPT_RULE" and grades[key].final_score == 0


def test_or_choice_with_neither_side_answered_is_missing():
    rubric = [written(4, choice_group="g"), written(5, choice_group="g")]
    grades, _ = grade_one(rubric)
    assert grades["4"].blank and grades["5"].blank


def test_or_choice_whose_alternatives_have_parts():
    rubric = [
        written(5, part="a", choice_group="g", choice_alternative="5"),
        written(5, part="b", choice_group="g", choice_alternative="5"),
        written(6, part="a", choice_group="g", choice_alternative="6"),
    ]
    grades, _ = grade_one(rubric, answer(5, part="a"))
    assert grades["5A"].routing == "AUTO_ACCEPT"
    assert grades["5B"].blank  # Chose this side, so its parts are due.
    assert grades["6A"].routing == "NOT_ATTEMPTED"


def test_any_k_of_n():
    rubric = [written(n, section_attempt_count=2) for n in (1, 2, 3)]
    grades, _ = grade_one(rubric, answer(1), answer(3))
    assert grades["1"].routing == grades["3"].routing == "AUTO_ACCEPT"
    assert grades["2"].routing == "NOT_ATTEMPTED"

    grades, _ = grade_one(rubric, answer(1, marks=1.0), answer(2), answer(3))
    assert grades["1"].extra and grades["1"].final_score == 0  # The lowest of three is left out.
    assert grades["2"].final_score == grades["3"].final_score == 2
    assert not any(g.flag_reason for g in grades.values())

    grades, _ = grade_one(rubric, answer(1))
    assert grades["2"].blank and grades["3"].blank


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


def test_flagged_marks_count_toward_the_total_as_suggestions(client_for):
    client, _ = client_for(good_reply(mcq_answer(), answer(read=0.2)))
    result = sheet(client)
    assert result.total_score == 3 and result.flagged_count == 1 and result.status == "NEEDS_REVIEW"


def test_both_sides_of_a_choice_never_push_the_total_past_the_maximum(client_for):
    rubric = [written(4, choice_group="g"), written(5, choice_group="g")]
    client, _ = client_for(good_reply(answer(4), answer(5)))
    result = grade_sheet(
        exam_id="e", hall_ticket="s1", student_name="", file_bytes=pdf(1), extension=".pdf",
        rubric_list=rubric, total_marks=2, client=client,
    )
    assert result.total_score == 2 and result.flagged_count == 0 and result.status == "COMPLETE"


def test_a_student_who_answers_every_question_gets_the_best_ones_counted():
    """The 20-mark paper: one from each group (2 marks), any two of four (4), any one of two (8)."""
    rubric = [
        written(1, 2, choice_group="A", section="I"), written(2, 2, choice_group="A", section="I"),
        written(3, 2, choice_group="B", section="I"), written(4, 2, choice_group="B", section="I"),
        *[written(n, 4, section="II", section_attempt_count=2) for n in (5, 6, 7, 8)],
        *[written(n, 8, section="III", section_attempt_count=1) for n in (9, 10)],
    ]
    given = {1: 2, 2: 1.5, 3: 1, 4: 2, 5: 3.5, 6: 3, 7: 0, 8: 3.5, 9: 6, 10: 7.5}
    grades, _ = grade_one(rubric, *[answer(n, marks=float(m), quote="" if m == 0 else "an answer here") for n, m in given.items()])
    assert {n for n in given if grades[str(n)].extra} == {2, 3, 6, 7, 9}
    assert sum(g.final_score for g in grades.values()) == 2 + 2 + 3.5 + 3.5 + 7.5
    assert not any(g.flag_reason for g in grades.values())


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
    assert "DUPLICATE_ANSWER" in g.flag_reason and g.routing == "FLAGGED"
    assert g.extracted_text == "first half" and g.suggested_score == 2


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


def test_queue_job_grades_and_saves_on_the_emulator(client_for, monkeypatch, tmp_path):
    if not db_service.is_connected():
        pytest.skip("The Firestore emulator is not connected.")
    from routes import upload as upload_route
    from services import gemini_sheet
    from services.queue_manager import QueueJob

    client, fake = client_for(good_reply(mcq_answer(), answer()))
    monkeypatch.setattr(gemini_sheet, "get_client", lambda: client)
    monkeypatch.setattr(upload_route.queue_manager, "update_job_stage", lambda *a, **k: None)
    # The job is run directly, not through the queue; without this it looks deleted.
    monkeypatch.setattr(upload_route.queue_manager, "get_job", lambda job_id: job)
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


# ------------------------------------------------- marking style and the kinds of paper

def test_each_marking_style_has_its_own_rules_and_generous_is_the_default():
    generous, balanced, strict = (sheet_instructions([written()], style) for style in ("GENEROUS", "BALANCED", "STRICT"))
    assert "Give the benefit of the doubt" in generous and "never give the benefit of the doubt" in strict
    assert "strict board examiner" in strict and "misspelt key term" in strict
    assert "vague or general line" in strict and "find it" in strict
    # The leniency rules belong to the kinder styles only.
    assert "any wording" in generous and "any wording" not in strict
    assert "never below 0" in strict and "Spelling and grammar lose no marks" not in strict
    assert "partly there or vague" in balanced
    assert sheet_instructions([written()]) == generous == sheet_instructions([written()], "nonsense")


def test_teacher_notes_are_added_trimmed_and_only_for_written_answers():
    text = sheet_instructions([written()], "STRICT", "  Cut half a mark\nfor spelling.  " + "x" * 2000)
    assert "Cut half a mark for spelling." in text and "x" * 601 not in text
    assert "teacher's own marking rules" not in sheet_instructions([written()], "STRICT", "")


def test_a_paper_is_only_sent_the_rules_its_questions_need():
    written_only, mixed = sheet_instructions([written()]), sheet_instructions([mcq(), written()])
    assert "multiple choice" not in written_only and "multiple-choice" not in written_only
    assert "For multiple choice" in mixed and "Marking written answers" in mixed
    assert len(written_only) < len(mixed)


def test_mcq_questions_send_only_their_letters_to_gemini():
    sent = json.loads(rubric_for_prompt([mcq(), written()]))
    assert sent[0] == {"question_number": 1, "sub_part": "", "section": "A", "format": "MCQ", "options": ["A", "B"]}
    assert sent[1]["question"] == "Explain" and sent[1]["criteria"][0]["n"] == 1


def test_mcq_only_paper_uses_the_short_prompt_and_small_reply(client_for, monkeypatch):
    seen = []
    client, fake = client_for(reply({"readable": True, "answers": [
        {"question_number": 1, "selected_option": "B", "read_confidence": 0.95},
        {"question_number": 3, "selected_option": "A,B", "read_confidence": 0.4},
    ]}))
    generate = client.generate
    monkeypatch.setattr(client, "generate", lambda run, **kw: seen.append(kw) or generate(run, **kw))
    result = sheet(client, rubric=[mcq(1), mcq(2), mcq(3)])
    assert len(fake.calls) == 1
    assert seen[0]["response_schema"] is McqSheet and seen[0]["system_instruction"] == MCQ_INSTRUCTIONS
    by_number = {q.question_number: q for q in result.questions}
    assert by_number[1].final_score == 1 and by_number[1].routing == "AUTO_ACCEPT"
    assert by_number[2].blank and by_number[2].final_score == 0
    assert by_number[3].flag_reason == "UNCLEAR_OPTION" and by_number[3].routing == "MANUAL"


def test_the_exams_marking_style_reaches_the_one_request(client_for, monkeypatch):
    seen = []
    client, fake = client_for(good_reply(answer()), good_reply(answer()))
    generate = client.generate
    monkeypatch.setattr(client, "generate", lambda run, **kw: seen.append(kw) or generate(run, **kw))
    result = grade_sheet(
        exam_id="e", hall_ticket="s1", student_name="", file_bytes=pdf(1), extension=".pdf",
        rubric_list=[written()], client=client, marking_style="strict", marking_notes="Cut for spelling.",
    )
    assert len(seen) == 1 and len(fake.calls) == 1 and result.gemini_usage["calls"] == 1
    assert "strict board examiner" in seen[0]["system_instruction"]
    assert "Cut for spelling." in seen[0]["system_instruction"]


# ------------------------------------------------------------ calibration from one sheet

CALIBRATION = {
    "calibration_id": "cal-1",
    "hall_ticket": "MARKED-1",
    "rubric_version": "1",
    "examples": [
        {"question_number": 2, "sub_part": "", "max_marks": 2.0, "answer": "lemon looks big due to water",
         "app_marks": 1.0, "teacher_marks": 2.0, "remark": "Water acting as a lens is enough", "changed": True},
        {"question_number": 1, "sub_part": "", "max_marks": 1.0, "answer": "B",
         "app_marks": 1.0, "teacher_marks": 1.0, "remark": "", "changed": False},
    ],
}


def test_a_calibrated_sheet_sends_the_teachers_example_with_each_written_question(client_for, monkeypatch):
    seen = []
    client, fake = client_for(good_reply(mcq_answer(), answer()))
    generate = client.generate
    monkeypatch.setattr(client, "generate", lambda run, **kw: seen.append(kw) or generate(run, **kw))
    result = grade_sheet(
        exam_id="e", hall_ticket="s2", student_name="", file_bytes=pdf(1), extension=".pdf",
        rubric_list=[mcq(), written()], client=client, calibration=CALIBRATION,
    )
    assert len(fake.calls) == 1
    sent = json.loads(seen[0]["contents"][1])
    example = sent[1]["teacher_example"]
    assert example == {
        "answer": "lemon looks big due to water", "teacher_marks": 2.0, "app_marks": 1.0,
        "teacher_remark": "Water acting as a lens is enough",
    }
    assert "teacher_example" not in sent[0]  # Multiple choice is marked by code, never calibrated.
    assert "teacher_example" in seen[0]["system_instruction"]
    assert result.calibration_id == "cal-1" and result.calibrated_from == "MARKED-1"


def test_without_calibration_nothing_extra_is_sent(client_for, monkeypatch):
    seen = []
    client, _ = client_for(good_reply(answer()))
    generate = client.generate
    monkeypatch.setattr(client, "generate", lambda run, **kw: seen.append(kw) or generate(run, **kw))
    result = grade_sheet(
        exam_id="e", hall_ticket="s3", student_name="", file_bytes=pdf(1), extension=".pdf",
        rubric_list=[written()], client=client,
    )
    assert "teacher_example" not in seen[0]["contents"][1]
    assert "teacher_example" not in seen[0]["system_instruction"]
    assert result.calibration_id == "" and result.calibrated_from == ""
