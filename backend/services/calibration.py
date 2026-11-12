"""One marked sheet teaches the app how this exam's teacher marks. Pure functions, no I/O.

The teacher marks one sheet: they decide its flagged answers, change marks where they
disagree and write why. They then choose to use that sheet for calibration. Every written
answer on it becomes the example for its question: what the student wrote, the mark the app
proposed, the mark the teacher settled on, and the teacher's remark.

Later sheets of the same exam carry each question's example in their one Gemini request,
and Gemini is asked to mark to that standard. The marks still come from Gemini's reading
and the code's checks; nothing is copied, and the teacher still approves every sheet.
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

# Characters of the example answer sent with each question: enough for a long answer,
# small enough that a sheet's examples stay a few thousand tokens.
ANSWER_LIMIT = 1500
REMARK_LIMIT = 300


def _part(value: Any) -> str:
    return re.sub(r"[^A-Z0-9.]", "", str(value or "").upper()).strip(".")


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build(
    result: dict[str, Any], rubric_questions: list[dict[str, Any]], *, now: float | None = None
) -> dict[str, Any]:
    """Turn one approved sheet into a calibration record.

    Only written answers the student actually wrote are used: multiple choice is marked
    by code, and blanks, extra answers and unattempted choices teach nothing.
    """
    formats = {
        (int(q["question_number"]), _part(q.get("sub_part"))): str(q.get("question_format") or "")
        for q in rubric_questions
    }
    examples: list[dict[str, Any]] = []
    for question in result.get("questions") or []:
        key = (int(question.get("question_number", 0)), _part(question.get("sub_part")))
        if key not in formats or formats[key] == "MCQ":
            continue
        if question.get("blank") or question.get("extra") or question.get("routing") == "NOT_ATTEMPTED":
            continue
        text = str(question.get("teacher_corrected_text") or question.get("extracted_text") or "").strip()
        if not text:
            continue
        teacher_marks = _number(question.get("final_score")) or 0.0
        # An answer the app could not mark has no proposal to compare with.
        app_marks = None if question.get("local_routing") == "MANUAL" else _number(question.get("grader_score"))
        remark = str(question.get("edit_reason") or "").strip()
        examples.append({
            "question_number": key[0],
            "sub_part": key[1],
            "max_marks": _number(question.get("max_marks")) or 0.0,
            "answer": text,
            "app_marks": app_marks,
            "teacher_marks": teacher_marks,
            "remark": remark,
            "changed": app_marks is None or abs(app_marks - teacher_marks) > 1e-6,
        })
    return {
        "calibration_id": uuid.uuid4().hex,
        "exam_id": str(result.get("exam_id", "")),
        "hall_ticket": str(result.get("hall_ticket", "")),
        "rubric_version": str(result.get("rubric_version", "")),
        "created_at": now if now is not None else time.time(),
        "examples": examples,
        "corrections": sum(1 for example in examples if example["changed"]),
    }


def examples_for_prompt(calibration: dict[str, Any] | None) -> dict[tuple[int, str], dict[str, Any]]:
    """Each written question's example, in the words the sheet prompt uses."""
    found: dict[tuple[int, str], dict[str, Any]] = {}
    for example in (calibration or {}).get("examples") or []:
        shown: dict[str, Any] = {
            "answer": str(example.get("answer", ""))[:ANSWER_LIMIT],
            "teacher_marks": example.get("teacher_marks"),
        }
        if example.get("app_marks") is not None:
            shown["app_marks"] = example["app_marks"]
        if example.get("remark"):
            shown["teacher_remark"] = str(example["remark"])[:REMARK_LIMIT]
        found[(int(example.get("question_number", 0)), _part(example.get("sub_part")))] = shown
    return found


def applies_to(calibration: dict[str, Any] | None, rubric_version: str) -> bool:
    """A calibration counts only for sheets marked against the same approved marks."""
    return bool(
        calibration
        and calibration.get("examples")
        and str(calibration.get("rubric_version", "")) == str(rubric_version)
    )


def summary(calibration: dict[str, Any] | None) -> dict[str, Any]:
    """What the teacher sees: which sheet, how many answers, how many they corrected."""
    if not calibration:
        return {"active": False}
    examples = calibration.get("examples") or []
    return {
        "active": True,
        "calibration_id": calibration.get("calibration_id", ""),
        "hall_ticket": calibration.get("hall_ticket", ""),
        "rubric_version": str(calibration.get("rubric_version", "")),
        "created_at": calibration.get("created_at"),
        "answers": len(examples),
        "corrections": int(calibration.get("corrections", 0)),
        "remarks": sum(1 for example in examples if example.get("remark")),
    }


def has_teacher_decisions(result: dict[str, Any]) -> bool:
    """True once the teacher has confirmed, changed or approved anything on a sheet."""
    return bool(result.get("teacher_approved")) or any(
        question.get("teacher_confirmed") or question.get("teacher_override")
        for question in result.get("questions") or []
    )
