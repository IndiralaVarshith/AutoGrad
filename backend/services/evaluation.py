"""Compare the app's marks with the teacher's. Pure functions, no models, no database.

The teacher's marks are loaded here only, after grading has finished, and are never
passed to the pipeline.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MCQ_QUESTIONS = range(1, 21)
SHORT_QUESTIONS = range(21, 36)
SHORT_MAX = 2.0
AGREEMENT_WINDOW = 0.5
NEEDS_REVIEW = {"FLAGGED", "MANUAL"}
ALL_STUDENTS = [f"Student_{n}" for n in range(1, 51)]
# The teacher appears to have marked Q1 as D. Used only for one side-by-side figure.
TEACHER_EFFECTIVE_KEY_CHANGES = {1: "D"}


def load_split(path: Path) -> dict[str, list[str]]:
    tune = json.loads(path.read_text())["tune"]
    return {"tune": list(tune), "report": [s for s in ALL_STUDENTS if s not in set(tune)]}


@dataclass
class TeacherSheet:
    mcq: dict[int, str | None] = field(default_factory=dict)  # None: unusable cell
    short: dict[int, float | None] = field(default_factory=dict)  # None: no valid mark
    mcq_mark: float = 0.0


def load_teacher_marks(path: Path) -> dict[str, TeacherSheet]:
    sheets: dict[str, TeacherSheet] = {}
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            sheet = TeacherSheet(mcq_mark=float(row["MCQ mark"]))
            for q in MCQ_QUESTIONS:
                cell = row[str(q)].strip()
                sheet.mcq[q] = cell if cell in {"A", "B", "C", "D"} else None
            for q in SHORT_QUESTIONS:
                sheet.short[q] = _short_mark(row[str(q)])
            sheets[row["Student_ID"]] = sheet
    return sheets


def _short_mark(cell: str) -> float | None:
    """Blank, NC, NM and impossible values such as 4.0 are not usable marks."""
    try:
        mark = float(cell.strip())
    except ValueError:
        return None
    return mark if 0.0 <= mark <= SHORT_MAX else None


def view(question: dict[str, Any], *, local_only: bool) -> tuple[bool, float | None]:
    """Return (needs_review, mark), as the app stood before or after any Gemini check."""
    routing = question.get("routing", "MANUAL")
    if local_only and question.get("local_routing"):
        routing = question["local_routing"]
    if routing in NEEDS_REVIEW:
        return True, None
    mark = question.get("final_score", 0.0)
    return False, float(mark)


def _pct(part: int, whole: int) -> float | None:
    return round(100.0 * part / whole, 1) if whole else None


def _constant_mark_baseline(results: dict, teacher: dict) -> dict[str, float | None]:
    """How a marker that never reads the answer would score on the 0.5 window."""
    marks = [
        mark for student in results
        for mark in teacher[student].short.values() if mark is not None
    ]
    return {
        str(constant): _pct(sum(abs(constant - mark) <= 0.5 for mark in marks), len(marks))
        for constant in (1.5, 2.0)
    }


def score_run(
    results: dict[str, dict[str, Any]],
    teacher: dict[str, TeacherSheet],
    *,
    answer_key: dict[int, str],
    local_only: bool = True,
) -> dict[str, Any]:
    mcq = Counter()
    short = Counter()
    silent: list[dict[str, Any]] = []
    flag_reasons = Counter()
    drafts = Counter()
    effective_key = {**answer_key, **TEACHER_EFFECTIVE_KEY_CHANGES}
    mark_gap = {"as_written": Counter(), "teacher_effective": Counter()}

    for student, paper in results.items():
        truth = teacher[student]
        questions = {int(q["question_number"]): q for q in paper["questions"]}
        for q in MCQ_QUESTIONS:
            question = questions.get(q, {"routing": "MANUAL", "flag_reason": "GRADING_FAILED"})
            flagged, _ = view(question, local_only=local_only)
            if flagged:
                flag_reasons[question.get("flag_reason") or "OTHER"] += 1
            expected = truth.mcq[q]
            if expected is None:
                mcq["unusable_teacher_cell"] += 1
                continue
            mcq["total"] += 1
            if flagged:
                mcq["flagged"] += 1
            elif question.get("selected_option") == expected:
                mcq["correct"] += 1
            else:
                mcq["silent_wrong"] += 1
                silent.append({
                    "student": student, "question": q, "kind": "MCQ",
                    "app": question.get("selected_option", ""), "teacher": expected,
                })
        for label, key in (("as_written", answer_key), ("teacher_effective", effective_key)):
            app_mark, pending = 0.0, 0
            for q in MCQ_QUESTIONS:
                question = questions.get(q, {"routing": "MANUAL"})
                if view(question, local_only=local_only)[0]:
                    pending += 1
                elif question.get("selected_option") == key.get(q):
                    app_mark += 1.0
            # Only a sheet with no pending MCQ has a final total to compare.
            if pending == 0:
                mark_gap[label]["sheets"] += 1
                mark_gap[label]["exact"] += int(app_mark == truth.mcq_mark)
                mark_gap[label]["within_1"] += int(abs(app_mark - truth.mcq_mark) <= 1.0)

        for q in SHORT_QUESTIONS:
            question = questions.get(q, {"routing": "MANUAL", "flag_reason": "GRADING_FAILED"})
            flagged, mark = view(question, local_only=local_only)
            if flagged:
                flag_reasons[question.get("flag_reason") or "OTHER"] += 1
            expected = truth.short[q]
            if expected is None:
                short["no_usable_teacher_mark"] += 1
                if not flagged:
                    short["no_teacher_mark_but_app_marked"] += 1
                elif question.get("flag_reason") in {"BLANK_ANSWER", "NO_ANSWER_FOUND"}:
                    short["no_teacher_mark_and_app_found_no_answer"] += 1
                else:
                    short["no_teacher_mark_and_app_flagged_written_answer"] += 1
                continue
            short["total"] += 1
            if flagged:
                short["flagged"] += 1
                suggested = question.get("suggested_score")
                if suggested is not None:
                    drafts["with_draft"] += 1
                    drafts["draft_within_0_5"] += int(abs(float(suggested) - expected) <= AGREEMENT_WINDOW)
            elif abs(mark - expected) <= AGREEMENT_WINDOW:
                short["agree"] += 1
                short["exact"] += int(mark == expected)
            else:
                short["silent_wrong"] += 1
                silent.append({
                    "student": student, "question": q, "kind": "SHORT",
                    "app": mark, "teacher": expected,
                })

    all_answers = len(results) * (len(MCQ_QUESTIONS) + len(SHORT_QUESTIONS))
    return {
        "sheets": len(results),
        "view": "local_only" if local_only else "gemini_assisted",
        "mcq": {
            **{k: mcq[k] for k in ("total", "correct", "flagged", "silent_wrong", "unusable_teacher_cell")},
            "exact_match_pct_of_all": _pct(mcq["correct"], mcq["total"]),
            "exact_match_pct_of_unflagged": _pct(mcq["correct"], mcq["correct"] + mcq["silent_wrong"]),
            "flagged_pct": _pct(mcq["flagged"], mcq["total"]),
            "sheet_totals_vs_teacher": {k: dict(v) for k, v in mark_gap.items()},
        },
        "short": {
            **{k: short[k] for k in (
                "total", "agree", "exact", "flagged", "silent_wrong", "no_usable_teacher_mark",
                "no_teacher_mark_and_app_found_no_answer",
                "no_teacher_mark_and_app_flagged_written_answer",
                "no_teacher_mark_but_app_marked",
            )},
            "agreement_pct_of_unflagged": _pct(short["agree"], short["agree"] + short["silent_wrong"]),
            "agreement_pct_of_all": _pct(short["agree"], short["total"]),
            "flagged_pct": _pct(short["flagged"], short["total"]),
            "flagged_drafts": {
                "with_draft": drafts["with_draft"],
                "within_0_5": drafts["draft_within_0_5"],
                "within_0_5_pct": _pct(drafts["draft_within_0_5"], drafts["with_draft"]),
            },
        },
        "constant_mark_baseline": _constant_mark_baseline(results, teacher),
        "flagged_overall_pct": _pct(sum(flag_reasons.values()), all_answers),
        "flag_reasons": dict(flag_reasons.most_common()),
        "silent_wrong": silent,
    }


def score_reading(
    reads: dict[str, dict[str, Any]], teacher: dict[str, TeacherSheet]
) -> dict[str, Any]:
    """Reader-only comparison. reads[student] = {"answers": {q: text}, "unmapped": n, "seconds": s}."""
    counts = Counter()
    for student, read in reads.items():
        truth = teacher[student]
        answers = {int(q): text for q, text in read["answers"].items()}
        counts["unmapped"] += read.get("unmapped", 0)
        counts["seconds"] += read.get("seconds", 0)
        for q in MCQ_QUESTIONS:
            if truth.mcq[q] is None:
                continue
            counts["mcq_total"] += 1
            text = answers.get(q, "").strip().upper().strip("().")
            if text == truth.mcq[q]:
                counts["mcq_correct"] += 1
            elif text not in {"A", "B", "C", "D"}:
                counts["mcq_not_a_letter"] += 1
        for q in SHORT_QUESTIONS:
            found = bool(answers.get(q, "").strip())
            if truth.short[q] is None:
                counts["short_no_teacher_mark"] += 1
                counts["short_no_teacher_mark_but_text_found"] += int(found)
            else:
                counts["short_marked"] += 1
                counts["short_marked_with_text"] += int(found)
    return {
        **dict(counts),
        "mcq_exact_pct": _pct(counts["mcq_correct"], counts["mcq_total"]),
        "short_coverage_pct": _pct(counts["short_marked_with_text"], counts["short_marked"]),
        "seconds_per_sheet": round(counts["seconds"] / max(1, len(reads)), 1),
    }


def render_report(
    title: str, meta: dict[str, Any], local: dict[str, Any], assisted: dict[str, Any] | None
) -> str:
    def line(name: str, value: Any) -> str:
        return f"| {name} | {value} |\n"

    def block(result: dict[str, Any]) -> str:
        m, s = result["mcq"], result["short"]
        return (
            "| Measure | Result |\n|---|---|\n"
            + line("MCQ exact match, of all usable answers", f"{m['exact_match_pct_of_all']}% ({m['correct']}/{m['total']})")
            + line("MCQ exact match, of unflagged answers", f"{m['exact_match_pct_of_unflagged']}%")
            + line("MCQ flagged", f"{m['flagged_pct']}% ({m['flagged']})")
            + line("MCQ silent wrong", m["silent_wrong"])
            + line("Short answers within 0.5, of unflagged answers", f"{s['agreement_pct_of_unflagged']}% ({s['agree']}/{s['agree'] + s['silent_wrong']})")
            + line("Short answers within 0.5, of all with a teacher mark", f"{s['agreement_pct_of_all']}% ({s['agree']}/{s['total']})")
            + line("Baseline: same mark for every answer, never reading it", f"always 1.5: {result['constant_mark_baseline']['1.5']}%; always 2: {result['constant_mark_baseline']['2.0']}%")
            + line("Short answers flagged", f"{s['flagged_pct']}% ({s['flagged']}/{s['total']})")
            + line("Flagged drafts within 0.5 (not counted as marks)", f"{s['flagged_drafts']['within_0_5_pct']}% ({s['flagged_drafts']['within_0_5']}/{s['flagged_drafts']['with_draft']})")
            + line("Short-answer silent wrong", s["silent_wrong"])
            + line("All answers flagged", f"{result['flagged_overall_pct']}%")
        )

    text = f"# {title}\n\n"
    for key, value in meta.items():
        text += f"- **{key}:** {value}\n"
    text += "\n## Local models only (main result)\n\n" + block(local)
    if assisted:
        text += "\n## With Gemini on flagged answers\n\n" + block(assisted)
    m, s = local["mcq"], local["short"]
    gaps = m["sheet_totals_vs_teacher"]
    text += (
        "\n## Teacher data caveats\n\n"
        f"- MCQ cells without a usable letter were left out: {m['unusable_teacher_cell']}.\n"
        f"- Short answers with no valid teacher mark (blank, NC, NM, 4.0) were left out: {s['no_usable_teacher_mark']}. "
        f"Of these the app found no answer on {s['no_teacher_mark_and_app_found_no_answer']}, "
        f"flagged a written answer on {s['no_teacher_mark_and_app_flagged_written_answer']}, "
        f"and marked {s['no_teacher_mark_but_app_marked']} without flagging.\n"
        "- App MCQ totals vs the teacher's recorded totals, on sheets with no pending MCQ. "
        "The totals are unreliable (see the key mismatch), so this is context only: "
        f"key as written {gaps['as_written']}; with Q1 as D, a side note, {gaps['teacher_effective']}.\n"
    )
    text += "\n## Why answers were flagged\n\n| Reason | Count |\n|---|---|\n"
    for reason, count in local["flag_reasons"].items():
        text += line(reason, count)
    text += "\n## Silent wrong marks\n\n"
    if local["silent_wrong"]:
        text += "| Student | Question | App | Teacher |\n|---|---|---|---|\n"
        for item in local["silent_wrong"]:
            text += f"| {item['student']} | Q{item['question']} | {item['app']} | {item['teacher']} |\n"
    else:
        text += "None.\n"
    return text
