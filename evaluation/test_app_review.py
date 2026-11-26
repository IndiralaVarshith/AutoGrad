"""Tests for app_review.py. Pure data: no database, no Gemini."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import app_review  # noqa: E402


def q(n, ai, teacher, max_marks=2.0, **extra):
    return {"question_number": n, "sub_part": "", "max_marks": max_marks,
            "grader_score": ai, "final_score": teacher, **extra}


SHEETS = [
    {"exam_id": "e1", "hall_ticket": "s1", "teacher_approved": True, "questions": [
        q(1, 2.0, 2.0),
        q(2, 1.5, 1.0, flag_reason="LOW_CONFIDENCE", edit_reason="missed a step"),
        q(3, 1.0, 1.0, flag_reason="LOW_CONFIDENCE"),
        q(4, 0.0, 0.0, blank=True),
        q(5, 2.0, 0.0, extra=True),
    ]},
    {"exam_id": "e1", "hall_ticket": "s2", "teacher_approved": True, "questions": [
        q(1, 1.0, 2.0, edit_reason="full marks"),
        q(2, 2.0, 2.0),
    ]},
    {"exam_id": "e1", "hall_ticket": "s3", "teacher_approved": False, "questions": [q(1, 2.0, 0.0)]},
]


def test_pairs_use_only_approved_answered_questions():
    rows, left_out = app_review.answer_pairs(SHEETS)
    assert [(r["hall_ticket"], r["question"]) for r in rows] == [
        ("s1", "1"), ("s1", "2"), ("s1", "3"), ("s2", "1"), ("s2", "2")]
    assert left_out == {"unapproved_sheets": 1, "blank": 1, "extra": 1, "no_mark": 0}


def test_unapproved_sheets_can_be_included():
    rows, left_out = app_review.answer_pairs(SHEETS, include_unapproved=True)
    assert len(rows) == 6 and left_out["unapproved_sheets"] == 0


def test_missing_or_bad_marks_are_left_out():
    rows, left_out = app_review.answer_pairs([{"teacher_approved": True, "questions": [
        q(1, None, 1.0), q(2, "nan", 1.0), q(3, 1.0, 1.0)]}])
    assert len(rows) == 1 and left_out["no_mark"] == 2


def test_summary_numbers():
    rows, _ = app_review.answer_pairs(SHEETS)
    s = app_review.summarize(rows)
    assert s["answers"] == 5 and s["sheets"] == 2
    assert s["agreement"]["exact"] == pytest.approx(3 / 5)
    assert s["agreement"]["mae"] == pytest.approx((0.5 + 1.0) / 5)
    assert s["changed_rate"] == pytest.approx(2 / 5)
    assert s["ai_minus_teacher_marks"] == pytest.approx(0.5 - 1.0)
    # Two flagged answers, one of them changed; two changes, one of them flagged.
    assert s["flag_precision"] == pytest.approx(0.5)
    assert s["flag_recall"] == pytest.approx(0.5)
    # Sheet totals: s1 4.5 vs 4.0, s2 3.0 vs 4.0.
    assert s["sheet_total_mae"] == pytest.approx(0.75)


def test_no_disagreement_has_no_recall():
    rows, _ = app_review.answer_pairs([{"teacher_approved": True, "questions": [q(1, 1.0, 1.0)]}])
    s = app_review.summarize(rows)
    assert s["changed_rate"] == 0 and s["flag_recall"] is None and s["flag_precision"] is None


def test_empty_input_reports_how_to_get_data():
    rows, left_out = app_review.answer_pairs([])
    text = app_review.report(app_review.summarize(rows), rows, left_out)
    assert "No teacher-approved answers" in text


def test_cli_from_json_writes_report(tmp_path):
    source = tmp_path / "results.json"
    source.write_text(json.dumps(SHEETS))
    out = tmp_path / "report"
    assert app_review.main(["--json", str(source), "--out", str(out)]) == 0
    text = out.with_suffix(".md").read_text()
    assert "Answers compared: 5" in text
    assert "missed a step" in text and "full marks" in text
    saved = json.loads(out.with_suffix(".json").read_text())
    assert saved["summary"]["answers"] == 5 and len(saved["answers"]) == 5
