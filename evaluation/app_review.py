"""Agreement between AutoGrade's marks and the teacher's final marks, for sheets marked in the app.

The dataset evaluation (analyze.py) compares AutoGrade with expert marks that come with public
datasets. This script covers the real-school case study: sheets uploaded to the app and reviewed
by a teacher. For every answer it compares

  grader_score  the mark AutoGrade gave (stored at grading time; review never changes it)
  final_score   the mark on the sheet after the teacher reviewed and approved it

It only reads. Results come from Firestore (exams/{exam}/results) or from a JSON file holding a
list of result documents, so the numbers can be reproduced without the database.

  python evaluation/app_review.py                       # every exam, approved sheets only
  python evaluation/app_review.py --exam varshith       # one exam
  python evaluation/app_review.py --json results.json   # offline, from exported result documents
  python evaluation/app_review.py --include-unapproved  # also sheets the teacher has not approved
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import REPORTS, agreement, fmt, pearson, save_json  # noqa: E402

# A teacher's change smaller than this is rounding, not a disagreement.
TOLERANCE = 1e-6


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _skip(question: dict) -> str | None:
    """Why an answer says nothing about marking quality, or None if it counts."""
    if question.get("blank") or question.get("routing") == "NOT_ATTEMPTED":
        return "blank"
    if question.get("extra"):
        return "extra"  # beyond the 'answer any N' choice, so it never counts
    if _number(question.get("grader_score")) is None or _number(question.get("final_score")) is None:
        return "no_mark"
    return None


def answer_pairs(results: list[dict], include_unapproved: bool = False) -> tuple[list[dict], dict]:
    """One row per answer that a teacher has signed off, plus counts of what was left out."""
    rows: list[dict] = []
    left_out = {"unapproved_sheets": 0, "blank": 0, "extra": 0, "no_mark": 0}
    for result in results:
        if not result.get("teacher_approved") and not include_unapproved:
            left_out["unapproved_sheets"] += 1
            continue
        for question in result.get("questions") or []:
            reason = _skip(question)
            if reason:
                left_out[reason] += 1
                continue
            ai = _number(question.get("grader_score"))
            teacher = _number(question.get("final_score"))
            rows.append({
                "exam_id": result.get("exam_id", ""),
                "hall_ticket": result.get("hall_ticket", ""),
                "question": f"{question.get('question_number', '')}{question.get('sub_part', '')}",
                "max_marks": _number(question.get("max_marks")) or 0.0,
                "ai": ai,
                "teacher": teacher,
                "changed": abs(ai - teacher) > TOLERANCE,
                "flagged": bool(question.get("flag_reason")),
                "flag_reason": question.get("flag_reason") or "",
                "edit_reason": question.get("edit_reason") or "",
            })
    return rows, left_out


def summarize(rows: list[dict], step: float = 0.5) -> dict:
    """Agreement, how often the teacher changed a mark, and whether the flags caught those changes."""
    if not rows:
        return {"answers": 0}
    ai = [r["ai"] for r in rows]
    teacher = [r["teacher"] for r in rows]
    changed = [r for r in rows if r["changed"]]
    flagged = [r for r in rows if r["flagged"]]
    caught = [r for r in changed if r["flagged"]]

    sheets: dict[tuple[str, str], list[float]] = {}
    for r in rows:
        totals = sheets.setdefault((r["exam_id"], r["hall_ticket"]), [0.0, 0.0])
        totals[0] += r["ai"]
        totals[1] += r["teacher"]
    sheet_ai = [t[0] for t in sheets.values()]
    sheet_teacher = [t[1] for t in sheets.values()]

    max_total = sum(r["max_marks"] for r in rows)
    return {
        "answers": len(rows),
        "sheets": len(sheets),
        "agreement": agreement(ai, teacher, step),
        "changed_rate": len(changed) / len(rows),
        "ai_minus_teacher_marks": sum(a - t for a, t in zip(ai, teacher)),
        "max_marks": max_total,
        "flagged_rate": len(flagged) / len(rows),
        # Of the answers the teacher changed, the share AutoGrade had flagged for review.
        "flag_recall": len(caught) / len(changed) if changed else None,
        # Of the answers AutoGrade flagged, the share the teacher then changed.
        "flag_precision": len(caught) / len(flagged) if flagged else None,
        "sheet_total_mae": sum(abs(a - t) for a, t in zip(sheet_ai, sheet_teacher)) / len(sheets),
        "sheet_total_pearson": pearson(sheet_ai, sheet_teacher),
    }


def report(summary: dict, rows: list[dict], left_out: dict) -> str:
    lines = ["# AutoGrade vs teacher, sheets marked in the app", ""]
    if not summary.get("answers"):
        lines.append("No teacher-approved answers found. Approve sheets on the Review page first, "
                     "or pass --include-unapproved.")
        return "\n".join(lines) + "\n"
    a = summary["agreement"]
    lines += [
        f"Sheets: {summary['sheets']}. Answers compared: {summary['answers']}.",
        "",
        "| n | Exact | Within 0.5 | MAE | QWK |",
        "|---|---|---|---|---|",
        f"| {a['n']} | {fmt(a['exact'], True)} | {fmt(a['within_step'], True)} | {fmt(a['mae'])} | {fmt(a['qwk'])} |",
        "",
        f"Teacher changed {fmt(summary['changed_rate'], True)} of AutoGrade's marks. "
        f"AutoGrade gave {summary['ai_minus_teacher_marks']:+.1f} marks in total against the teacher "
        f"(out of {summary['max_marks']:g} available).",
        f"Flags: {fmt(summary['flagged_rate'], True)} of answers flagged; "
        f"{fmt(summary['flag_recall'], True)} of the teacher's changes were on flagged answers; "
        f"{fmt(summary['flag_precision'], True)} of flagged answers were changed.",
        f"Sheet totals: MAE {fmt(summary['sheet_total_mae'])}, Pearson {fmt(summary['sheet_total_pearson'])}.",
        "",
        "Left out: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in left_out.items()) + ".",
    ]
    changed = [r for r in rows if r["changed"]]
    if changed:
        lines += ["", "## Marks the teacher changed", "",
                  "| Exam | Sheet | Q | Max | AutoGrade | Teacher | Flag | Teacher's reason |",
                  "|---|---|---|---|---|---|---|---|"]
        for r in changed:
            reason = r["edit_reason"].replace("|", "/").replace("\n", " ")
            lines.append(f"| {r['exam_id']} | {r['hall_ticket']} | {r['question']} | {r['max_marks']:g} | "
                         f"{r['ai']:g} | {r['teacher']:g} | {r['flag_reason'] or '—'} | {reason or '—'} |")
    return "\n".join(lines) + "\n"


def load_from_firestore(exam: str | None) -> list[dict]:
    """Read result documents. Uses the same database the app does (emulator if FIRESTORE_EMULATOR_HOST is set)."""
    from common import setup_backend

    setup_backend()
    from config import get_firestore

    db = get_firestore()
    if db is None:
        raise SystemExit("Firestore is unavailable. Start the app's database or use --json.")
    exam_ids = [exam] if exam else [doc.id for doc in db.collection("exams").stream()]
    results = []
    for exam_id in exam_ids:
        for doc in db.collection("exams").document(exam_id).collection("results").stream():
            data = doc.to_dict() or {}
            data.setdefault("exam_id", exam_id)
            data.setdefault("hall_ticket", doc.id)
            results.append(data)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--exam", help="Only this exam id.")
    parser.add_argument("--json", type=Path, help="Read result documents from this JSON file instead of Firestore.")
    parser.add_argument("--include-unapproved", action="store_true",
                        help="Also use sheets the teacher has not approved (their marks may not be final).")
    parser.add_argument("--step", type=float, default=0.5, help="Smallest mark unit, for QWK and 'within'. Default 0.5.")
    parser.add_argument("--out", type=Path, default=REPORTS / "app_review",
                        help="Write <out>.md and <out>.json. Default evaluation/reports/app_review.")
    args = parser.parse_args(argv)

    if args.json:
        results = json.loads(args.json.read_text())
        if args.exam:
            results = [r for r in results if r.get("exam_id") == args.exam]
    else:
        results = load_from_firestore(args.exam)

    rows, left_out = answer_pairs(results, args.include_unapproved)
    summary = summarize(rows, args.step)
    text = report(summary, rows, left_out)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.with_suffix(".md").write_text(text)
    save_json(args.out.with_suffix(".json"), {"summary": summary, "left_out": left_out, "answers": rows})
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
