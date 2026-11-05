"""
AutoGrade — Question Paper Intake

Two checks that run before a question paper is trusted:
  - parse_question_paper_text(): deterministic parsing of plain-text papers, no AI.
  - check_image_quality(): rejects blank or unreadable scans before any model call.

Marks only ever come from what the paper prints. Anything unclear is returned as
a problem for the teacher; nothing is filled in.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Any

from PIL import Image, ImageFilter, ImageStat

from services.marks_validator import (
    extract_declared_marks_from_text,
    parse_question_number_and_part,
    parse_section_formula,
    read_marks,
)
from services.ocr import extract_page_regions

# Calibrated on one printed scan (test_data/question_paper.pdf, 150 dpi) with
# Gaussian blur: OCR reads ~90% of the text up to radius 3 and collapses at 4.
# Other scanners or handwritten papers may need different values.
MIN_PAGE_CHARS = 50
MIN_SHARPNESS = 12.0
_SHARPNESS_MAX_SIDE = 1200
_LAPLACIAN = ImageFilter.Kernel(
    (3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128
)

_SECTION_RE = re.compile(
    r"^(?:part|section)\s+([ivx]+|\d+|[a-z])\s*[:.\-–—]\s*(.*)$", re.IGNORECASE
)
_MARKS_EACH_RE = re.compile(r"\(\s*(\d+(?:\.\d+)?)\s*marks?\s+each\s*\)", re.IGNORECASE)
_MCQ_HEADER_RE = re.compile(r"multiple[\s-]+choice|\bmcqs?\b", re.IGNORECASE)
_QUESTION_RE = re.compile(r"^(\d+)\s*[.)]\s+(.+)$")
_OPTION_RE = re.compile(r"^([A-Z])\s*[.)]\s+(.+)$")


class QualityCheckUnavailable(RuntimeError):
    """The local OCR engine could not run, so the upload could not be checked."""


@dataclass
class ParsedPaper:
    raw: dict[str, Any]  # shape accepted by validate_and_reconcile_exam_structure
    questions: list[dict[str, Any]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


# =========================================================
# TEXT PAPERS
# =========================================================

def _section_marks(header: str) -> float | None:
    each = _MARKS_EACH_RE.search(header)
    if each:
        return read_marks(each.group(1))
    return read_marks(parse_section_formula(header)[1])


def parse_question_paper_text(text: str) -> ParsedPaper:
    """Parse a plain-text paper into sections, questions, options and printed marks."""
    sections: list[dict[str, Any]] = []
    section: dict[str, Any] | None = None
    question: dict[str, Any] | None = None
    last_option: dict[str, str] | None = None
    preamble: list[str] = []
    problems: list[str] = []
    numbers: list[int] = []

    for line in (raw.strip() for raw in text.splitlines()):
        if not line:
            continue

        header = _SECTION_RE.match(line)
        if header:
            marks = _section_marks(line)
            section = {
                "section_id": header.group(1).upper(),
                "section_title": header.group(2).strip(),
                "attempt_instruction": line,
                "marks_per_question": marks,
                "questions": [],
            }
            sections.append(section)
            question, last_option = None, None
            if marks is None:
                problems.append(
                    f"Part {section['section_id']}: no printed marks per question; "
                    "teacher must enter them."
                )
            continue

        numbered = _QUESTION_RE.match(line)
        if numbered:
            number = int(numbered.group(1))
            numbers.append(number)
            if section is None:
                problems.append(f"Q{number}: appears outside any part, so it has no printed marks.")
                section = {"section_id": "", "section_title": "", "attempt_instruction": "",
                           "marks_per_question": None, "questions": []}
                sections.append(section)
            question = {
                "question_number": number,
                "question_text": numbered.group(2).strip(),
                "question_type": "THEORY",
                "options": [],
                "criteria": [],
            }
            if section["marks_per_question"] is not None:
                question["marks"] = section["marks_per_question"]
            section["questions"].append(question)
            last_option = None
            continue

        option = _OPTION_RE.match(line)
        if option and question is not None:
            last_option = {"label": option.group(1), "text": option.group(2).strip()}
            question["options"].append(last_option)
            continue

        # Unnumbered text: a wrapped line of the current item, or instructions/title.
        if last_option is not None:
            last_option["text"] += f" {line}"
        elif question is not None:
            question["question_text"] += f" {line}"
        elif section is None:
            preamble.append(line)

    questions = _flatten(sections, problems)

    for number in sorted({n for n in numbers if numbers.count(n) > 1}):
        problems.append(f"Q{number}: the question number appears more than once.")
    if numbers:
        missing = sorted(set(range(1, max(numbers) + 1)) - set(numbers))
        if missing:
            problems.append(f"Question numbers missing from the paper: {missing}.")

    raw = {
        "exam_title": preamble[0] if preamble else "",
        "declared_maximum_marks": extract_declared_marks_from_text("\n".join(preamble)),
        "sections": sections,
    }
    return ParsedPaper(raw=raw, questions=questions, problems=problems)


def _flatten(sections: list[dict[str, Any]], problems: list[str]) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    for section in sections:
        expects_mcq = bool(_MCQ_HEADER_RE.search(section["attempt_instruction"]))
        for q in section["questions"]:
            labels = [item["label"] for item in q["options"]]
            is_mcq = bool(labels)
            if expects_mcq and len(labels) < 2:
                problems.append(f"Q{q['question_number']}: multiple-choice question without options.")
            elif labels and labels != [chr(ord("A") + i) for i in range(len(labels))]:
                problems.append(f"Q{q['question_number']}: options are out of order: {labels}.")
            q["question_format"] = "MCQ" if is_mcq or expects_mcq else "SHORT_ANSWER"
            questions.append({
                "question_number": q["question_number"],
                "sub_part": "",
                "question_text": q["question_text"],
                "question_type": q["question_type"],
                "question_format": q["question_format"],
                "options": q["options"],
                "max_marks": q.get("marks", 0.0),
                "section": section["section_id"],
                "criteria": [],
            })
    return questions


# =========================================================
# MODEL REPLIES
# =========================================================

def questions_from_model_reply(reply: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Return one question list from a model reply, plus problems for the teacher.

    Sections are the source of truth because the validator totals them, so the
    saved questions and the saved total cannot disagree.
    """
    sectioned: list[dict[str, Any]] = []
    for s_idx, section in enumerate(reply.get("sections") or []):
        section_id = str(section.get("section_id", section.get("id", f"SEC_{s_idx + 1}"))).strip()
        section["section_id"] = section_id
        for q in section.get("questions") or []:
            sectioned.append({**q, "section": section_id})
    flat = reply.get("questions") or []
    questions = sectioned or flat

    problems: list[str] = []
    if sectioned and flat and _ids(sectioned) != _ids(flat):
        problems.append("The model returned two different question lists for this paper.")

    seen: set[tuple[int, str]] = set()
    for idx, q in enumerate(questions, start=1):
        if q.get("question_number") in (None, ""):
            problems.append(f"Question at position {idx}: no question number was read.")
            continue
        number, part = parse_question_number_and_part(q.get("question_number"), idx)
        key = (number, str(q.get("sub_part") or part).strip().upper())
        if key in seen:
            problems.append(f"Q{key[0]}{key[1]}: the question number appears more than once.")
        seen.add(key)
        question_text = str(q.get("question_text", ""))
        if not question_text.strip():
            problems.append(f"Q{key[0]}{key[1]}: the question text was not read.")
        if re.search(r"\(\s*or\s*\)", question_text, re.IGNORECASE):
            problems.append(f"Q{key[0]}{key[1]}: two alternative questions were merged into one.")
    return questions, problems


def _ids(questions: list[dict[str, Any]]) -> set[tuple[int, str]]:
    ids = set()
    for idx, q in enumerate(questions, start=1):
        number, part = parse_question_number_and_part(q.get("question_number"), idx)
        ids.add((number, str(q.get("sub_part") or part).strip().upper()))
    return ids


# =========================================================
# SCANS
# =========================================================

def sharpness(image: Image.Image) -> float:
    """Variance of the Laplacian at a fixed scale. Low values mean a blurred page."""
    gray = image.convert("L")
    gray.thumbnail((_SHARPNESS_MAX_SIDE, _SHARPNESS_MAX_SIDE))
    edges = gray.filter(_LAPLACIAN)
    # The kernel leaves border pixels unfiltered, which would inflate the variance.
    width, height = edges.size
    return float(ImageStat.Stat(edges.crop((2, 2, width - 2, height - 2))).var[0])


def check_image_quality(pages: list[bytes]) -> list[str]:
    """Return one reason per unusable page. An empty list means the upload is readable."""
    if not pages:
        return ["The file contains no pages."]

    problems: list[str] = []
    measured: list[float | None] = []
    for number, page in enumerate(pages, start=1):
        try:
            with Image.open(io.BytesIO(page)) as image:
                image.load()
                measured.append(sharpness(image))
        except Exception:
            measured.append(None)
            problems.append(f"Page {number}: the image could not be opened.")
    if problems:
        return problems

    results = extract_page_regions(pages)
    if any(result.status == "UNAVAILABLE" for result in results):
        raise QualityCheckUnavailable("Local OCR is unavailable, so the upload could not be checked.")

    non_blank = [result for result in results if not result.blank_candidate]
    if not non_blank:
        return ["Every page looks blank."]

    for result in non_blank:
        chars = sum(len(region.extracted_text) for region in result.regions)
        page_sharpness = measured[result.page_number - 1]
        if chars < MIN_PAGE_CHARS:
            problems.append(f"Page {result.page_number}: almost no text could be read.")
        elif page_sharpness is not None and page_sharpness < MIN_SHARPNESS:
            problems.append(f"Page {result.page_number}: the image is too blurry.")
    return problems
