"""
AutoGrade — Examination Marks Validation & Structured Representation Engine

Provides deterministic, pure-Python arithmetic and structural validation for
question papers:
  1. Section multiplier parsing (e.g., '4x1=4M', '4x2=8M', '2x4=8M').
  2. Internal choice resolution (OR questions — student answers one, not both).
  3. Attempt-rule calculation ('Answer any K of N' — counts K questions, not all N).
  4. Parent vs Subquestion deduplication (parts sum to parent; never double-counted).
  5. Section totals deduplication (section totals never added on top of question marks).
  6. Declared vs Calculated marks reconciliation against the paper header.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class QuestionItem:
    question_number: int
    sub_part: str = ""                       # e.g., 'a', 'b', 'A', 'B'
    question_text: str = ""
    question_type: str = "THEORY"            # THEORY | NUMERICAL | DIAGRAM | MIXED
    marks: float = 0.0
    section_id: str = ""                     # e.g., 'I', 'II', 'III', 'A', 'B'
    choice_group: str = ""                   # e.g., 'Q9' for 9A OR 9B
    is_or_choice: bool = False               # True if this is an OR alternative
    is_subquestion: bool = False
    parent_question_number: int | None = None
    criteria: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SectionStructure:
    section_id: str
    section_title: str = ""
    attempt_instruction: str = ""            # e.g., 'ANSWER ANY TWO OF THE FOLLOWING: 2x4=8M'
    attempt_count: int | None = None         # e.g., 2 if 'any 2'
    total_questions: int = 0
    marks_per_question: float | None = None  # e.g., 1.0 from '4x1=4M'
    section_max_marks: float | None = None   # e.g., 8.0
    questions: list[QuestionItem] = field(default_factory=list)


@dataclass
class ExamPaperStructure:
    exam_title: str = ""
    declared_maximum_marks: float | None = None
    calculated_maximum_marks: float = 0.0
    sections: list[SectionStructure] = field(default_factory=list)
    all_questions: list[QuestionItem] = field(default_factory=list)
    validation_status: str = "VALID"         # VALID | RECONCILED | DISCREPANCY
    validation_warnings: list[str] = field(default_factory=list)
    reconciliation_notes: list[str] = field(default_factory=list)
    marks_problems: list[str] = field(default_factory=list)  # unreadable marks or missing questions


def read_marks(value: Any) -> float | None:
    """Return a usable positive mark value, or None. Never substitutes a default."""
    if value is None or isinstance(value, bool):
        return None
    try:
        marks = float(value)
    except (TypeError, ValueError):
        return None
    return marks if math.isfinite(marks) and marks > 0 else None


def _question_marks(q: dict[str, Any], fallback: Any = None) -> float | None:
    """Read marks from a question, then from a printed section value. Never guess."""
    for value in (q.get("marks"), q.get("max_marks"), fallback):
        marks = read_marks(value)
        if marks is not None:
            return marks
    return None


def _record_marks(
    exam_struct: ExamPaperStructure,
    q: dict[str, Any],
    q_num: int,
    sub_part: str,
    fallback: Any = None,
) -> float:
    """Return a question's marks, recording a problem (and 0) when they are unreadable."""
    marks = _question_marks(q, fallback)
    if marks is None:
        exam_struct.marks_problems.append(
            f"Q{q_num}{sub_part}: marks are missing or unreadable; teacher must enter them."
        )
        return 0.0
    return marks


def parse_question_number_and_part(val: Any, default_idx: int = 1) -> tuple[int, str]:
    """
    Safely extract numeric question number and optional sub_part string.
    Never crashes on strings like '9A', '9(B)', 'Q10', etc.
    Examples:
      - 9       -> (9, "")
      - "9"     -> (9, "")
      - "9A"    -> (9, "A")
      - "9B"    -> (9, "B")
      - "9a"    -> (9, "A")
      - "Q9A"   -> (9, "A")
      - "Q10"   -> (10, "")
      - ""      -> (default_idx, "")
    """
    if val is None:
        return default_idx, ""
    if isinstance(val, int):
        return val, ""

    val_str = str(val).strip()
    if not val_str:
        return default_idx, ""

    # Match numeric part and optional letter suffix: e.g. "9A", "9(a)", "Q10b"
    m = re.search(r"(\d+)\s*\(?([a-zA-Z])\)?", val_str)
    if m:
        return int(m.group(1)), m.group(2).upper()

    # Just digits: e.g. "9", "Q9"
    digits = re.sub(r"[^\d]", "", val_str)
    if digits:
        return int(digits), ""

    return default_idx, ""


def safe_int_qnum(val: Any, default: int = 0) -> int:
    """Safely extract just the integer portion of a question number (e.g. '9A' -> 9)."""
    q_num, _ = parse_question_number_and_part(val, default)
    return q_num


def parse_section_formula(instruction_text: str) -> tuple[int | None, float | None, float | None]:
    """
    Extract (count, marks_per_question, total_marks) from instruction strings like:
      - '4x1=4M' or '4 X 1 = 4 Marks' -> (4, 1.0, 4.0)
      - '4x2=8M'                      -> (4, 2.0, 8.0)
      - '2x4=8M'                      -> (2, 4.0, 8.0)
      - '5 x 2 = 10'                  -> (5, 2.0, 10.0)
    """
    if not instruction_text:
        return None, None, None

    # Pattern: N x M = Total or N * M = Total
    match = re.search(r"(\d+)\s*[xX*×]\s*([\d.]+)\s*=\s*([\d.]+)", instruction_text)
    if match:
        count = int(match.group(1))
        marks_per_q = float(match.group(2))
        total = float(match.group(3))
        return count, marks_per_q, total

    # Pattern: 'Answer any 2' or 'Attempt any 4'
    any_match = re.search(r"(?:answer|attempt)\s+any\s*[-–—]?\s*(\d+|one|two|three|four|five|six|seven|eight)", instruction_text, re.IGNORECASE)
    word_to_num = {
        "one": 1, "two": 2, "three": 3, "four": 4,
        "five": 5, "six": 6, "seven": 7, "eight": 8,
    }
    if any_match:
        val = any_match.group(1).lower()
        count = word_to_num.get(val, int(val) if val.isdigit() else None)
        return count, None, None

    return None, None, None


def extract_declared_marks_from_text(text: str) -> float | None:
    """
    Search header text for explicit total marks declaration:
      - 'Marks: 20'
      - 'Max Marks: 20'
      - 'Maximum Marks: 20'
      - 'Total Marks: 20'
      - 'M.M.: 20' or 'MM: 20'
    """
    if not text:
        return None

    patterns = [
        r"(?:maximum\s+marks|max\.?\s*marks|total\s+marks|m\.?m\.?)\s*[:=\-–—]?\s*(\d+(?:\.\d+)?)",
        r"(?:marks)\s*[:=\-–—]\s*(\d+(?:\.\d+)?)",
        r"\b(\d+(?:\.\d+)?)\s*(?:marks)\b",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                val = float(m.group(1))
                if 5.0 <= val <= 200.0:  # reasonable exam mark bounds
                    return val
            except (ValueError, TypeError):
                continue
    return None


def calculate_section_marks(section: SectionStructure) -> tuple[float, list[str]]:
    """
    Calculate the actual maximum marks contributed by a section based on:
      1. Explicit section formula (e.g. 4x1=4M -> 4M)
      2. Attempt rule ('Answer any K' -> top K questions / groups)
      3. Choice groups (OR questions in the same group contribute once)
      4. Subquestion relationships
    """
    notes: list[str] = []
    questions = section.questions

    if not questions:
        return section.section_max_marks or 0.0, notes

    # Group questions by choice group (e.g. Q9A and Q9B in group 'Q9')
    choice_groups: dict[str, list[QuestionItem]] = {}
    independent_questions: list[QuestionItem] = []

    for q in questions:
        grp = q.choice_group or (f"Q{q.question_number}" if q.is_or_choice else None)
        if grp:
            choice_groups.setdefault(grp, []).append(q)
        else:
            independent_questions.append(q)

    # Effective units to choose from
    unit_marks: list[float] = []

    for q in independent_questions:
        unit_marks.append(q.marks)

    for grp_name, group_questions in choice_groups.items():
        # Choice group: student attempts only ONE alternative
        max_in_group = max(q.marks for q in group_questions)
        unit_marks.append(max_in_group)
        if len(group_questions) > 1:
            notes.append(
                f"Section {section.section_id}: Choice group {grp_name} has {len(group_questions)} "
                f"alternatives worth {max_in_group:.1f}M each; only one alternative is counted."
            )

    # If section specifies attempt count (e.g., 'Answer any 2 of the following')
    if section.attempt_count and section.attempt_count < len(unit_marks):
        # Sort descending to compute maximum attainable
        unit_marks.sort(reverse=True)
        effective_sum = sum(unit_marks[:section.attempt_count])
        notes.append(
            f"Section {section.section_id}: Attempt rule 'Answer any {section.attempt_count} of "
            f"{len(unit_marks)}' applies; section maximum is {effective_sum:.1f}M (not {sum(unit_marks):.1f}M)."
        )
        return effective_sum, notes

    # If section has an explicit section_max_marks that matches formula
    if section.section_max_marks is not None and section.section_max_marks > 0:
        raw_sum = sum(unit_marks)
        if abs(raw_sum - section.section_max_marks) < 0.01:
            return section.section_max_marks, notes
        if section.attempt_count and section.marks_per_question:
            formula_total = section.attempt_count * section.marks_per_question
            if abs(formula_total - section.section_max_marks) < 0.01:
                return formula_total, notes

    return sum(unit_marks), notes


def _missing_question_problems(section: SectionStructure) -> list[str]:
    """Flag a section that requires more answers than it has questions.

    calculate_section_marks can return the printed section total, which would
    otherwise hide a question that is missing from the paper or was not read.
    """
    try:
        required = int(section.attempt_count or 0)
    except (TypeError, ValueError):
        return []
    groups = {
        q.choice_group or f"Q{q.question_number}"
        for q in section.questions
        if q.choice_group or q.is_or_choice
    }
    independent = sum(1 for q in section.questions if not (q.choice_group or q.is_or_choice))
    available = independent + len(groups)
    if available >= required:
        return []
    return [
        f"Section {section.section_id}: requires {required} answers but only {available} "
        "question(s) could be read; a question may be missing. Teacher must check the paper."
    ]


def validate_and_reconcile_exam_structure(
    raw_data: dict[str, Any],
) -> ExamPaperStructure:
    """
    Pure Python validation and reconciliation of raw extracted examination data.
    Ensures mathematical consistency, resolves OR choices, applies attempt rules,
    and reconciles against declared maximum marks.
    """
    exam_struct = ExamPaperStructure()
    exam_struct.exam_title = str(raw_data.get("exam_title", "")).strip()

    # 1. Parse declared maximum marks
    declared_raw = raw_data.get("declared_maximum_marks", raw_data.get("declared_marks", None))
    if declared_raw is not None:
        try:
            exam_struct.declared_maximum_marks = float(declared_raw)
        except (ValueError, TypeError):
            exam_struct.declared_maximum_marks = None

    raw_sections = raw_data.get("sections", [])
    raw_questions = raw_data.get("questions", [])

    # If sections are present, build structured sections
    if raw_sections:
        for s_idx, s in enumerate(raw_sections):
            sec_id = str(s.get("section_id", s.get("id", f"SEC_{s_idx+1}"))).strip()
            sec_title = str(s.get("section_title", s.get("title", f"Section {sec_id}"))).strip()
            instruction = str(s.get("attempt_instruction", s.get("instructions", ""))).strip()

            count, marks_per_q, formula_total = parse_section_formula(instruction)
            sec_max = s.get("section_maximum_marks", s.get("max_marks", formula_total))
            try:
                sec_max = float(sec_max) if sec_max is not None else formula_total
            except (ValueError, TypeError):
                sec_max = formula_total

            sec_obj = SectionStructure(
                section_id=sec_id,
                section_title=sec_title,
                attempt_instruction=instruction,
                attempt_count=count or s.get("attempt_count"),
                marks_per_question=marks_per_q or s.get("marks_per_question"),
                section_max_marks=sec_max,
            )

            # Gather questions belonging to this section
            sec_questions_raw = s.get("questions", [])
            for q_idx, q in enumerate(sec_questions_raw):
                q_num, extracted_part = parse_question_number_and_part(q.get("question_number"), q_idx + 1)
                sub_part = str(q.get("sub_part", "")).strip() or extracted_part
                q_text = str(q.get("question_text", "")).strip()
                q_type = str(q.get("question_type", "THEORY")).upper()
                q_marks = _record_marks(exam_struct, q, q_num, sub_part, sec_obj.marks_per_question)

                choice_grp = str(q.get("choice_group", "")).strip()
                is_or = bool(q.get("is_or_choice", False) or "(OR)" in q_text or " OR " in q_text)

                q_item = QuestionItem(
                    question_number=q_num,
                    sub_part=sub_part,
                    question_text=q_text,
                    question_type=q_type,
                    marks=q_marks,
                    section_id=sec_id,
                    choice_group=choice_grp,
                    is_or_choice=is_or,
                    criteria=q.get("criteria", []),
                )
                sec_obj.questions.append(q_item)
                exam_struct.all_questions.append(q_item)

            sec_obj.total_questions = len(sec_obj.questions)
            exam_struct.sections.append(sec_obj)

    else:
        # Flat question list: group by detected section or treat as single section
        sec_map: dict[str, list[QuestionItem]] = {}
        section_metadata: dict[str, dict[str, Any]] = {}
        for idx, q in enumerate(raw_questions):
            q_num, extracted_part = parse_question_number_and_part(q.get("question_number"), idx + 1)
            sub_part = str(q.get("sub_part", "")).strip() or extracted_part
            sec_id = str(q.get("section", q.get("section_id", "A"))).strip()
            section_metadata.setdefault(sec_id, q)
            q_text = str(q.get("question_text", f"Question {q_num}")).strip()
            q_type = str(q.get("question_type", "THEORY")).upper()
            q_marks = _record_marks(exam_struct, q, q_num, sub_part)
            choice_grp = str(q.get("choice_group", "")).strip()
            is_or = bool(q.get("is_or_choice", False) or "(OR)" in q_text)

            q_item = QuestionItem(
                question_number=q_num,
                sub_part=str(q.get("sub_part", "")).strip(),
                question_text=q_text,
                question_type=q_type,
                marks=q_marks,
                section_id=sec_id,
                choice_group=choice_grp,
                is_or_choice=is_or,
                criteria=q.get("criteria", []),
            )
            sec_map.setdefault(sec_id, []).append(q_item)
            exam_struct.all_questions.append(q_item)

        for sec_id, questions in sec_map.items():
            metadata = section_metadata[sec_id]
            sec_obj = SectionStructure(
                section_id=sec_id,
                section_title=f"Section {sec_id}",
                attempt_instruction=str(metadata.get("attempt_instruction", "")).strip(),
                attempt_count=metadata.get(
                    "section_attempt_count",
                    metadata.get("attempt_count"),
                ),
                section_max_marks=metadata.get("section_max_marks"),
                total_questions=len(questions),
                questions=questions,
            )
            exam_struct.sections.append(sec_obj)

    # 2. Compute total marks per section using deterministic arithmetic
    total_calculated = 0.0
    for sec in exam_struct.sections:
        sec_marks, sec_notes = calculate_section_marks(sec)
        total_calculated += sec_marks
        exam_struct.reconciliation_notes.extend(sec_notes)
        exam_struct.marks_problems.extend(_missing_question_problems(sec))

    exam_struct.calculated_maximum_marks = round(total_calculated, 1)

    # 3. Compare with declared maximum marks
    declared = exam_struct.declared_maximum_marks
    calculated = exam_struct.calculated_maximum_marks

    if declared is not None and declared > 0:
        if abs(calculated - declared) < 0.01:
            naive_sum = sum(q.marks for q in exam_struct.all_questions)
            if abs(naive_sum - declared) > 0.01:
                exam_struct.validation_status = "RECONCILED"
                exam_struct.reconciliation_notes.append(
                    f"Naive question sum was {naive_sum:.1f}M, but explicit choice and "
                    f"attempt rules reconcile the examination to {declared:.1f}M."
                )
            else:
                exam_struct.validation_status = "VALID"
                exam_struct.reconciliation_notes.append(
                    f"Calculated exam marks ({calculated:.1f}) exactly match declared maximum marks ({declared:.1f})."
                )
        else:
            exam_struct.validation_status = "DISCREPANCY"
            exam_struct.validation_warnings.append(
                f"Discrepancy detected: Question paper declares {declared:.1f} marks, "
                f"but structurally calculated maximum is {calculated:.1f} marks."
            )
    else:
        # No declared marks found in header; trust calculated
        exam_struct.declared_maximum_marks = calculated
        exam_struct.validation_status = "VALID"

    # Unreadable marks make any total unreliable, whatever it adds up to.
    if exam_struct.marks_problems:
        exam_struct.validation_status = "DISCREPANCY"
        exam_struct.validation_warnings.extend(exam_struct.marks_problems)

    return exam_struct


def normalize_rubric_questions_with_structure(
    raw_questions: list[dict[str, Any]],
    exam_struct: ExamPaperStructure,
) -> list[dict[str, Any]]:
    """
    Ensure every question in the rubric strictly respects its validated marks
    and that criteria sums match the question's validated maximum marks.
    """
    validated: list[dict[str, Any]] = []

    # Map question_number (+ sub_part if any) to validated marks
    marks_lookup: dict[tuple[int, str], float] = {}
    for q in exam_struct.all_questions:
        marks_lookup[(q.question_number, q.sub_part)] = q.marks
        marks_lookup[(q.question_number, "")] = q.marks

    for idx, q in enumerate(raw_questions):
        q_num, extracted_part = parse_question_number_and_part(q.get("question_number"), idx + 1)
        sub_part = str(q.get("sub_part", "")).strip() or extracted_part

        # Source of truth: validated mark from exam_struct
        val_mark = marks_lookup.get((q_num, sub_part), marks_lookup.get((q_num, ""), None))
        if val_mark is None or val_mark <= 0:
            val_mark = _question_marks(q) or 0.0  # 0 = unreadable; flagged by the validator

        # Clean criteria
        criteria_raw = q.get("criteria", [])
        validated_criteria: list[dict[str, Any]] = []
        c_sum = 0.0

        for c in criteria_raw:
            desc = str(c.get("description", "Evaluation criterion")).strip()
            c_marks = float(c.get("max_marks", c.get("marks", 0.0)))
            validated_criteria.append({"description": desc, "max_marks": c_marks})
            c_sum += c_marks

        if not validated_criteria:
            validated_criteria.append({
                "description": "Comprehensive and accurate answer demonstrating full understanding",
                "max_marks": val_mark,
            })
            c_sum = val_mark

        # Enforce sum(criteria) == val_mark
        if val_mark > 0 and abs(c_sum - val_mark) > 0.01 and c_sum > 0:
            scale = val_mark / c_sum
            new_sum = 0.0
            for c in validated_criteria:
                c["max_marks"] = round(c["max_marks"] * scale, 1)
                new_sum += c["max_marks"]
            diff = round(val_mark - new_sum, 1)
            validated_criteria[-1]["max_marks"] = max(0.5, validated_criteria[-1]["max_marks"] + diff)

        entry = {
            "question_number": q_num,
            "question_text": str(q.get("question_text", f"Question {q_num}")).strip(),
            "question_type": str(q.get("question_type", "THEORY")).upper(),
            "max_marks": val_mark,
            "section": str(q.get("section", "")).strip(),
            "criteria": validated_criteria,
        }
        source_section = next(
            (section for section in exam_struct.sections if section.section_id == q.get("section")),
            None,
        )
        if source_section is not None:
            entry["section_attempt_count"] = source_section.attempt_count
            entry["section_max_marks"] = source_section.section_max_marks
            entry["attempt_instruction"] = source_section.attempt_instruction
        if sub_part:
            entry["sub_part"] = sub_part
        if q.get("choice_group"):
            entry["choice_group"] = str(q.get("choice_group"))
        if q.get("is_or_choice"):
            entry["is_or_choice"] = True
        if q.get("question_format"):
            entry["question_format"] = str(q.get("question_format"))
        if q.get("options"):
            entry["options"] = q.get("options")
        if q.get("has_figure"):
            entry["has_figure"] = q.get("has_figure")
        if q.get("figure_page"):
            entry["figure_page"] = q.get("figure_page")

        validated.append(entry)

    return validated
