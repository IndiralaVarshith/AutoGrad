"""Question paper to structured questions and a draft rubric, in one Gemini request.

Marks come only from what the paper prints. Gemini must quote where each mark is
printed; code then checks the quote and adds up the totals. A mismatch is listed
for the teacher and is never scaled or adjusted to fit.
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

import config
from services.gemini_client import GeminiCallFailed, GeminiClient, ReplyCutOff, RunBudget, get_client
from services.paper_intake import apply_review_items

PAPER_PROMPT_VERSION = "gemini-paper-v4"


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
- A printed formula such as "2 x 4 = 8" means 2 answers of 4 marks each count in that section.
- For an OR choice, return every alternative as its own entry with the same choice_group.
  If an alternative has several parts, also give those parts the same choice_alternative.
- An internal choice, such as 9(A) OR 9(B) and 10(A) OR 10(B), is one OR choice per question
  number: give 9(A) and 9(B) one choice_group, and 10(A) and 10(B) another. "Answer any one
  alternative" of each question is not "answer any one question": leave answer_any_k null.

Marks:
- Use only marks the paper prints. Never estimate or divide up a total.
- Marks printed once for a section as "N x M = T" are M marks for each question in it: give
  every such question printed_marks M, with the formula as marks_evidence.
- If no marks are printed for a question, printed_marks is null. Say so in unclear_items.
- marks_evidence is the printed text copied exactly.

Draft rubric:
- For each question with printed marks, draft marking points a teacher can edit.
- If an answer key is given, base the points on it and use it for suggested_option.
- If a key's entry conflicts with the paper, keep the paper's question and say so in unclear_items.
- These are drafts. A teacher approves them later.

If something is unclear, say so in unclear_items rather than guessing.

Keep the reply short:
- Copy question text in full. Every other text, each marking point and each unclear_items entry,
  is one plain sentence of at most 30 words.
- Never write your reasoning, doubts or second thoughts inside the reply. Decide, write one short
  unclear_items line if needed, and move on to the next question.
- A question that depends on a figure, diagram or graph you cannot read clearly is still returned
  with its printed marks and has_figure true. Its marking point can be "Correct answer as shown
  in the figure (teacher to confirm)"."""


PAPER_TYPES = ("MCQ", "WRITTEN", "MIXED")

_PAPER_TYPE_HINT = {
    "MCQ": (
        "\n\nThe teacher says every question on this paper is multiple choice. Give each one the"
        " MCQ format and its options. Draft no marking points beyond the option."
    ),
    "WRITTEN": (
        "\n\nThe teacher says this paper has no multiple-choice questions: every answer is written"
        " or drawn. Do not give any question the MCQ format unless it prints lettered options."
    ),
}


def normal_paper_type(value: str | None) -> str:
    """MCQ, WRITTEN or MIXED. Anything else, or nothing, is MIXED: read whatever is printed."""
    kind = str(value or "").strip().upper()
    return kind if kind in PAPER_TYPES else "MIXED"


def paper_type_mismatch(paper_type: str, questions: list[dict[str, Any]]) -> str:
    """Say so when the paper does not match what the teacher ticked. Empty when it does."""
    mcq = [_label(q) for q in questions if q.get("question_format") == "MCQ"]
    written = [_label(q) for q in questions if q.get("question_format") != "MCQ"]
    if paper_type == "MCQ" and written:
        return (
            "You chose a multiple-choice paper, but these look like written questions: "
            f"{', '.join(written[:8])}. Check them, or choose Mixed."
        )
    if paper_type == "WRITTEN" and mcq:
        return (
            "You chose a handwritten paper, but these look like multiple-choice questions: "
            f"{', '.join(mcq[:8])}. Check them, or choose Mixed."
        )
    return ""


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


_FORMULA = re.compile(
    r"(\d+)\s*[xX*×]\s*(\d+(?:\.\d+)?)\s*=\s*(\d+(?:\.\d+)?)(?:\s*(?:marks|mark|m)\b)?",
    re.IGNORECASE,
)


def fill_marks_from_section_formula(
    records: list[dict[str, Any]], sections: list[dict[str, Any]], paper_text: str = ""
) -> list[str]:
    """Give a question its marks from its section's printed "N x M = T" when none are printed.

    Papers often print marks once per section, such as "4 x 2 = 8m": every question there is
    worth 2. Only a whole question with no marks at all is filled. Sub-parts share the marks
    equally only when that gives half-mark steps, and the teacher is told. Returns notes.
    """
    notes: list[str] = []
    for section in sections:
        section_id = str(section.get("section_id") or "").strip()
        match = _FORMULA.search(str(section.get("instruction_text") or ""))
        if not match:
            continue
        count, each, total = int(match.group(1)), float(match.group(2)), float(match.group(3))
        if count <= 0 or abs(count * each - total) > 0.01:
            continue  # A formula that does not add up proves nothing.
        evidence = match.group(0)
        if paper_text and _squeeze(evidence) not in _squeeze(paper_text):
            continue
        by_number: dict[int, list[dict[str, Any]]] = {}
        for record in records:
            if str(record.get("section") or "") == section_id:
                by_number.setdefault(int(record["question_number"]), []).append(record)
        for number, parts in by_number.items():
            if any(float(part.get("max_marks") or 0) > 0 for part in parts):
                continue
            alternatives = _sum_alternatives(parts) if parts[0].get("choice_group") else None
            if alternatives is not None and len(alternatives) == len(parts):
                shares = {id(part): each for part in parts}  # Each OR alternative is worth M.
            elif len(parts) == 1:
                shares = {id(parts[0]): each}
            else:
                share = each / len(parts)
                if abs(share * 2 - round(share * 2)) > 1e-6:
                    continue
                shares = {id(part): share for part in parts}
                notes.append(
                    f"Q{number}: the section prints {evidence}, so Q{number} is worth {each:g}. "
                    f"Its {len(parts)} parts were given {share:g} each. Please check."
                )
            for part in parts:
                marks = shares[id(part)]
                part["max_marks"] = part["marks"] = marks
                part["marks_evidence"] = evidence
                part["marks_from_section"] = True
                if not part.get("criteria"):
                    part["criteria"] = [{"description": "Correct and complete answer", "max_marks": marks}]
            if len(parts) == 1 or alternatives is not None:
                notes.append(f"Q{number}: worth {each:g}, from the section's printed {evidence}.")
    return notes


def _section_of(records: list[dict[str, Any]], section_id: str) -> list[dict[str, Any]]:
    return [r for r in records if str(r.get("section") or "") == section_id]


def _choice_repairs(members: list[dict[str, Any]]) -> list[tuple[str, Any]]:
    """Ways an internal choice is commonly misread, from the smallest change up.

    Each is (what it changes, a function that applies it to the section's records).
    """
    repairs: list[tuple[str, Any]] = []
    any_k = next((m.get("section_attempt_count") for m in members if m.get("section_attempt_count")), None)
    groups: dict[str, set[int]] = {}
    for member in members:
        if member.get("choice_group"):
            groups.setdefault(str(member["choice_group"]), set()).add(int(member["question_number"]))

    def drop_any_k(records: list[dict[str, Any]]) -> None:
        for record in records:
            record["section_attempt_count"] = None

    # One OR choice that spans several question numbers, each with its own alternatives,
    # is really one choice per question: 9(A) or 9(B), and 10(A) or 10(B).
    spanning = {
        group: numbers for group, numbers in groups.items()
        if len(numbers) > 1 and all(
            len(_sum_alternatives([m for m in members if m.get("choice_group") == group
                                   and int(m["question_number"]) == number])) >= 2
            for number in numbers
        )
    }

    def split_groups(records: list[dict[str, Any]]) -> None:
        for record in records:
            if record.get("choice_group") in spanning:
                record["choice_group"] = f"{record['choice_group']}-Q{record['question_number']}"

    if any_k and groups:
        repairs.append((f"'answer any {any_k}' read as an internal choice, not a limit on questions", drop_any_k))
    if spanning:
        repairs.append(("one OR choice split into one per question number", split_groups))
    if any_k and groups and spanning:
        def both(records: list[dict[str, Any]]) -> None:
            drop_any_k(records)
            split_groups(records)
        repairs.append(("both of the above", both))
    return repairs


def repair_choice_structure(
    records: list[dict[str, Any]], printed_total: float | None
) -> tuple[list[dict[str, Any]], list[str]]:
    """Fix a misread internal choice when the paper's own printed totals prove it.

    Only how the questions are grouped changes; no question's marks change. A repair is
    used only when it makes the section match its printed marks (or, if none are printed,
    the whole paper match its printed total). Returns new records and notes for the teacher.
    """
    import copy

    records = copy.deepcopy(records)
    notes: list[str] = []

    def section_total(section_records: list[dict[str, Any]], section_id: str) -> float:
        any_k = next((r.get("section_attempt_count") for r in section_records if r.get("section_attempt_count")), None)
        return calculate_section_total(section_records, any_k, section_id)[0]

    def paper_total(all_records: list[dict[str, Any]]) -> float:
        return round(sum(
            section_total(_section_of(all_records, s), s)
            for s in dict.fromkeys(str(r.get("section") or "") for r in all_records)
        ), 2)

    for section_id in dict.fromkeys(str(r.get("section") or "") for r in records):
        members = _section_of(records, section_id)
        printed = next((r.get("section_max_marks") for r in members if r.get("section_max_marks") is not None), None)
        current = section_total(members, section_id)
        if printed is not None:
            if abs(float(printed) - current) <= 0.01:
                continue
        elif printed_total is None or abs(float(printed_total) - paper_total(records)) <= 0.01:
            continue
        for description, apply in _choice_repairs(members):
            trial = copy.deepcopy(records)
            apply(_section_of(trial, section_id))
            fixed = section_total(_section_of(trial, section_id), section_id)
            matches = (
                abs(float(printed) - fixed) <= 0.01 if printed is not None
                else abs(float(printed_total) - paper_total(trial)) <= 0.01
            )
            if matches:
                records = trial
                notes.append(
                    f"Section {section_id}: the choices were read again to match the printed marks "
                    f"({description}). It now counts {fixed:g} marks, as printed. Please check."
                )
                break
    return records, notes


def _question_record(question: PaperQuestion, section: dict[str, Any]) -> dict[str, Any]:
    is_mcq = question.question_format == QuestionFormat.MCQ
    marks = question.printed_marks if (question.printed_marks or 0) > 0 else None
    criteria = [
        {"description": c.description.strip(), "max_marks": float(c.max_marks)}
        for c in question.draft_criteria
    ]
    if is_mcq and marks:
        # Code marks MCQs from the chosen letter, so the only point is the whole mark.
        criteria = [{"description": "Selects the correct option", "max_marks": float(marks)}]
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
        "criteria": criteria,
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
        if record.get("marks_from_section"):
            pass  # Taken from the section's printed formula, which was already checked.
        elif not _number_in_text(marks, evidence):
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
    problems: list[str] = [f"AutoGrade was unsure: {item}" for item in extraction.unclear_items if item.strip()]
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
        records.append(record)
    formula_notes = fill_marks_from_section_formula(records, sections, paper_text)
    for record in records:
        problems.extend(_question_problems(record, paper_text))
    if formula_notes:
        # Gemini's own doubts about marks the printed formula has now settled are not repeated.
        problems = [
            p for p in problems
            if not (p.startswith("AutoGrade was unsure:") and re.search(r"\bmarks?\b", p, re.IGNORECASE)
                    and any(f"Q{n}" in note for note in formula_notes for n in re.findall(r"\d+", p)[:1]))
        ]

    records, repair_notes = repair_choice_structure(records, extraction.printed_total_marks)
    for section in sections:
        # The totals below read the section's rule, so it follows any repair.
        members = _section_of(records, section["section_id"].strip())
        if members and all(m.get("section_attempt_count") is None for m in members):
            section["answer_any_k"] = None
    total, total_problems, notes = calculate_paper_total(records, sections, extraction.printed_total_marks)
    notes = formula_notes + repair_notes + notes
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
        text = "\n".join(page.get_text() for page in document)
    # A scan has no text layer: only line breaks come back, and nothing can be checked against it.
    return text if text.strip() else ""


_AFTER_CUT_OFF = """

Your last reply for this paper never finished: one field kept repeating the same doubt until
the reply ran out of room. This time keep every field to one short sentence, put any doubt in
a single unclear_items line, and finish the JSON."""


def _keep_cut_off_reply(error: ReplyCutOff, label: str) -> None:
    """Keep a cut-off paper reply on this machine, so the cause can be seen.

    It holds the question paper's text only, never a student's work. The folder is git-ignored.
    """
    try:
        folder = Path(config.GEMINI_USAGE_DIR)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "cut_off_paper_reply.txt"
        mode = "w" if label == "first try" else "a"
        with path.open(mode) as handle:
            handle.write(f"===== {label}: {error} =====\n{error.text}\n")
        print(f"[Paper] {error} Reply kept in {path}. It ends: ...{error.text[-160:]!r}")
    except OSError:
        pass


def read_question_paper(
    data: bytes,
    extension: str,
    *,
    answer_key: bytes | None = None,
    answer_key_extension: str = ".txt",
    client: GeminiClient | None = None,
    run: RunBudget | None = None,
    paper_type: str = "",
) -> dict[str, Any]:
    """One request. Raises a GeminiError on failure, and then nothing gets saved."""
    paper_type = normal_paper_type(paper_type)
    client = client or get_client()
    run = run or client.new_run()
    contents: list[Any] = ["QUESTION PAPER:", _document_part(data, extension)]
    if answer_key:
        contents += ["ANSWER KEY:", _document_part(answer_key, answer_key_extension)]
    request = dict(
        sheet_id="question_paper",
        contents=contents,
        response_schema=PaperExtraction,
        system_instruction=PAPER_INSTRUCTIONS + _PAPER_TYPE_HINT.get(paper_type, ""),
    )
    try:
        result = client.generate(run, kind="question_paper", **request)
    except ReplyCutOff as first:
        _keep_cut_off_reply(first, "first try")
        # The paper is fine: the reply ran out of room, or got stuck repeating itself.
        # The same request at temperature 0 would be cut off the same way, so this one
        # try gets more room and a little randomness. A second cut-off is raised.
        retry = dict(
            request,
            temperature=config.GEMINI_PAPER_RETRY_TEMPERATURE,
            system_instruction=request["system_instruction"] + _AFTER_CUT_OFF,
        )
        try:
            result = client.generate(
                run, kind="question_paper_retry",
                max_output_tokens=config.GEMINI_PAPER_RETRY_OUTPUT_TOKENS, **retry,
            )
        except ReplyCutOff as second:
            _keep_cut_off_reply(second, "second try")
            raise
        except GeminiCallFailed as error:
            if error.transient:
                raise
            # The model refused the larger limit: keep the usual one.
            result = client.generate(run, kind="question_paper_retry", **retry)
    extraction = (
        result.parsed
        if isinstance(result.parsed, PaperExtraction)
        else PaperExtraction.model_validate(result.parsed)
    )
    if not extraction.readable or not extraction.questions:
        return {"unreadable": True, "unclear_items": extraction.unclear_items}
    paper = paper_from_extraction(extraction, embedded_text(data, extension))
    paper["finish_reason"] = result.finish_reason
    paper["paper_type"] = paper_type
    mismatch = paper_type_mismatch(paper_type, paper["questions"])
    if mismatch:
        # The paper wins over the tick box: nothing is forced, the teacher is told.
        paper["problems"].append(mismatch)
        paper["needs_teacher_review"] = True
    if result.finish_reason not in ("STOP", ""):
        paper["problems"].append(
            f"The reading stopped early ({result.finish_reason}), so questions may be missing."
        )
        paper["needs_teacher_review"] = True
    return paper
