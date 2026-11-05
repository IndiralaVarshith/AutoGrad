"""Draft marking criteria from a teacher's answer key and the printed marks.

The output is only a draft. The teacher edits and approves it through
POST /exam/rubric/approve, which still refuses empty criteria. No model runs here,
and anything the key does not settle is left empty rather than filled in.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any

_HEADER_NUMBER = {"question_number", "question", "q", "qno"}
_HEADER_ANSWER = {"correct_answer", "answer", "key"}
_POINT_SPLIT_RE = re.compile(r";\s+|(?<=[a-z)])\.\s+(?=[A-Z])")


class AnswerKeyError(ValueError):
    """The answer key file has no usable rows."""


def parse_answer_key(text: str) -> tuple[dict[int, str], list[str]]:
    """Read a CSV key with question number and answer columns."""
    rows = list(csv.reader(io.StringIO(text)))
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        raise AnswerKeyError("The answer key is empty.")

    header = [cell.strip().lower().replace(" ", "_") for cell in rows[0]]
    number_col = next((i for i, name in enumerate(header) if name in _HEADER_NUMBER), None)
    answer_col = next((i for i, name in enumerate(header) if name in _HEADER_ANSWER), None)
    if number_col is None or answer_col is None:
        raise AnswerKeyError(
            "The answer key needs a header with Question_Number and Correct_Answer columns."
        )

    key: dict[int, str] = {}
    problems: list[str] = []
    for line, row in enumerate(rows[1:], start=2):
        cell = row[number_col].strip() if number_col < len(row) else ""
        answer = row[answer_col].strip() if answer_col < len(row) else ""
        if not cell.isdigit():
            problems.append(f"Answer key line {line}: '{cell}' is not a question number.")
            continue
        number = int(cell)
        if number in key:
            problems.append(f"Q{number}: the answer key lists this question more than once.")
            continue
        if not answer:
            problems.append(f"Q{number}: the answer key has no answer.")
            continue
        key[number] = answer
    if not key:
        raise AnswerKeyError("The answer key has no usable answers.")
    return key, problems


def _short_answer_criteria(answer: str, marks: float) -> list[dict[str, Any]]:
    """Split the key's points evenly in half marks, or keep it as one criterion."""
    points = [point.strip(" .") for point in _POINT_SPLIT_RE.split(answer) if point.strip(" .")]
    share = marks / len(points) if points else 0.0
    if len(points) > 1 and (share * 2).is_integer():
        return [{"description": point, "max_marks": share} for point in points]
    return [{"description": answer, "max_marks": marks}]


def draft_criteria(
    questions: list[dict[str, Any]], key: dict[int, str]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return copies of the paper's questions with drafted criteria, plus problems."""
    drafted: list[dict[str, Any]] = []
    problems: list[str] = []
    seen: set[int] = set()

    for original in questions:
        question = dict(original)
        number = int(question["question_number"])
        seen.add(number)
        marks = float(question.get("max_marks") or 0.0)
        answer = key.get(number, "")
        question["criteria"] = []
        question["correct_option"] = ""

        if marks <= 0:
            problems.append(f"Q{number}: no printed marks, so no criteria were drafted.")
        elif not answer:
            problems.append(f"Q{number}: not in the answer key, so no criteria were drafted.")
        elif question.get("question_format") == "MCQ":
            labels = [str(item.get("label", "")).upper() for item in question.get("options") or []]
            option = answer.upper()
            if option in labels:
                question["correct_option"] = option
                question["criteria"] = [
                    {"description": f"Selected option {option}", "max_marks": marks}
                ]
            else:
                problems.append(
                    f"Q{number}: the key says '{answer}', which is not one of the printed "
                    f"options {labels}."
                )
        else:
            question["criteria"] = _short_answer_criteria(answer, marks)
        drafted.append(question)

    for number in sorted(set(key) - seen):
        problems.append(f"Q{number}: in the answer key but not on the question paper.")
    return drafted, problems


def approval_problems(questions: list[Any]) -> list[str]:
    """Reasons the teacher may not approve this rubric yet."""
    problems: list[str] = []
    for question in questions:
        name = f"Q{question.question_number}{question.sub_part}"
        if not question.criteria or any(
            not criterion.description.strip() for criterion in question.criteria
        ):
            problems.append(f"{name}: has no marking criteria or a blank criterion.")
        if question.question_format == "MCQ":
            labels = {str(item.get("label", "")).upper() for item in question.options}
            if question.correct_option.strip().upper() not in labels:
                problems.append(f"{name}: multiple-choice question without a valid correct option.")
    return problems
