"""One answer sheet: one Gemini request reads and grades it, then code decides the flags.

Gemini proposes. Code checks. Written answers are marked point by point against the
rubric and the code adds the points up. An answer goes to the teacher when a rule doubts
it. A flagged answer's mark still counts toward the total, as a suggestion the teacher
can change; the sheet is final only after the teacher approves it. MCQ marks are
computed here from the letter read, and Gemini never sees the MCQ key.

When the teacher has chosen a marked sheet to calibrate with, each written question
carries that sheet's answer, the teacher's mark and remark, and Gemini marks to the
same standard (services/calibration.py).
"""

from __future__ import annotations

import json
import math
import re
from enum import Enum
from typing import Any, Callable

from pydantic import BaseModel, Field
from rapidfuzz import fuzz

import config
from models.schemas import CriterionResult, PaperResult, QuestionGrade, QuestionRubric
from services.gemini_client import BadModelResponse, GeminiClient, RunBudget, get_client, split_pages
from services.gemini_paper import question_units

SHEET_PROMPT_VERSION = "gemini-sheet-v8"

# Flag reasons.
UNREADABLE = "UNREADABLE"
UNMATCHED = "UNMATCHED"
MISSING = "MISSING"
LOW_CONFIDENCE = "LOW_CONFIDENCE"
PARTIAL_HIGH_VALUE = "PARTIAL_HIGH_VALUE"
EVIDENCE_NOT_FOUND = "EVIDENCE_NOT_FOUND"
INVALID_MARK = "INVALID_MARK"
UNCLEAR_OPTION = "UNCLEAR_OPTION"
NO_KEY = "NO_KEY"
DUPLICATE_ANSWER = "DUPLICATE_ANSWER"
NUMBER_INFERRED = "NUMBER_INFERRED"
ATTEMPT_RULE = "ATTEMPT_RULE"
ZERO_ON_WRITTEN = "ZERO_ON_WRITTEN"
SHEET_NOT_READ = "SHEET_NOT_READ"

# With these, both sides of a choice were answered: counting both could pass the paper's total.
_NOT_COUNTED = {ATTEMPT_RULE}
_NO_SUGGESTION = {UNREADABLE, MISSING, SHEET_NOT_READ, INVALID_MARK, NO_KEY, UNCLEAR_OPTION}


class NumberSource(str, Enum):
    WRITTEN = "WRITTEN"
    INFERRED = "INFERRED"


class PointMark(BaseModel):
    n: int = Field(description="The rubric point's number n for this question.")
    marks: float = Field(description="Marks earned for this point: 0 up to the point's marks.")
    quote: str = Field(default="", description="Words from read_text that earned it, copied exactly.")


class AnswerLength(str, Enum):
    FULL = "FULL"
    SHORT = "SHORT"
    HALF = "HALF"
    VERY_SHORT = "VERY_SHORT"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class SheetAnswer(BaseModel):
    question_number: int
    sub_part: str = ""
    number_source: NumberSource = Field(
        description="WRITTEN if the student wrote this question number; INFERRED if you worked it out."
    )
    read_text: str = Field(description="The answer exactly as written. Do not correct or complete it.")
    selected_option: str = Field(default="", description="MCQ only: the option letter the student chose.")
    page_number: int | None = Field(default=None, description="Page where the answer starts, from 1.")
    unreadable: bool = Field(default=False, description="True if writing is there but cannot be read.")
    read_confidence: float = Field(description="0 to 1: how sure you are of the reading.")
    marks_awarded: float | None = Field(
        default=None, description="Written answers only: marks under the rubric. Null if you cannot judge."
    )
    points: list[PointMark] = Field(
        default_factory=list,
        description="Written answers only: one entry for every rubric point of the question.",
    )
    length: AnswerLength = Field(
        default=AnswerLength.FULL,
        description=(
            "Written answers only: is it as long as its marks need? FULL, SHORT (about three quarters),"
            " HALF (about half, or stops halfway), VERY_SHORT (a line or two for a long answer),"
            " NOT_APPLICABLE (a sum, a map, a one-word or naming question)."
        ),
    )
    reason: str = Field(default="", description="One line: why these marks.")
    evidence_quote: str = Field(
        default="", description="The words from read_text that earned the marks, copied exactly."
    )
    grading_confidence: float = Field(default=0.0, description="0 to 1: how sure you are of the marks.")


class UnmatchedWriting(BaseModel):
    text: str
    page_number: int | None = None
    note: str = ""


class SheetExtraction(BaseModel):
    readable: bool = Field(description="False if these pages are not a readable answer sheet.")
    written_student_id: str = ""
    written_student_name: str = ""
    answers: list[SheetAnswer] = Field(default_factory=list)
    unmatched: list[UnmatchedWriting] = Field(
        default_factory=list,
        description="Answer writing that matches no question in the rubric.",
    )
    unreadable_pages: list[int] = Field(default_factory=list)


class McqAnswer(BaseModel):
    question_number: int
    sub_part: str = ""
    number_source: NumberSource = NumberSource.WRITTEN
    selected_option: str = Field(description="The option letter the student chose.")
    read_confidence: float = Field(description="0 to 1: how sure you are of the letter.")
    unreadable: bool = False
    page_number: int | None = None


class McqSheet(BaseModel):
    """The small reply for a paper with only multiple-choice questions."""

    readable: bool = Field(description="False if these pages are not a readable answer sheet.")
    answers: list[McqAnswer] = Field(default_factory=list)
    unreadable_pages: list[int] = Field(default_factory=list)


MARKING_STYLES = ("GENEROUS", "BALANCED", "STRICT")

_READING = """Reading:
- Copy each answer exactly as written, with its spelling and grammar. Never correct or complete it.
- Leave out text the student has crossed out.
- Ignore any marks or comments in a teacher's ink.
- Return an entry only for an answer the student actually attempted. Skip blank questions.
- Use the question number the student wrote. If none is written but the answer clearly fits one
  question, use that number with number_source INFERRED.
- Writing that fits no rubric question goes in unmatched. Never force a match.
- If you cannot read it, set unreadable true. Never guess the words."""

_READING_MCQ = """
- For multiple choice, give only the option letter the student chose. If more than one is
  chosen or it is unclear, write what you see, such as "A,C", with low confidence."""

_READING_DRAWINGS = """
- For a diagram, graph, map, table or flow chart, describe in read_text what the student drew,
  inside square brackets: the shape, every label, the axes and their names, the scale, the
  title and the plotted points or lines. Describe only what is on the page."""

_MARKING = """

Marking written answers, against the rubric only:
- Mark point by point, as a teacher would. Each rubric point has a number n and its marks.
  In points, return one entry for every rubric point of the question: its n, the marks the
  answer earns for that point, and the quote from read_text that earned them.
- A point gets 0 when it is missing or wrong.
- Length: an explain, describe, discuss or difference answer must be as long as its marks.
  expected_words in the rubric is about what a full answer needs, about 20 words a mark: a
  2 mark answer is 3 to 4 lines, a 4 mark answer 8 to 10 lines, a 5 mark answer half a page or
  more. A diagram counts for part of it. Set length by comparing the answer with this. An
  answer that stops halfway, or is correct but only a line or two for a 4 mark question, has
  not earned full marks: its points are not fully explained, so mark them down. A brief answer
  can be fine for a sum, a map, a definition or a one-word question: set NOT_APPLICABLE.
- There is no negative marking. No point, and no answer, ever gets less than 0. A mistake
  lowers the marks of the one point it belongs to, and is counted once: a point that already
  got 0 for being wrong or missing loses nothing more, and a mistake never takes marks from
  another point. Name each mistake and its correction in reason."""

_STYLE = {
    "GENEROUS": """
- A point is earned by the idea, in any wording. The points are not a word list: the student's
  own words, a different valid example or a shorter explanation can earn the marks.
- For a diagram or graph, a point is earned by what is drawn and labelled, not by neatness.
- Give the benefit of the doubt. If the answer shows the student understands a point, give that
  point its full marks even when its wording is imperfect. Give part of a point's marks only
  when the idea is half there.
- Spelling and grammar lose no marks. A wrong fact stated as true lowers that point's marks.""",
    "BALANCED": """
- A point is earned by the idea, in any wording, but it must be stated, not hinted at.
- For a diagram or graph, a point is earned by what is drawn and labelled, not by neatness.
- Give a point its full marks when the idea is stated clearly and correctly. Give half of the
  point's marks when it is only partly there or vague.
- A wrong fact stated as true takes 0.5 off the point it is in. Spelling and grammar lose no
  marks, unless a key term cannot be recognised.""",
    "STRICT": """
Mark as a strict board examiner who checks every line and corrects every mistake. Never round
up and never give the benefit of the doubt: when you are unsure whether a point is earned, it
is not.
- A point earns its full marks only when it is stated completely, correctly and precisely, with
  the right term. Your own wording is allowed, but a vague or general line ("it was important",
  "it helped people") earns nothing. A point that is only partly stated earns at most half.
- If the question asks for a number of points, reasons, examples or features, only that many
  correct ones earn marks, and each one missing loses its share.
- If the question asks to explain, compare, give an example or draw, an answer that only lists
  or only defines loses the marks for the missing part.
- Writing that does not answer the question, or repeats an earlier point, earns nothing.
- Diagrams, graphs and maps: each missing or wrong label, title, axis name, unit, scale, key or
  direction loses 0.5. A rough or wrongly shaped drawing loses 0.5.
- Then lower the marks of the point each remaining mistake is in, never below 0 for that
  point, and give each mistake with its correction in reason:
  - a wrong fact, name, date, number, formula or unit: 0.5 each, or 1 if the answer depends on it;
  - a misspelt key term, name or place: 0.5 each, at most 1 for spelling in one answer;
  - a missing heading, step, final answer or unit where one is expected: 0.5 each;
  - careless, unordered or incomplete presentation: 0.5.
- An answer worth 3 marks or more gets full marks only when it has no fault at all. Nearly
  every real answer has one: find it.
- In reason, say where the marks were lost.""",
}

_MARKING_END = """
- Marks are in steps of 0.5, from 0 up to the point's marks. marks_awarded is the sum of
  the points.
- Each quote is copied exactly from read_text. Leave it empty only for a point that gets 0.
  evidence_quote is the single most important quote."""

_MARKING_NO_MCQ = """
- Do not mark multiple-choice questions. Leave marks_awarded null and points empty for them."""

_CALIBRATION = """

This exam's teacher has marked another student's sheet. A question may carry it as
teacher_example: that student's answer, the marks the teacher gave, the marks the app had
proposed, and the teacher's remark. Mark to this teacher's standard:
- An answer of about the same quality and completeness as the example earns about the same
  marks, point for point.
- Where the teacher moved the app's marks up or down, move your marking the same way for
  answers like it. Where the remark states a rule, follow it.
- The example is another student's work. Judge this student's own answer; never copy the
  example's marks without comparing the two answers."""

_CONFIDENCE = """

Confidence is honest: use a low value when the writing is hard to read or the marks could
reasonably differ. A teacher reviews low-confidence answers, so doubt costs nothing."""

MCQ_INSTRUCTIONS = """You read multiple-choice answer sheets. Every question on this paper is multiple choice.

- For each question the student answered, give the question number and only the option letter
  chosen. Skip blank questions.
- If more than one option is chosen or it is unclear, write what you see, such as "A,C", with
  low read_confidence.
- Ignore crossed-out choices and anything in a teacher's ink.
- If the number is not written but the position makes it clear, set number_source INFERRED.
- If a choice cannot be read, set unreadable true. Never guess."""


def normal_style(value: str | None) -> str:
    """One of MARKING_STYLES. Anything else falls back to the configured default."""
    style = str(value or "").strip().upper()
    return style if style in MARKING_STYLES else config.GEMINI_MARKING_STYLE


def sheet_instructions(
    rubric_list: list[QuestionRubric] | None = None, style: str = "", notes: str = "",
    calibrated: bool = False,
) -> str:
    """The instructions for one paper: only the rules its kinds of question need.

    A paper with no multiple-choice questions is not sent the multiple-choice rules, and
    the other way round, so no request pays for text it cannot use.
    """
    formats = {q.question_format for q in rubric_list} if rubric_list else {"MCQ", ""}
    has_mcq = "MCQ" in formats
    has_written = bool(formats - {"MCQ"})
    text = "You read and mark handwritten exam answer sheets of any subject.\n\n" + _READING
    if has_mcq:
        text += _READING_MCQ
    if has_written:
        text += _READING_DRAWINGS + _MARKING + _STYLE[normal_style(style)] + _MARKING_END
        if has_mcq:
            text += _MARKING_NO_MCQ
        notes = " ".join(str(notes or "").split())[:MARKING_NOTES_LIMIT]
        if notes:
            text += (
                "\n\nThis teacher's own marking rules for this exam. Where they differ from the"
                f" rules above, follow these:\n{notes}"
            )
        if calibrated:
            text += _CALIBRATION
    return text + _CONFIDENCE


MARKING_NOTES_LIMIT = 600
SHEET_INSTRUCTIONS = sheet_instructions()


def normalize_part(value: Any) -> str:
    return re.sub(r"[^A-Z0-9.]", "", str(value or "").upper()).strip(".")


def _squeeze(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


_GAP = re.compile(r"\s*(?:\.{3,}|…)\s*")


def _quote_is_in_text(quote: str, text: str) -> bool:
    """True when every part of the quote is in the answer.

    Gemini often joins two separate phrases with "..."; each phrase is checked on its own.
    """
    text = _squeeze(text)
    parts = [_squeeze(part) for part in _GAP.split(quote or "")]
    parts = [part for part in parts if part]
    if not parts or not text:
        return False
    return all(part in text or fuzz.partial_ratio(part, text) >= 90 for part in parts)


def expected_words(max_marks: float) -> int:
    """About how many words a full explained answer of these marks needs. 0 means not checked."""
    per_mark = config.GEMINI_WORDS_PER_MARK
    return int(round(float(max_marks) * per_mark)) if per_mark > 0 and max_marks >= 2 else 0


# The most a short answer can earn, as a share of the question's marks.
_LENGTH_CAP = {
    "GENEROUS": {"SHORT": 1.0, "HALF": 0.75, "VERY_SHORT": 0.5},
    "BALANCED": {"SHORT": 0.75, "HALF": 0.5, "VERY_SHORT": 0.25},
    "STRICT": {"SHORT": 0.5, "HALF": 0.5, "VERY_SHORT": 0.25},
}
_LENGTH_ORDER = ["FULL", "SHORT", "HALF", "VERY_SHORT"]
# Questions where a short answer can be complete: sums, maps, naming, one word.
_BRIEF_IS_FINE = re.compile(
    r"\b(calculate|solve|find|prove|evaluate|simplify|compute|draw|sketch|map|locate|mark|"
    r"label|name|identify|fill|match|choose|one word|true or false|expand|abbreviat|define|"
    r"state|list|mention|what is meant|full form|show that|verify|derive|determine|construct|plot)",
    re.IGNORECASE,
)


def _word_count(text: str) -> tuple[int, bool]:
    """Words written outside the [drawing] descriptions, and whether there is a drawing."""
    drawing = "[" in (text or "")
    prose = re.sub(r"\[[^\]]*\]?", " ", text or "")
    return len(re.findall(r"[^\W\d_]{2,}", prose)), drawing


def _length_cap(
    rubric: QuestionRubric, answer: SheetAnswer, style: str
) -> tuple[float | None, str]:
    """The most this answer can earn for its length, and why. None when length is not checked."""
    expected = expected_words(rubric.max_marks)
    judged = answer.length.value if isinstance(answer.length, AnswerLength) else str(answer.length)
    if not expected:
        return None, ""
    words, drawing = _word_count(answer.read_text)
    measured = "FULL"
    # The count backs up Gemini's own judgement, even when Gemini said length does not apply.
    # A drawing, a sum or a naming question can be brief, so the count is skipped for those.
    # Working in symbols (a sum, a proof, a derivation) is short in words but not short.
    symbols = len(re.findall(r"[0-9=+\-*/^√()]", answer.read_text or ""))
    if not drawing and symbols < words and not _BRIEF_IS_FINE.search(rubric.question_text or ""):
        share = words / expected
        measured = "VERY_SHORT" if share < 0.25 else "HALF" if share < 0.5 else "FULL"
        if share >= 1.0:
            # As long as a full answer needs: the length rule has nothing to take off. Gemini's
            # doubts about the content are already in the points.
            judged = "FULL"
    worst = max(judged if judged in _LENGTH_ORDER else "FULL", measured, key=_LENGTH_ORDER.index)
    share = _LENGTH_CAP[normal_style(style)].get(worst, 1.0)
    if share >= 1.0:
        return None, ""
    cap = math.floor(float(rubric.max_marks) * share * 2) / 2
    label = {"SHORT": "shorter than", "HALF": "about half of", "VERY_SHORT": "far shorter than"}[worst]
    return cap, (
        f"Answer is {label} a {rubric.max_marks:g} mark answer needs ({words} words; about "
        f"{expected} expected), so at most {cap:g}"
    )


def rubric_for_prompt(
    rubric_list: list[QuestionRubric], examples: dict[tuple[int, str], dict] | None = None
) -> str:
    """The rubric Gemini marks against. The MCQ key and suggestions are left out on purpose.

    examples: the teacher's calibration example for each written question, if any.
    """
    examples = examples or {}
    return json.dumps(
        [
            {
                "question_number": q.question_number,
                "sub_part": q.sub_part,
                "section": q.section,
                "format": q.question_format,
                # Reading a chosen letter needs only the letters, not the question or options.
                **({"options": [str(o.get("label", "")) for o in q.options]}
                   if q.question_format == "MCQ" else {
                    "question": q.question_text,
                    "max_marks": q.max_marks,
                    **({"expected_words": expected_words(q.max_marks)}
                       if expected_words(q.max_marks) else {}),
                    "criteria": [
                        {"n": index, "point": c.description, "marks": c.max_marks}
                        for index, c in enumerate(q.criteria, start=1)
                    ],
                }),
                **({"or_choice": q.choice_group} if q.choice_group else {}),
                **(
                    {"answer_any_k_in_section": q.section_attempt_count}
                    if q.section_attempt_count else {}
                ),
                **(
                    {"teacher_example": examples[(q.question_number, normalize_part(q.sub_part))]}
                    if q.question_format != "MCQ"
                    and (q.question_number, normalize_part(q.sub_part)) in examples else {}
                ),
            }
            for q in rubric_list
        ],
        ensure_ascii=False,
    )


def _page_slice(file_bytes: bytes, start: int, end: int) -> bytes:
    import pymupdf

    with pymupdf.open(stream=file_bytes, filetype="pdf") as source, pymupdf.open() as piece:
        piece.insert_pdf(source, from_page=start, to_page=end - 1)
        return piece.tobytes()


def page_count_of(file_bytes: bytes, extension: str) -> int:
    if extension != ".pdf":
        return 1
    import pymupdf

    with pymupdf.open(stream=file_bytes, filetype="pdf") as document:
        return document.page_count


def _sheet_part(data: bytes, extension: str) -> Any:
    from google.genai import types

    mime = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
    return types.Part.from_bytes(data=data, mime_type=mime[extension])


def _read_parts(
    file_bytes: bytes,
    extension: str,
    rubric_list: list[QuestionRubric],
    sheet_id: str,
    client: GeminiClient,
    run: RunBudget,
    style: str = "",
    notes: str = "",
    examples: dict[tuple[int, str], dict] | None = None,
) -> tuple[list[tuple[SheetExtraction, int]], list[str], str]:
    """Send the sheet: one request, or two if it is over the page limit.

    Returns (extraction, page offset) for each request that gave a usable reply, the
    problems from those that did not, and the model that answered.
    """
    pages = page_count_of(file_bytes, extension)
    ranges = split_pages(pages)
    rubric_text = rubric_for_prompt(rubric_list, examples)
    readings: list[tuple[SheetExtraction, int]] = []
    problems: list[str] = []
    model = config.GEMINI_MODEL
    # A paper of only multiple-choice questions gets a short prompt and a small reply.
    mcq_only = bool(rubric_list) and all(q.question_format == "MCQ" for q in rubric_list)
    schema = McqSheet if mcq_only else SheetExtraction
    instructions = (
        MCQ_INSTRUCTIONS if mcq_only
        else sheet_instructions(rubric_list, style, notes, calibrated=bool(examples))
    )
    for start, end in ranges:
        piece = file_bytes if len(ranges) == 1 else _page_slice(file_bytes, start, end)
        contents = ["RUBRIC:", rubric_text, "ANSWER SHEET:", _sheet_part(piece, extension)]
        try:
            result = client.generate(
                run,
                kind="sheet",
                sheet_id=sheet_id,
                contents=contents,
                response_schema=schema,
                system_instruction=instructions,
            )
        except BadModelResponse as error:
            problems.append(f"Pages {start + 1}-{end}: {error}")
            continue
        parsed = result.parsed if isinstance(result.parsed, schema) else schema.model_validate(result.parsed)
        extraction = _from_mcq_sheet(parsed) if mcq_only else parsed
        model = result.model
        if result.finish_reason not in ("STOP", ""):
            problems.append(f"Pages {start + 1}-{end}: the reply stopped early ({result.finish_reason}).")
        readings.append((extraction, start))
    return readings, problems, model


def _from_mcq_sheet(sheet: McqSheet) -> SheetExtraction:
    return SheetExtraction(
        readable=sheet.readable,
        unreadable_pages=sheet.unreadable_pages,
        answers=[
            SheetAnswer(
                question_number=a.question_number, sub_part=a.sub_part, number_source=a.number_source,
                read_text=a.selected_option, selected_option=a.selected_option,
                page_number=a.page_number, unreadable=a.unreadable, read_confidence=a.read_confidence,
            )
            for a in sheet.answers
        ],
    )


def _attempted(answer: SheetAnswer | None) -> bool:
    if answer is None:
        return False
    return answer.unreadable or bool(answer.read_text.strip() or answer.selected_option.strip())


def _mcq_grade(rubric: QuestionRubric, answer: SheetAnswer) -> tuple[float | None, list[str]]:
    labels = {str(option.get("label", "")).upper() for option in rubric.options}
    chosen = normalize_part(answer.selected_option or answer.read_text)
    key = rubric.correct_option.strip().upper()
    flags: list[str] = []
    if answer.unreadable:
        return None, [UNREADABLE]
    if chosen not in labels:
        return None, [UNCLEAR_OPTION]
    if key not in labels:
        return None, [NO_KEY]
    if answer.read_confidence < config.GEMINI_MIN_CONFIDENCE:
        flags.append(LOW_CONFIDENCE)
    return (rubric.max_marks if chosen == key else 0.0), flags


def _half_step(value: float) -> bool:
    return abs(value * 2 - round(value * 2)) < 1e-6


def _point_marks(
    rubric: QuestionRubric, answer: SheetAnswer
) -> tuple[float | None, list[CriterionResult], bool]:
    """Add up the marks given point by point: (total, per-point record, every quote found).

    The total is None when the points cannot be trusted: a number that is not in the
    rubric, a point marked twice, or marks outside the point's range.
    """
    by_number: dict[int, PointMark] = {}
    for point in answer.points:
        if point.n in by_number or not 1 <= point.n <= len(rubric.criteria):
            return None, [], False
        by_number[point.n] = point
    total = 0.0
    quotes_found = True
    record: list[CriterionResult] = []
    for number, criterion in enumerate(rubric.criteria, start=1):
        point = by_number.get(number)
        earned = float(point.marks) if point else 0.0
        if earned < 0 or earned > float(criterion.max_marks) + 1e-6 or not _half_step(earned):
            return None, [], False
        found = bool(point) and _quote_is_in_text(point.quote, answer.read_text)
        if earned > 0 and not found:
            quotes_found = False
        total += earned
        record.append(CriterionResult(
            criterion=criterion.description,
            verbatim_quote=point.quote if point else "",
            marks_awarded=earned,
            rationale=answer.reason,
            quote_verified=found,
        ))
    return total, record, quotes_found


def _take_off(record: list[CriterionResult], amount: float, why: str) -> None:
    """Lower the points, last one first, by this much in all. No row ever goes below 0.

    The rows the teacher sees then add up to the mark given, with no negative row.
    """
    for row in reversed(record):
        if amount <= 1e-6:
            return
        cut = min(float(row.marks_awarded), amount)
        if cut <= 0:
            continue
        row.marks_awarded = float(row.marks_awarded) - cut
        row.rationale = f"{row.rationale} {why}.".strip()
        amount -= cut


def _written_grade(
    rubric: QuestionRubric, answer: SheetAnswer, style: str = ""
) -> tuple[float | None, list[str], list[CriterionResult]]:
    if answer.unreadable:
        return None, [UNREADABLE], []
    maximum = float(rubric.max_marks)
    record: list[CriterionResult] = []
    if answer.points:
        # The code adds the points up; Gemini's own total is not used. Nothing is ever
        # subtracted from the sum: a mistake only lowers the point it is in.
        marks, record, quotes_found = _point_marks(rubric, answer)
    else:
        marks = answer.marks_awarded
        quotes_found = bool(marks) and _quote_is_in_text(answer.evidence_quote, answer.read_text)
    if marks is None or marks < 0 or marks > maximum + 1e-6 or not _half_step(marks):
        return None, [INVALID_MARK], []
    cap, why = _length_cap(rubric, answer, style)
    if cap is not None and marks > cap:
        if not record:
            record = [CriterionResult(
                criterion="Overall", verbatim_quote=answer.evidence_quote, marks_awarded=marks,
                rationale=answer.reason,
                quote_verified=_quote_is_in_text(answer.evidence_quote, answer.read_text),
            )]
        _take_off(record, marks - cap, why)
        marks = cap
    flags: list[str] = []
    lowest = min(answer.read_confidence, answer.grading_confidence)
    if lowest < config.GEMINI_MIN_CONFIDENCE:
        flags.append(LOW_CONFIDENCE)
    if marks > 0 and not quotes_found:
        flags.append(EVIDENCE_NOT_FOUND)
    high_value = config.GEMINI_HIGH_VALUE_MARKS
    if high_value > 0 and 0 < marks < maximum and maximum >= high_value:
        flags.append(PARTIAL_HIGH_VALUE)
    if marks == 0 and config.GEMINI_FLAG_ZERO_ON_WRITTEN:
        flags.append(ZERO_ON_WRITTEN)
    return marks, flags, record


def _grade_record(
    rubric: QuestionRubric,
    answer: SheetAnswer | None,
    marks: float | None,
    flags: list[str],
    model: str,
    reason: str = "",
    point_record: list[CriterionResult] | None = None,
) -> QuestionGrade:
    routing = "AUTO_ACCEPT"
    if flags:
        routing = "MANUAL" if _NO_SUGGESTION & set(flags) else "FLAGGED"
    accepted = routing == "AUTO_ACCEPT" and marks is not None
    # A flagged mark counts too, as a suggestion, so the total is not low until review.
    counted = marks is not None and routing != "MANUAL" and not _NOT_COUNTED & set(flags)
    is_mcq = rubric.question_format == "MCQ"
    text = (answer.read_text if answer else "") or ""
    selected = normalize_part(answer.selected_option or answer.read_text) if answer and is_mcq else ""
    read_confidence = answer.read_confidence if answer else 0.0
    grade_confidence = (
        read_confidence if is_mcq else (answer.grading_confidence if answer else 0.0)
    )
    quote = answer.evidence_quote if answer else ""
    return QuestionGrade(
        question_number=rubric.question_number,
        sub_part=normalize_part(rubric.sub_part),
        choice_group=rubric.choice_group,
        question_type=rubric.question_type or "THEORY",
        max_marks=rubric.max_marks,
        extracted_text=text or selected,
        grading_input_text=text or selected,
        source_page_references=(
            [{"page_number": answer.page_number}] if answer and answer.page_number else []
        ),
        grader_score=marks or 0.0,
        final_score=marks if counted else 0.0,
        suggested_score=None if accepted or marks is None or routing == "MANUAL" else marks,
        selected_option=selected,
        flag_reason=",".join(flags),
        local_routing=routing,
        routing=routing,
        routing_reason=reason or (answer.reason if answer else ""),
        criteria=(
            [] if is_mcq or answer is None else point_record or [CriterionResult(
                criterion="Overall",
                verbatim_quote=quote,
                marks_awarded=marks or 0.0,
                rationale=answer.reason,
                quote_verified=_quote_is_in_text(quote, text),
            )]
        ),
        c_ocr=read_confidence,
        c_final=min(read_confidence, grade_confidence),
        ocr_confidence_class="GEMINI_REPORTED",
        model_used=model,
        verification_status="NOT_REQUESTED",
    )


def _merge_answers(
    readings: list[tuple[SheetExtraction, int]],
) -> tuple[dict[tuple[int, str], SheetAnswer], dict[tuple[int, str], list[SheetAnswer]]]:
    """Key each answer by question. Repeats are kept apart so the teacher sees them."""
    merged: dict[tuple[int, str], SheetAnswer] = {}
    repeats: dict[tuple[int, str], list[SheetAnswer]] = {}
    for extraction, offset in readings:
        for answer in extraction.answers:
            if answer.page_number:
                answer.page_number += offset
            if not _attempted(answer):
                continue
            key = (answer.question_number, normalize_part(answer.sub_part))
            if key in merged:
                repeats.setdefault(key, [merged[key]]).append(answer)
            else:
                merged[key] = answer
    return merged, repeats


def _unattempted_flags(rubric_list: list[QuestionRubric], attempted: set[tuple[int, str]]) -> tuple[
    dict[tuple[int, str], list[str]],
    set[tuple[int, str]],
    list[tuple[int, list[list[tuple[int, str]]]]],
]:
    """Apply OR and any-K rules: which questions are allowed to be blank, and what breaks a rule.

    The third value lists each place where the student answered more than the paper asks:
    how many answers count there, and the answered parts of each answer.
    """
    extra: dict[tuple[int, str], list[str]] = {}
    allowed_blank: set[tuple[int, str]] = set()
    over: list[tuple[int, list[list[tuple[int, str]]]]] = []

    def key_of(question: QuestionRubric) -> tuple[int, str]:
        return (question.question_number, normalize_part(question.sub_part))

    groups: dict[str, list[QuestionRubric]] = {}
    for question in rubric_list:
        if question.choice_group:
            groups.setdefault(f"{question.section}|{question.choice_group}", []).append(question)
    for members in groups.values():
        sides: dict[str, list[QuestionRubric]] = {}
        for member in members:
            side = member.choice_alternative or f"{member.question_number}{member.sub_part}"
            sides.setdefault(side, []).append(member)
        tried = [side for side, parts in sides.items() if any(key_of(p) in attempted for p in parts)]
        if len(tried) > 1:
            over.append((1, [[key_of(p) for p in sides[side] if key_of(p) in attempted] for side in tried]))
            for side in tried:
                for part in sides[side]:
                    if key_of(part) in attempted:
                        extra.setdefault(key_of(part), []).append(ATTEMPT_RULE)
        elif len(tried) == 1:
            for side, parts in sides.items():
                if side != tried[0]:
                    allowed_blank.update(key_of(part) for part in parts)

    sections: dict[str, list[QuestionRubric]] = {}
    for question in rubric_list:
        if question.section_attempt_count:
            sections.setdefault(question.section, []).append(question)
    for members in sections.values():
        wanted = int(members[0].section_attempt_count or 0)
        units = question_units([m.model_dump() for m in members])
        by_unit: dict[int, list[QuestionRubric]] = {}
        for member in members:
            by_unit.setdefault(units[member.question_number], []).append(member)
        tried_units = [
            unit for unit, parts in by_unit.items() if any(key_of(p) in attempted for p in parts)
        ]
        if len(tried_units) > wanted:
            over.append((wanted, [
                [key_of(p) for p in by_unit[unit] if key_of(p) in attempted] for unit in tried_units
            ]))
            for unit in tried_units:
                for part in by_unit[unit]:
                    if key_of(part) in attempted:
                        extra.setdefault(key_of(part), []).append(ATTEMPT_RULE)
        elif len(tried_units) == wanted:
            for unit, parts in by_unit.items():
                if unit not in tried_units:
                    allowed_blank.update(key_of(part) for part in parts)
    return extra, allowed_blank, over


def _best_answers(
    over: list[tuple[int, list[list[tuple[int, str]]]]],
    marks: dict[tuple[int, str], float | None],
) -> tuple[set[tuple[int, str]], set[tuple[int, str]]]:
    """Where a student wrote extra answers, pick the best ones, as a teacher does.

    Returns (parts that count, parts that are extra). A place where any answer has no
    mark is left out of both: only the teacher can choose there.
    """
    counted: set[tuple[int, str]] = set()
    extras: set[tuple[int, str]] = set()
    for wanted, answers in over:
        keys = [key for answer in answers for key in answer]
        if any(marks.get(key) is None for key in keys):
            continue
        # Highest marks first; equal marks keep the paper's order.
        ranked = sorted(answers, key=lambda answer: -sum(marks[key] for key in answer))
        counted.update(key for answer in ranked[:wanted] for key in answer)
        extras.update(key for answer in ranked[wanted:] for key in answer)
    # A part that loses anywhere is extra, even if another rule would have kept it.
    return counted - extras, extras


def grade_readings(
    rubric_list: list[QuestionRubric],
    readings: list[tuple[SheetExtraction, int]],
    problems: list[str],
    model: str,
    style: str = "",
) -> tuple[list[QuestionGrade], list[dict[str, Any]]]:
    """Apply every rule to Gemini's replies. Pure, so all flag logic is testable."""
    merged, repeats = _merge_answers(readings)
    rubric_keys = {(q.question_number, normalize_part(q.sub_part)) for q in rubric_list}
    extra_flags, allowed_blank, over = _unattempted_flags(rubric_list, set(merged) & rubric_keys)
    all_unreadable = bool(readings) and not any(extraction.readable for extraction, _ in readings)
    cut_off = bool(problems)

    marked: dict[tuple[int, str], tuple[float | None, list[str], list[CriterionResult]]] = {}
    for rubric in rubric_list:
        key = (rubric.question_number, normalize_part(rubric.sub_part))
        answer = merged.get(key)
        if answer is None:
            continue
        if rubric.question_format == "MCQ":
            marked[key] = (*_mcq_grade(rubric, answer), [])
        else:
            marked[key] = _written_grade(rubric, answer, style)
    counted, extras = (
        _best_answers(over, {key: value[0] for key, value in marked.items()})
        if config.GEMINI_COUNT_BEST_ANSWERS else (set(), set())
    )

    grades: list[QuestionGrade] = []
    for rubric in rubric_list:
        key = (rubric.question_number, normalize_part(rubric.sub_part))
        answer = merged.get(key)
        if answer is None:
            if key in allowed_blank:
                grade = _grade_record(rubric, None, None, [], model, "Not attempted; the paper's choice rules allow this.")
                grade.routing = grade.local_routing = "NOT_ATTEMPTED"
                grades.append(grade)
                continue
            if cut_off or not readings:
                flag = SHEET_NOT_READ
            elif all_unreadable:
                flag = UNREADABLE
            elif config.GEMINI_FLAG_BLANKS:
                flag = MISSING
            else:
                grade = _grade_record(rubric, None, 0.0, [], model, "No answer was found for this question, so it gets 0.")
                grade.blank = True
                grades.append(grade)
                continue
            grades.append(_grade_record(rubric, None, None, [flag], model, "No answer was read for this question."))
            continue
        marks, flags, point_record = marked[key]
        if key in extras:
            # Written beyond what the paper asks, and it scored no higher than the others.
            grade = _grade_record(rubric, answer, marks, [], model, point_record=point_record)
            grade.routing = grade.local_routing = "NOT_ATTEMPTED"
            grade.extra = True
            grade.final_score = 0.0
            grade.routing_reason = (
                f"Extra answer, not counted: the student answered more than the paper asks here, "
                f"and the best answers count. This one earned {marks:g}."
            )
            grades.append(grade)
            continue
        if answer.number_source == NumberSource.INFERRED:
            flags.append(NUMBER_INFERRED)
        if key in repeats:
            flags.append(DUPLICATE_ANSWER)
        flags.extend(f for f in extra_flags.get(key, []) if not (f == ATTEMPT_RULE and key in counted))
        grades.append(_grade_record(rubric, answer, marks, flags, model, point_record=point_record))

    unmapped: list[dict[str, Any]] = []
    for key, answer in merged.items():
        if key not in rubric_keys:
            unmapped.append({
                "detected_question_number": key[0],
                "sub_part": key[1],
                "extracted_text": answer.read_text or answer.selected_option,
                "mapping_status": "NOT_ON_PAPER",
                "page_references": [{"page_number": answer.page_number}] if answer.page_number else [],
                "flag_reason": UNMATCHED,
            })
    for extraction, offset in readings:
        for item in extraction.unmatched:
            unmapped.append({
                "detected_question_number": None,
                "sub_part": "",
                "extracted_text": item.text,
                "mapping_status": "UNRESOLVED",
                "page_references": (
                    [{"page_number": item.page_number + offset}] if item.page_number else []
                ),
                "flag_reason": UNMATCHED,
                "note": item.note,
            })
    return grades, unmapped


def grade_sheet(
    *,
    exam_id: str,
    hall_ticket: str,
    student_name: str,
    file_bytes: bytes,
    extension: str,
    rubric_list: list[QuestionRubric],
    source_file_reference: str = "",
    rubric_version: str = "",
    total_marks: float | None = None,
    source_sha256: str = "",
    client: GeminiClient | None = None,
    run: RunBudget | None = None,
    on_stage: Callable[[str, str], None] | None = None,
    marking_style: str = "",
    marking_notes: str = "",
    calibration: dict[str, Any] | None = None,
) -> PaperResult:
    """Read and grade one sheet in one request (two only past the page limit).

    calibration: the teacher's calibration sheet for this exam and rubric version, or None.
    Raises a GeminiError if no request got through.
    """
    from services.calibration import examples_for_prompt

    client = client or get_client()
    run = run or client.new_run()
    examples = examples_for_prompt(calibration)
    if on_stage:
        on_stage(
            "gemini_sheet",
            "Gemini is reading and marking the sheet"
            + (" to your calibration" if examples else ""),
        )
    readings, problems, model = _read_parts(
        file_bytes, extension, rubric_list, hall_ticket, client, run,
        style=marking_style, notes=marking_notes, examples=examples,
    )
    grades, unmapped = grade_readings(rubric_list, readings, problems, model, marking_style)
    unreadable_pages = sorted(
        {page + offset for extraction, offset in readings for page in extraction.unreadable_pages}
    )
    flagged = [g for g in grades if g.routing in ("FLAGGED", "MANUAL")]
    needs_review = bool(flagged or unmapped or unreadable_pages or problems)
    confidences = [g.c_final for g in grades if g.routing != "NOT_ATTEMPTED"]
    usage = run.per_sheet.get(hall_ticket)
    return PaperResult(
        hall_ticket=hall_ticket,
        student_name=student_name,
        exam_id=exam_id,
        questions=grades,
        total_score=sum(g.final_score for g in grades),
        max_total=float(total_marks or sum(q.max_marks for q in rubric_list)),
        avg_confidence=round(sum(confidences) / len(confidences), 4) if confidences else 0.0,
        flagged_count=len(flagged),
        status="NEEDS_REVIEW" if needs_review else "COMPLETE",
        finality_status="PENDING_VALIDATION",
        source_file_reference=source_file_reference,
        rubric_version=str(rubric_version),
        unmapped_answers=unmapped,
        page_readers=[
            {"page": page, "model": model, "status": "UNREADABLE"} for page in unreadable_pages
        ],
        sheet_problems=problems,
        source_sha256=source_sha256,
        page_count=page_count_of(file_bytes, extension),
        gemini_usage=usage.as_dict(config.GEMINI_USD_INR) if usage else {},
        calibration_id=str((calibration or {}).get("calibration_id", "")) if examples else "",
        calibrated_from=str((calibration or {}).get("hall_ticket", "")) if examples else "",
    )
