"""One answer sheet: one Gemini request reads and grades it, then code decides the flags.

Gemini proposes. Code checks. An answer counts toward the total only when no rule
flags it; a flagged answer keeps its mark as a suggestion and counts only after the
teacher confirms it. MCQ marks are computed here from the letter read, and Gemini
never sees the MCQ key, so knowing the answer cannot affect how a letter is read.
"""

from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any, Callable

from pydantic import BaseModel, Field
from rapidfuzz import fuzz

import config
from models.schemas import CriterionResult, PaperResult, QuestionGrade, QuestionRubric
from services.gemini_client import (
    BadModelResponse,
    GeminiClient,
    GeminiError,
    RunBudget,
    RunCapReached,
    DailyLimitReached,
    get_client,
    split_pages,
)
from services.gemini_paper import question_units

SHEET_PROMPT_VERSION = "gemini-sheet-v2"

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

_NO_SUGGESTION = {UNREADABLE, MISSING, SHEET_NOT_READ, INVALID_MARK, NO_KEY, UNCLEAR_OPTION}


class NumberSource(str, Enum):
    WRITTEN = "WRITTEN"
    INFERRED = "INFERRED"


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


SHEET_INSTRUCTIONS = """You read and mark handwritten exam answer sheets of any subject.

Reading:
- Copy each answer exactly as written, with its spelling and grammar. Never correct or complete it.
- Leave out text the student has crossed out.
- Ignore any marks or comments in a teacher's ink.
- Return an entry only for an answer the student actually attempted. Skip blank questions.
- Use the question number the student wrote. If none is written but the answer clearly fits one
  question, use that number with number_source INFERRED.
- Writing that fits no rubric question goes in unmatched. Never force a match.
- If you cannot read it, set unreadable true. Never guess the words.
- For multiple choice, give only the option letter the student chose. If more than one is
  chosen or it is unclear, write what you see, such as "A,C", with low confidence.

Marking written answers, against the rubric only:
- Mark the ideas, as a fair teacher would. Spelling and grammar lose no marks. Any wording that
  shows the idea earns credit.
- The rubric's points show what a good answer covers. They are not a word list. An answer in the
  student's own words, with a different valid example or a shorter explanation, earns the marks.
- Give the benefit of the doubt. If the answer shows the student understands the main idea, award
  full marks even when it is brief or imperfect.
- Award partial marks only when an important part of the question is truly missing or wrong.
- Award 0 only when the answer is wrong or does not address the question.
- Award marks in steps of 0.5, from 0 up to the question's maximum.
- evidence_quote is copied exactly from read_text. Leave it empty only when you award 0.
- Do not mark multiple-choice questions. Leave marks_awarded null for them.

Confidence is honest: use a low value when the writing is hard to read or the marks could
reasonably differ. A teacher reviews low-confidence answers, so doubt costs nothing."""


def normalize_part(value: Any) -> str:
    return re.sub(r"[^A-Z0-9.]", "", str(value or "").upper()).strip(".")


def _squeeze(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def _quote_is_in_text(quote: str, text: str) -> bool:
    quote, text = _squeeze(quote), _squeeze(text)
    if not quote or not text:
        return False
    return quote in text or fuzz.partial_ratio(quote, text) >= 90


def rubric_for_prompt(rubric_list: list[QuestionRubric]) -> str:
    """The rubric Gemini marks against. The MCQ key and suggestions are left out on purpose."""
    return json.dumps(
        [
            {
                "question_number": q.question_number,
                "sub_part": q.sub_part,
                "section": q.section,
                "format": q.question_format,
                "question": q.question_text,
                "max_marks": q.max_marks,
                **({"options": q.options} if q.question_format == "MCQ" else {"criteria": [
                    {"point": c.description, "marks": c.max_marks} for c in q.criteria
                ]}),
                **({"or_choice": q.choice_group} if q.choice_group else {}),
                **(
                    {"answer_any_k_in_section": q.section_attempt_count}
                    if q.section_attempt_count else {}
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
) -> tuple[list[tuple[SheetExtraction, int]], list[str], str]:
    """Send the sheet: one request, or two if it is over the page limit.

    Returns (extraction, page offset) for each request that gave a usable reply, the
    problems from those that did not, and the model that answered.
    """
    pages = page_count_of(file_bytes, extension)
    ranges = split_pages(pages)
    rubric_text = rubric_for_prompt(rubric_list)
    readings: list[tuple[SheetExtraction, int]] = []
    problems: list[str] = []
    model = config.GEMINI_MODEL
    for start, end in ranges:
        piece = file_bytes if len(ranges) == 1 else _page_slice(file_bytes, start, end)
        contents = ["RUBRIC:", rubric_text, "ANSWER SHEET:", _sheet_part(piece, extension)]
        try:
            result = client.generate(
                run,
                kind="sheet",
                sheet_id=sheet_id,
                contents=contents,
                response_schema=SheetExtraction,
                system_instruction=SHEET_INSTRUCTIONS,
            )
        except BadModelResponse as error:
            problems.append(f"Pages {start + 1}-{end}: {error}")
            continue
        extraction = (
            result.parsed
            if isinstance(result.parsed, SheetExtraction)
            else SheetExtraction.model_validate(result.parsed)
        )
        model = result.model
        if result.finish_reason not in ("STOP", ""):
            problems.append(f"Pages {start + 1}-{end}: the reply stopped early ({result.finish_reason}).")
        readings.append((extraction, start))
    return readings, problems, model


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


def _written_grade(rubric: QuestionRubric, answer: SheetAnswer) -> tuple[float | None, list[str]]:
    if answer.unreadable:
        return None, [UNREADABLE]
    marks = answer.marks_awarded
    maximum = float(rubric.max_marks)
    if (
        marks is None
        or marks < 0
        or marks > maximum + 1e-6
        or abs(marks * 2 - round(marks * 2)) > 1e-6
    ):
        return None, [INVALID_MARK]
    flags: list[str] = []
    lowest = min(answer.read_confidence, answer.grading_confidence)
    if lowest < config.GEMINI_MIN_CONFIDENCE:
        flags.append(LOW_CONFIDENCE)
    if marks > 0 and not _quote_is_in_text(answer.evidence_quote, answer.read_text):
        flags.append(EVIDENCE_NOT_FOUND)
    if 0 < marks < maximum and maximum >= config.GEMINI_HIGH_VALUE_MARKS:
        flags.append(PARTIAL_HIGH_VALUE)
    if marks == 0 and config.GEMINI_FLAG_ZERO_ON_WRITTEN:
        flags.append(ZERO_ON_WRITTEN)
    return marks, flags


def _grade_record(
    rubric: QuestionRubric,
    answer: SheetAnswer | None,
    marks: float | None,
    flags: list[str],
    model: str,
    reason: str = "",
) -> QuestionGrade:
    routing = "AUTO_ACCEPT"
    if flags:
        routing = "MANUAL" if _NO_SUGGESTION & set(flags) else "FLAGGED"
    accepted = routing == "AUTO_ACCEPT" and marks is not None
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
        final_score=marks if accepted else 0.0,
        suggested_score=None if accepted or marks is None or routing == "MANUAL" else marks,
        selected_option=selected,
        flag_reason=",".join(flags),
        local_routing=routing,
        routing=routing,
        routing_reason=reason or (answer.reason if answer else ""),
        criteria=(
            [] if is_mcq or answer is None else [CriterionResult(
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
    dict[tuple[int, str], list[str]], set[tuple[int, str]]
]:
    """Apply OR and any-K rules: which questions are allowed to be blank, and what breaks a rule."""
    extra: dict[tuple[int, str], list[str]] = {}
    allowed_blank: set[tuple[int, str]] = set()

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
            for unit in tried_units:
                for part in by_unit[unit]:
                    if key_of(part) in attempted:
                        extra.setdefault(key_of(part), []).append(ATTEMPT_RULE)
        elif len(tried_units) == wanted:
            for unit, parts in by_unit.items():
                if unit not in tried_units:
                    allowed_blank.update(key_of(part) for part in parts)
    return extra, allowed_blank


def grade_readings(
    rubric_list: list[QuestionRubric],
    readings: list[tuple[SheetExtraction, int]],
    problems: list[str],
    model: str,
) -> tuple[list[QuestionGrade], list[dict[str, Any]]]:
    """Apply every rule to Gemini's replies. Pure, so all flag logic is testable."""
    merged, repeats = _merge_answers(readings)
    rubric_keys = {(q.question_number, normalize_part(q.sub_part)) for q in rubric_list}
    extra_flags, allowed_blank = _unattempted_flags(rubric_list, set(merged) & rubric_keys)
    all_unreadable = bool(readings) and not any(extraction.readable for extraction, _ in readings)
    cut_off = bool(problems)

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
            else:
                flag = MISSING
            grades.append(_grade_record(rubric, None, None, [flag], model, "No answer was read for this question."))
            continue
        grader = _mcq_grade if rubric.question_format == "MCQ" else _written_grade
        marks, flags = grader(rubric, answer)
        if answer.number_source == NumberSource.INFERRED:
            flags.append(NUMBER_INFERRED)
        if key in repeats:
            flags.append(DUPLICATE_ANSWER)
        flags.extend(extra_flags.get(key, []))
        grades.append(_grade_record(rubric, answer, marks, flags, model))

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


def second_opinions(
    result: PaperResult,
    file_bytes: bytes,
    extension: str,
    rubric_list: list[QuestionRubric],
    client: GeminiClient,
    run: RunBudget,
) -> None:
    """Ask the stronger model about flagged written answers, for the teacher to see.

    It never clears a flag or changes a mark. Stops at the review cap, silently for the
    sheet: the answer simply stays flagged, without a second opinion.
    """
    by_key = {(q.question_number, normalize_part(q.sub_part)): q for q in rubric_list}
    for grade in result.questions:
        rubric = by_key.get((grade.question_number, grade.sub_part))
        if (
            rubric is None
            or rubric.question_format == "MCQ"
            or grade.routing not in ("FLAGGED", "MANUAL")
            or not grade.source_page_references
            or MISSING in grade.flag_reason
        ):
            continue
        page = int(grade.source_page_references[0]["page_number"])
        piece = _page_slice(file_bytes, page - 1, page) if extension == ".pdf" else file_bytes
        contents = [
            "RUBRIC:", rubric_for_prompt([rubric]),
            "ANSWER SHEET PAGE:", _sheet_part(piece, extension),
        ]
        try:
            reply = client.generate(
                run,
                kind="second_opinion",
                sheet_id=result.hall_ticket,
                contents=contents,
                response_schema=SheetExtraction,
                system_instruction=SHEET_INSTRUCTIONS,
                review=True,
            )
        except (RunCapReached, DailyLimitReached):
            return
        except GeminiError:
            continue
        parsed = reply.parsed if isinstance(reply.parsed, SheetExtraction) else SheetExtraction.model_validate(reply.parsed)
        for answer in parsed.answers:
            if (answer.question_number, normalize_part(answer.sub_part)) == (grade.question_number, grade.sub_part):
                grade.second_opinion = {
                    "model": reply.model,
                    "read_text": answer.read_text,
                    "marks": answer.marks_awarded,
                    "reason": answer.reason,
                    "confidence": min(answer.read_confidence, answer.grading_confidence),
                }
                break


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
) -> PaperResult:
    """Read and grade one sheet. Raises a GeminiError if no request got through."""
    client = client or get_client()
    run = run or client.new_run()
    if on_stage:
        on_stage("gemini_sheet", "Gemini is reading and marking the sheet")
    readings, problems, model = _read_parts(
        file_bytes, extension, rubric_list, hall_ticket, client, run
    )
    grades, unmapped = grade_readings(rubric_list, readings, problems, model)
    unreadable_pages = sorted(
        {page + offset for extraction, offset in readings for page in extraction.unreadable_pages}
    )
    flagged = [g for g in grades if g.routing in ("FLAGGED", "MANUAL")]
    needs_review = bool(flagged or unmapped or unreadable_pages or problems)
    confidences = [g.c_final for g in grades if g.routing != "NOT_ATTEMPTED"]
    usage = run.per_sheet.get(hall_ticket)
    result = PaperResult(
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
    )
    if config.GEMINI_REVIEW_ENABLED and flagged:
        if on_stage:
            on_stage("gemini_second_opinion", "Stronger model is looking at flagged answers")
        second_opinions(result, file_bytes, extension, rubric_list, client, run)
        usage = run.per_sheet.get(hall_ticket)
        result.gemini_usage = usage.as_dict(config.GEMINI_USD_INR) if usage else {}
    return result
