"""Send each sheet to Gemini once, through AutoGrade's own sheet reader, and save the raw reply.

Usage:
  python run_readings.py mendeley default [--ids Student_1,Student_2] [--limit N]
  python run_readings.py mendeley calibrated
  python run_readings.py checkmat default
  python run_readings.py asap default

Conditions:
  default     the app's default marking style (Generous), no teacher rules, no calibration
  strict      Strict marking style
  calibrated  (Mendeley) calibrated from one tuning sheet, using the teacher's real marks

Replies are saved to evaluation/runs/<dataset>/<condition>/<id>.json. Sheets already saved are skipped,
so the run can be stopped and resumed. All scoring happens later, offline, in analyze.py.
"""

from __future__ import annotations

import argparse
import sys
import time

from common import RUNS, load_json, save_json, setup_backend

setup_backend()

import datasets as ds  # noqa: E402
from services import calibration as calib  # noqa: E402
from services.gemini_client import DailyLimitReached, GeminiError, get_client  # noqa: E402
from services.gemini_sheet import _read_parts, grade_readings, SheetExtraction  # noqa: E402
from models.schemas import QuestionRubric  # noqa: E402

CALIBRATION_SHEET = "Student_21"  # a tuning sheet, never in the test set


def items_for(dataset: str, ids: list[str] | None):
    if dataset == "mendeley":
        return ds.mendeley_items(ids or ds.MENDELEY_ALL)
    if dataset == "checkmat":
        return ds.checkmat_items(ids)
    if dataset == "asap":
        return ds.asap_items(ids)
    raise SystemExit(f"Unknown dataset {dataset}")


def load_readings(path):
    data = load_json(path)
    return [(SheetExtraction.model_validate(r["extraction"]), r["offset"]) for r in data["readings"]], data


def mendeley_calibration() -> dict:
    """Build a calibration record from the tuning sheet: the app's own reading, the teacher's marks."""
    path = RUNS / "mendeley" / "default" / f"{CALIBRATION_SHEET}.json"
    if not path.exists():
        raise SystemExit(f"Run the default condition on {CALIBRATION_SHEET} first.")
    rubric_dicts = ds.mendeley_rubric()
    rubric = [QuestionRubric(**q) for q in rubric_dicts]
    readings, data = load_readings(path)
    grades, _ = grade_readings(rubric, readings, data["problems"], data["model"], "GENEROUS")
    teacher = ds.mendeley_ground_truth()[CALIBRATION_SHEET]["short"]
    questions = []
    for g in grades:
        q = g.model_dump()
        mark = teacher.get(g.question_number)
        if g.question_number in teacher and mark is not None:
            q["grader_score"] = q.get("suggested_score") if q.get("suggested_score") is not None else q.get("final_score")
            q["final_score"] = mark
        questions.append(q)
    result = {"exam_id": "mendeley", "hall_ticket": CALIBRATION_SHEET, "rubric_version": "eval", "questions": questions}
    return calib.build(result, rubric_dicts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset")
    ap.add_argument("condition", choices=["default", "strict", "calibrated"])
    ap.add_argument("--ids", default="")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    ids = [s for s in args.ids.split(",") if s] or None

    style = "STRICT" if args.condition == "strict" else "GENEROUS"
    examples = {}
    if args.condition == "calibrated":
        examples = calib.examples_for_prompt(mendeley_calibration())
        if ids is None:
            ids = ds.MENDELEY_TEST

    items = items_for(args.dataset, ids)
    out_dir = RUNS / args.dataset / args.condition
    client = get_client()
    done = failed = 0
    for item in items:
        path = out_dir / f"{item['id']}.json"
        if path.exists():
            continue
        if args.limit and done >= args.limit:
            break
        rubric = [QuestionRubric(**q) for q in item["rubric"]]
        run = client.new_run()
        started = time.time()
        try:
            readings, problems, model = _read_parts(
                item["file_bytes"], item["extension"], rubric, item["id"], client, run,
                style=style, notes="", examples=examples,
            )
        except DailyLimitReached:
            print("Daily Gemini limit reached; stopping. Run again tomorrow to continue.", flush=True)
            break
        except GeminiError as error:
            failed += 1
            save_json(out_dir / "failed" / f"{item['id']}.json", {"id": item["id"], "error": str(error)})
            print(f"{item['id']}: failed ({error})", flush=True)
            continue
        usage = run.per_sheet.get(item["id"])
        save_json(path, {
            "id": item["id"], "dataset": args.dataset, "condition": args.condition, "style": style,
            "calibrated": bool(examples), "model": model, "problems": problems,
            "seconds": round(time.time() - started, 1),
            "usage": usage.as_dict(0) if usage else {},
            "readings": [{"extraction": ex.model_dump(mode="json"), "offset": off} for ex, off in readings],
        })
        done += 1
        print(f"{item['id']}: saved ({done} done, {round(time.time() - started, 1)} s)", flush=True)
    print(f"finished: {done} saved, {failed} failed", flush=True)


if __name__ == "__main__":
    sys.exit(main())
