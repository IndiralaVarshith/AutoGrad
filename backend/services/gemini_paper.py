"""Question paper to structured questions and a draft rubric, in one Gemini request.

Marks come only from what the paper prints. Gemini must quote where each mark is
printed; code then checks the quote and adds up the totals. A mismatch is listed
for the teacher and is never scaled or adjusted to fit.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

import config
from services.gemini_client import GeminiClient, RunBudget, get_client
from services.paper_intake import apply_review_items

PAPER_PROMPT_VERSION = "gemini-paper-v1"


class QuestionFormat(str, Enum):
    MCQ = "MCQ"
    SHORT_ANSWER = "SHORT_ANSWER"
    LONG_ANSWER = "LONG_ANSWER"


class PaperOption(BaseModel):
    label: str
    text: str


class PaperCriterion(BaseModel):
    description: str
    max_marks: float


class PaperSection(BaseModel):
    section_id: str = Field(description="Short id, such as A or I. Invent 'A' if the paper has no sections.")
    title: str = ""
    instruction_text: str = Field(default="", description="The section's instructions, word for word.")
    answer_any_k: int | None = Field(
        default=None,
        description="K when the paper says to answer any K questions in this section, else null.",
    )
    printed_section_marks: float | None = Field(
        default=None, description="The section total if printed, else null. Never calculate it."
    )


class PaperQuestion(BaseModel):
    question_number: int = Field(description="The printed top-level question number.")
    sub_part: str = Field(default="", description="The printed sub-part, such as a or a.ii. Empty if none.")
    section_id: str
    question_text: str = Field(description="The question word for word, without its options.")
    question_format: QuestionFormat
    options: list[PaperOption] = Field(default_factory=list)
    printed_marks: float | None = Field(
        default=None,
        description="Marks printed for this question or part, or printed as a per-question value "
        "for its section. Null if the paper prints none. Never estimate.",
    )
    marks_evidence: str = Field(
        default="",
        description="The exact printed text the marks come from, such as '(1 Mark Each)'. "
        "Empty if printed_marks is null.",
    )
    choice_group: str = Field(
        default="",
        description="Same id on every question or part that is an alternative in one OR choice. "
        "Empty if no OR choice applies.",
    )
    choice_alternative: str = Field(
        default="",
        description="Which side of the OR this belongs to, when one alternative has several parts.",
    )
    has_figure: bool = False
    figure_page: int | None = None
    draft_criteria: list[PaperCriterion] = Field(
        default_factory=list,
        description="Draft marking points. Their max_marks must add up to printed_marks. "
        "Empty if printed_marks is null.",
    )
    suggested_option: str = Field(
        default="",
        description="MCQ only: the option label for the right answer, from the answer key if one "
        "was given, otherwise your suggestion for the teacher to confirm.",
    )


class PaperExtraction(BaseModel):
    readable: bool = Field(description="False if the document is not a readable question paper.")
    exam_title: str = ""
    printed_total_marks: float | None = Field(
        default=None, description="The paper's total if printed, else null. Never calculate it."
    )
    sections: list[PaperSection] = Field(default_factory=list)
    questions: list[PaperQuestion] = Field(default_factory=list)
    unclear_items: list[str] = Field(
        default_factory=list,
        description="Anything you could not read or were unsure about, one item per line.",
    )


PAPER_INSTRUCTIONS = """You read exam question papers of any subject and layout.

Return every question in the order printed. Copy question text and options word for word.

Structure:
- A question with printed sub-parts is one entry per sub-part, with the same question_number.
- "Answer any K of N" sets answer_any_k on the section. Do not drop any questions.
- For an OR choice, return every alternative as its own entry with the same choice_group.
  If an alternative has several parts, also give those parts the same choice_alternative.

Marks:
- Use only marks the paper prints. Never estimate or divide up a total.
- If no marks are printed for a question, printed_marks is null. Say so in unclear_items.
- marks_evidence is the printed text copied exactly.

Draft rubric:
- For each question with printed marks, draft marking points a teacher can edit.
- If an answer key is given, base the points on it and use it for suggested_option.
- If a key's entry conflicts with the paper, keep the paper's question and say so in unclear_items.
- These are drafts. A teacher approves them later.

If something is unclear, say so in unclear_items rather than guessing."""


def _label(question: dict[str, Any]) -> str:
    return f"Q{question.get('question_number')}{question.get('sub_part') or ''}"


def _squeeze(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def _number_in_text(value: float, text: str) -> bool:
    for found in re.findall(r"\d+(?:\.\d+)?", text or ""):
        if abs(float(found) - value) < 0.001:
            return True
    return False


def _sum_alternatives(parts: list[dict[str, Any]]) -> dict[str, float]:
    alternatives: dict[str, float] = {}
    for part in parts:
        side = str(part.get("choice_alternative") or "") or _label(part)
        alternatives[side] = alternatives.get(side, 0.0) + float(part.get("max_marks") or 0.0)
    return alternatives


def question_units(questions: list[dict[str, Any]]) -> dict[int, int]:
    """Map each question number to its unit. OR choices across numbers merge into one unit."""
    unit_of: dict[int, int] = {int(q["question_number"]): int(q["question_number"]) for q in questions}
    groups: dict[str, set[int]] = {}
    for question in questions:
        group = str(question.get("choice_group") or "")
        if group:
            groups.setdefault(group, set()).add(int(question["question_number"]))
    for members in groups.values():
        numbers = sorted(members)
        for number in numbers[1:]:
            old_unit = unit_of[number]
            for key, value in unit_of.items():
                if value == old_unit:
                    unit_of[key] = unit_of[numbers[0]]
    return unit_of


def calculate_section_total(
    questions: list[dict[str, Any]], answer_any_k: int | None, section_id: str
) -> tuple[float, list[str], list[str]]:
    """Return a section's maximum, problems and notes. Nothing is adjusted to fit."""
    problems: list[str] = []
    notes: list[str] = []
    unit_of = question_units(questions)
    groups: dict[str, list[dict[str, Any]]] = {}
    for question in questions:
        group = str(question.get("choice_group") or "")
        if group:
            groups.setdefault(group, []).append(question)

    values: dict[int, float] = {}
    for question in questions:
        if not question.get("choice_group"):
            unit = unit_of[int(question["question_number"])]
            values[unit] = values.get(unit, 0.0) + float(question.get("max_marks") or 0.0)
    for group, parts in groups.items():
        alternatives = _sum_alternatives(parts)
        if len(alternatives) < 2:
            problems.append(f"OR choice '{group}' has only one alternative: {_label(parts[0])}.")
        if len(set(alternatives.values())) > 1:
            problems.append(
                f"OR choice '{group}' has alternatives worth different marks: {alternatives}. "
                "The higher value was counted; please check."
            )
        unit = unit_of[int(parts[0]["question_number"])]
        values[unit] = values.get(unit, 0.0) + max(alternatives.values())

    if answer_any_k is None:
        return round(sum(values.values()), 2), problems, notes
    if answer_any_k <= 0 or answer_any_k > len(values):
        problems.append(
            f"Section {section_id}: says answer any {answer_any_k}, but {len(values)} questions were read."
        )
        return round(sum(values.values()), 2), problems, notes
    if len(set(values.values())) > 1:
        problems.append(
            f"Section {section_id}: 'any {answer_any_k}' covers questions worth different marks. "
            "The highest values were counted; please check."
        )
    best = sorted(values.values(), reverse=True)[:answer_any_k]
    notes.append(f"Section {section_id}: any {answer_any_k} of {len(values)} counted.")
    return round(sum(best), 2), problems, notes


def calculate_paper_total(
    questions: list[dict[str, Any]], sections: list[dict[str, Any]], printed_total: float | None
) -> tuple[float, list[str], list[str]]:
    """Add up the paper from its structure and compare with every printed total."""
    problems: list[str] = []
    notes: list[str] = []
    known = {str(section.get("section_id")): section for section in sections}
    by_section: dict[str, list[dict[str, Any]]] = {}
    for question in questions:
        by_section.setdefault(str(question.get("section") or ""), []).append(question)

    total = 0.0
    for section_id, members in by_section.items():
        section = known.get(section_id, {})
        section_total, section_problems, section_notes = calculate_section_total(
            members, section.get("answer_any_k"), section_id
        )
        problems.extend(section_problems)
        notes.extend(section_notes)
        printed = section.get("printed_section_marks")
        if printed is not None and abs(float(printed) - section_total) > 0.01:
            problems.append(
                f"Section {section_id}: prints {float(printed):g} marks, but its questions add up "
                f"to {section_total:g}."
            )
        total += section_total
    total = round(total, 2)

    if printed_total is None:
        notes.append(f"The paper prints no total. {total:g} was calculated; please confirm it.")
    elif abs(float(printed_total) - total) > 0.01:
        problems.append(f"The paper prints a total of {float(printed_total):g}, but its questions add up to {total:g}.")
    return total, problems, notes


def _question_record(question: PaperQuestion, section: dict[str, Any]) -> dict[str, Any]:
    is_mcq = question.question_format == QuestionFormat.MCQ
    marks = question.printed_marks if (question.printed_marks or 0) > 0 else None
    return {
        "question_number": question.question_number,
        "sub_part": question.sub_part.strip(),
        "question_text": question.question_text.strip(),
        "question_type": "THEORY",
        "question_format": question.question_format.value,
        "options": [{"label": o.label.strip().upper(), "text": o.text.strip()} for o in question.options],
        "max_marks": marks or 0.0,
        "marks": marks or 0.0,
        "marks_evidence": question.marks_evidence.strip(),
        "section": question.section_id.strip(),
        "section_attempt_count": section.get("answer_any_k"),
        "section_max_marks": section.get("printed_section_marks"),
        "attempt_instruction": section.get("instruction_text", ""),
        "choice_group": question.choice_group.strip(),
        "choice_alternative": question.choice_alternative.strip(),
        "is_or_choice": bool(question.choice_group.strip()),
        "has_figure": question.has_figure,
        "figure_page": question.figure_page,
        "criteria": [
            {"description": c.description.strip(), "max_marks": float(c.max_marks)}
            for c in question.draft_criteria
        ],
        "correct_option": "",
        # A suggestion only. The teacher's approved rubric holds the real key.
        "suggested_option": question.suggested_option.strip().upper() if is_mcq else "",
    }


def _question_problems(record: dict[str, Any], paper_text: str) -> list[str]:
    label = _label(record)
    problems: list[str] = []
    if not record["question_text"]:
        problems.append(f"{label}: the question text was not read.")
    marks = record["max_marks"]
    if marks <= 0:
        problems.append(f"{label}: the paper prints no marks for this. Enter them from the paper.")
    else:
        evidence = record["marks_evidence"]
        if not _number_in_text(marks, evidence):
            problems.append(f"{label}: {marks:g} marks could not be matched to printed text ('{evidence}').")
        elif paper_text and _squeeze(evidence) not in _squeeze(paper_text):
            problems.append(f"{label}: the marks text '{evidence}' was not found on the paper.")
        criteria_sum = sum(c["max_marks"] for c in record["criteria"])
        if record["criteria"] and abs(criteria_sum - marks) > 0.01:
            problems.append(f"{label}: criteria add up to {criteria_sum:g}, but the question is worth {marks:g}.")
    if record["question_format"] == "MCQ":
        labels = [option["label"] for option in record["options"]]
        if len(labels) < 2 or len(set(labels)) != len(labels):
            problems.append(f"{label}: multiple-choice question without clear options: {labels}.")
        elif record["suggested_option"] and record["suggested_option"] not in labels:
            problems.append(f"{label}: suggested answer '{record['suggested_option']}' is not an option.")
    return problems


def paper_from_extraction(extraction: PaperExtraction, paper_text: str = "") -> dict[str, Any]:
    """Turn Gemini's reply into a paper record, listing everything the teacher must check."""
    sections = [section.model_dump() for section in extraction.sections]
    known = {section["section_id"].strip(): section for section in sections}
    records: list[dict[str, Any]] = []
    problems: list[str] = [f"Gemini was unsure: {item}" for item in extraction.unclear_items if item.strip()]
    seen: set[tuple[int, str]] = set()
    for question in extraction.questions:
        section = known.get(question.section_id.strip(), {})
        record = _question_record(question, section)
        key = (record["question_number"], record["sub_part"].upper())
        if key in seen:
            problems.append(f"{_label(record)}: this question number appears more than once.")
        seen.add(key)
        if question.section_id.strip() not in known:
            problems.append(f"{_label(record)}: belongs to section '{question.section_id}', which was not read.")
        problems.extend(_question_problems(record, paper_text))
        records.append(record)

    total, total_problems, notes = calculate_paper_total(records, sections, extraction.printed_total_marks)
    problems.extend(total_problems)
    printed = extraction.printed_total_marks
    return {
        "exam_title": extraction.exam_title.strip(),
        "questions": records,
        "question_count": len(records),
        "total_marks": total,
        "declared_maximum_marks": printed,
        "calculated_maximum_marks": total,
        "validation_status": "DISCREPANCY" if total_problems else "VALID",
        "reconciliation_notes": notes,
        "sections": sorted(known),
        "section_details": sections,
        "extraction_warnings": [],
        "problems": problems,
        "needs_teacher_review": bool(problems),
        "review_items": apply_review_items(records),
        "verification_status": "UNVERIFIED",
        "source_kind": "gemini",
        "prompt_version": PAPER_PROMPT_VERSION,
        "reader_model": config.GEMINI_MODEL,
    }


def rubric_total_problems(
    questions: list[dict[str, Any]], total_marks: float | None
) -> tuple[float, list[str], list[str]]:
    """Total a teacher-edited rubric at approval: (total, blocking problems, warnings).

    total_marks is the paper's printed total. None means the teacher accepted the
    calculated total, so only the questions themselves are checked.
    """
    sections: dict[str, dict[str, Any]] = {}
    for question in questions:
        section_id = str(question.get("section") or "")
        sections.setdefault(
            section_id,
            {
                "section_id": section_id,
                "answer_any_k": question.get("section_attempt_count"),
                "printed_section_marks": question.get("section_max_marks"),
            },
        )
    total, warnings, _ = calculate_paper_total(questions, list(sections.values()), None)
    problems: list[str] = []
    if total_marks is not None and abs(float(total_marks) - total) > 0.01:
        problems.append(
            f"The paper prints a total of {float(total_marks):g}, but the questions add up to {total:g}."
        )
    for question in questions:
        marks = float(question.get("max_marks") or 0.0)
        if marks <= 0:
            problems.append(f"{_label(question)}: has no marks.")
        criteria_sum = sum(float(c.get("max_marks") or 0.0) for c in question.get("criteria") or [])
        if abs(criteria_sum - marks) > 0.01:
            problems.append(f"{_label(question)}: criteria add up to {criteria_sum:g}, but the question is worth {marks:g}.")
    return total, problems, warnings


def _document_part(data: bytes, extension: str) -> Any:
    from google.genai import types

    mime = {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }.get(extension)
    if mime is None:
        return types.Part.from_text(text=data.decode("utf-8", errors="replace"))
    return types.Part.from_bytes(data=data, mime_type=mime)


def embedded_text(data: bytes, extension: str) -> str:
    """Printed text when the file has any. Empty for scans, which skips the quote check."""
    if extension == ".txt":
        return data.decode("utf-8", errors="replace")
    if extension != ".pdf":
        return ""
    import pymupdf

    with pymupdf.open(stream=data, filetype="pdf") as document:
        return "\n".join(page.get_text() for page in document)


def read_question_paper(
    data: bytes,
    extension: str,
    *,
    answer_key: bytes | None = None,
    answer_key_extension: str = ".txt",
    client: GeminiClient | None = None,
    run: RunBudget | None = None,
) -> dict[str, Any]:
    """One request. Raises a GeminiError on failure, and then nothing gets saved."""
    client = client or get_client()
    run = run or client.new_run()
    contents: list[Any] = ["QUESTION PAPER:", _document_part(data, extension)]
    if answer_key:
        contents += ["ANSWER KEY:", _document_part(answer_key, answer_key_extension)]
    result = client.generate(
        run,
        kind="question_paper",
        sheet_id="question_paper",
        contents=contents,
        response_schema=PaperExtraction,
        system_instruction=PAPER_INSTRUCTIONS,
    )
    extraction = (
        result.parsed
        if isinstance(result.parsed, PaperExtraction)
        else PaperExtraction.model_validate(result.parsed)
    )
    if not extraction.readable or not extraction.questions:
        return {"unreadable": True, "unclear_items": extraction.unclear_items}
    paper = paper_from_extraction(extraction, embedded_text(data, extension))
    paper["finish_reason"] = result.finish_reason
    if result.finish_reason not in ("STOP", ""):
        paper["problems"].append(
            f"Gemini's reply stopped early ({result.finish_reason}), so questions may be missing."
        )
        paper["needs_teacher_review"] = True
    return paper
