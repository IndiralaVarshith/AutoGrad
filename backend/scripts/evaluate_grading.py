"""Grade the Mendeley sheets with Gemini on the emulator and compare with the teacher's marks.

    python scripts/evaluate_grading.py --split tune --name tune1
    python scripts/evaluate_grading.py --split report --name gemini-final --confirm-final \
        --earlier-run final

The report split is the 40 sheets kept out of tuning. It refuses to run without
--confirm-final or on uncommitted code, and a second run on a different commit needs
--rerun-reason, which goes into the report. Rerunning the same name resumes: finished
sheets cost no new request. Teacher marks are only read after grading ends.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
DATA = REPO / "test_data" / "mendeley"
EVALUATION = BACKEND / "evaluation"
RUNS = EVALUATION / "runs"  # transcripts live here and are git-ignored
REPORTS = REPO / "docs" / "eval"
FINAL_LOCK = EVALUATION / "final_run_gemini.json"
EMULATOR = "127.0.0.1:8080"


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=["tune", "report"], required=True)
    parser.add_argument("--name", required=True, help="Run name; rerunning it resumes finished sheets.")
    parser.add_argument("--limit", type=int, help="Only the first N sheets of a tune split.")
    parser.add_argument("--confirm-final", action="store_true")
    parser.add_argument("--rerun-reason", default="")
    parser.add_argument("--earlier-run", help="Saved run to report beside this one, such as 'final'.")
    return parser.parse_args(argv)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True).stdout.strip()


def guard_report_split(args: argparse.Namespace) -> dict:
    """Stop the report sheets being run casually. Returns the lock record."""
    if args.limit:
        sys.exit("Refusing: --limit is not allowed on the report split.")
    if not args.confirm_final:
        sys.exit("Refusing: the report split is kept out of tuning. Pass --confirm-final.")
    dirty = [
        line for line in git("status", "--porcelain").splitlines()
        if not line.startswith("??") and "evaluation/final_run" not in line
    ]
    if dirty:
        sys.exit(f"Refusing: commit the code first, so the run is traceable: {dirty[:5]}")
    commit = git("rev-parse", "HEAD")
    record = {"commit": commit, "name": args.name, "reasons": []}
    if FINAL_LOCK.exists():
        previous = json.loads(FINAL_LOCK.read_text())
        if previous["commit"] != commit or previous["name"] != args.name:
            if not args.rerun_reason:
                sys.exit(
                    f"Refusing: the final run already happened at {previous['commit'][:8]}. "
                    "A new run needs --rerun-reason, which the report will show."
                )
            record["reasons"] = previous.get("reasons", []) + [args.rerun_reason]
        else:
            record["reasons"] = previous.get("reasons", [])
    FINAL_LOCK.write_text(json.dumps(record, indent=2) + "\n")
    return record


def set_environment() -> None:
    """Must run before config is imported, so real Firestore can never be reached."""
    host = os.environ.get("FIRESTORE_EMULATOR_HOST", "").strip() or EMULATOR
    name, _, port = host.partition(":")
    if name not in {"127.0.0.1", "localhost"}:
        sys.exit(f"Refusing: '{host}' is not a local Firestore emulator.")
    try:
        socket.create_connection((name, int(port)), timeout=2).close()
    except OSError:
        sys.exit(f"Refusing: no Firestore emulator at {host}. Real Firestore is never used.")
    os.environ["FIRESTORE_EMULATOR_HOST"] = host


def approved_rubric(exam_id: str, client, run, model: str) -> tuple[list, float, dict[int, str]]:
    """Read the paper and key once, then auto-approve. The report says this was not a teacher."""
    from models.schemas import QuestionRubric
    from services.criteria_draft import approval_problems, parse_answer_key
    from services.db import db_service
    from services.gemini_paper import read_question_paper, rubric_total_problems

    key, key_problems = parse_answer_key((DATA / "answerkey.txt").read_text())
    cache = RUNS / f"paper-{model}.json"
    if cache.exists():
        paper = json.loads(cache.read_text())
    else:
        paper = read_question_paper(
            (DATA / "Question.txt").read_bytes(), ".txt",
            answer_key=(DATA / "answerkey.txt").read_bytes(), client=client, run=run,
        )
        if paper.get("unreadable"):
            sys.exit(f"Refusing: Gemini could not read the paper: {paper.get('unclear_items')}")
        RUNS.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(paper, indent=1))

    # The script stands in for the teacher here: the key file decides, never Gemini.
    problems = list(key_problems) + list(paper["problems"])
    for question in paper["questions"]:
        number = int(question["question_number"])
        if question["question_format"] == "MCQ":
            if question.get("suggested_option") != key.get(number, "").upper():
                problems.append(f"Q{number}: Gemini read the key as {question.get('suggested_option')}.")
            question["correct_option"] = key.get(number, "").upper()
    rubric = [QuestionRubric(**question) for question in paper["questions"]]
    total, total_problems, _ = rubric_total_problems(paper["questions"], paper["declared_maximum_marks"])
    problems += total_problems + approval_problems(rubric)
    if problems:
        sys.exit(f"Refusing: the paper needs a teacher's eye first: {problems[:8]}")
    db_service.save_approved_rubric(
        exam_id=exam_id, questions=rubric, teacher_confirmed=True, checked_total=total
    )
    return rubric, total, key


def grade_one(student: str, exam_id: str, rubric: list, total: float, client, run) -> dict:
    from services.db import db_service
    from services.gemini_sheet import grade_sheet

    # Only the raw sheets. Corrected_Pdf holds the teacher's marks.
    file_bytes = (DATA / "Student_Pdf" / f"{student}.pdf").read_bytes()
    started = time.perf_counter()
    result = grade_sheet(
        exam_id=exam_id, hall_ticket=student, student_name="", file_bytes=file_bytes,
        extension=".pdf", rubric_list=rubric, total_marks=total, client=client, run=run,
    )
    db_service.save_grading_result(exam_id, student, result)
    return {**result.model_dump(exclude={"ocr_pages"}), "seconds": round(time.perf_counter() - started, 1)}


def cost_summary(results: dict[str, dict]) -> dict:
    usages = [paper.get("gemini_usage") or {} for paper in results.values()]
    costs = [usage.get("notional_cost_inr") for usage in usages]
    known = [cost for cost in costs if cost is not None]
    count = max(1, len(results))
    return {
        "calls_per_sheet": round(sum(usage.get("calls", 0) for usage in usages) / count, 2),
        "input_tokens_per_sheet": round(sum(usage.get("input_tokens", 0) for usage in usages) / count),
        "output_tokens_per_sheet": round(sum(usage.get("output_tokens", 0) for usage in usages) / count),
        "notional_inr_per_sheet": round(sum(known) / len(known), 3) if known else None,
        "seconds_per_sheet": round(sum(paper.get("seconds", 0) for paper in results.values()) / count, 1),
    }


def main(argv=None) -> None:
    args = parse_args(argv)
    lock = guard_report_split(args) if args.split == "report" else None
    set_environment()
    sys.path.insert(0, str(BACKEND))

    import config
    from services.evaluation import load_split, load_teacher_marks, render_report, score_run
    from services.gemini_client import DailyLimitReached, GeminiCallFailed, GeminiError, RunCapReached, get_client

    students = load_split(EVALUATION / "split.json")[args.split]
    if args.limit:
        students = students[: args.limit]
    run_dir = RUNS / args.name
    run_dir.mkdir(parents=True, exist_ok=True)
    exam_id = f"eval-{args.name}"
    client = get_client()
    run = client.new_run(args.name)
    rubric, total, key = approved_rubric(exam_id, client, run, config.GEMINI_MODEL)

    failures: dict[str, str] = {}
    results: dict[str, dict] = {}
    stopped = ""
    started = time.perf_counter()
    for index, student in enumerate(students, start=1):
        path = run_dir / f"{student}.json"
        if path.exists():
            results[student] = json.loads(path.read_text())
            continue
        try:
            data = grade_one(student, exam_id, rubric, total, client, run)
        except (DailyLimitReached, RunCapReached) as error:
            stopped = str(error)
            print(f"[Eval] Stopped at {student}: {stopped} Rerun this name to resume.", flush=True)
            break
        except (GeminiCallFailed, GeminiError) as error:
            failures[student] = str(error)
            print(f"[Eval] {student} FAILED: {error}", flush=True)
            continue
        path.write_text(json.dumps(data, default=str))
        results[student] = data
        print(f"[Eval] {index}/{len(students)} {student} {data['seconds']}s", flush=True)

    # Teacher marks are read from here on, after every sheet is done.
    teacher = load_teacher_marks(DATA / "Teacher_manual_marks_Anonymized.csv")
    costs = cost_summary(results)
    meta = {
        "Split": f"{args.split} ({len(results)} of {len(students)} sheets graded)",
        "Model": config.GEMINI_MODEL,
        "Commit": git("rev-parse", "--short", "HEAD"),
        "Criteria": "drafted by Gemini from answerkey.txt and auto-approved by the script, with no teacher review",
        "Answer key": "answerkey.txt as written (Q1 = B)",
        "Failed sheets": failures or "none",
        "Stopped early": stopped or "no",
        "Run time": f"{round((time.perf_counter() - started) / 60, 1)} minutes in this session",
        "Calls per sheet": costs["calls_per_sheet"],
        "Tokens per sheet": f"{costs['input_tokens_per_sheet']} in, {costs['output_tokens_per_sheet']} out",
        "Seconds per sheet": costs["seconds_per_sheet"],
        "Cost per sheet": (
            f"Rs 0 actual on the free tier. Notional paid-tier cost Rs {costs['notional_inr_per_sheet']}, "
            f"at {config.GEMINI_PRICES_USD_PER_MILLION.get(config.GEMINI_MODEL)} USD per 1M tokens "
            f"({config.GEMINI_PRICES_AS_OF}) and Rs {config.GEMINI_USD_INR} per USD ({config.GEMINI_USD_INR_SOURCE})"
        ),
    }
    if lock and lock["reasons"]:
        meta["Earlier final runs, reasons for rerun"] = lock["reasons"]

    mcq_key = {question: answer for question, answer in key.items() if question <= 20}
    current = score_run(results, teacher, answer_key=mcq_key)
    earlier = None
    if args.earlier_run:
        saved = {}
        for student in results:
            path = RUNS / args.earlier_run / f"{student}.json"
            if path.exists():
                saved[student] = json.loads(path.read_text())
        earlier = score_run(saved, teacher, answer_key=mcq_key)
        meta["Earlier run"] = f"{args.earlier_run}, {len(saved)} sheets, recalculated from saved results with no model calls"
    report = render_report(f"Grading evaluation: {args.name}", meta, current, earlier)
    (run_dir / "summary.json").write_text(json.dumps({"gemini": current, "earlier": earlier, "cost": costs}, indent=2))
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"{args.name}.md").write_text(report)
    print(report)


if __name__ == "__main__":
    main()
