"""Grade the Mendeley sheets on the emulator and compare with the teacher's marks.

    python scripts/evaluate_grading.py --split tune --name first-try
    python scripts/evaluate_grading.py --split tune --name read-gemma --read-only --reader gemma4:e4b
    python scripts/evaluate_grading.py --split report --name final --confirm-final

The report split is the untouched 40. It refuses to run without --confirm-final or
on uncommitted code, and a second run on a different commit needs --rerun-reason,
which goes into the report. Teacher marks are only read after grading ends.
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
FINAL_LOCK = EVALUATION / "final_run.json"
EMULATOR = "127.0.0.1:8080"
RENDER_DPI = 150


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=["tune", "report"], required=True)
    parser.add_argument("--name", required=True, help="Run name; rerunning it resumes finished sheets.")
    parser.add_argument("--reader", help="Override OLLAMA_SHEET_READER_MODEL.")
    parser.add_argument("--read-only", action="store_true", help="Compare reading only; no grading.")
    parser.add_argument("--gemini", action="store_true", help="Allow Gemini on flagged answers (max 20).")
    parser.add_argument("--limit", type=int, help="Only the first N sheets of a tune split.")
    parser.add_argument("--confirm-final", action="store_true")
    parser.add_argument("--rerun-reason", default="")
    return parser.parse_args(argv)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True).stdout.strip()


def guard_report_split(args: argparse.Namespace) -> dict:
    """Stop the untouched sheets being run casually. Returns the lock record."""
    if args.limit:
        sys.exit("Refusing: --limit is not allowed on the report split.")
    if not args.confirm_final:
        sys.exit("Refusing: the report split is the untouched 40. Pass --confirm-final.")
    dirty = [
        line for line in git("status", "--porcelain").splitlines()
        if not line.startswith("??") and "evaluation/final_run.json" not in line
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


def set_environment(args: argparse.Namespace) -> None:
    """Must run before config is imported."""
    host = os.environ.get("FIRESTORE_EMULATOR_HOST", "").strip() or EMULATOR
    name, _, port = host.partition(":")
    if name not in {"127.0.0.1", "localhost"}:
        sys.exit(f"Refusing: '{host}' is not a local Firestore emulator.")
    try:
        socket.create_connection((name, int(port)), timeout=2).close()
    except OSError:
        sys.exit(f"Refusing: no Firestore emulator at {host}. Real Firestore is never used.")
    os.environ["FIRESTORE_EMULATOR_HOST"] = host
    os.environ["GEMINI_FALLBACK_ENABLED"] = "false"
    allow_gemini = "true" if args.gemini else "false"
    os.environ["GEMINI_ENABLED"] = allow_gemini
    os.environ["GEMINI_VERIFICATION_ENABLED"] = allow_gemini
    os.environ["GEMINI_VERIFICATION_SAMPLE_RATE"] = "0"
    os.environ["GEMINI_MAX_CALLS_PER_RUN"] = "20"
    os.environ["GEMINI_MAX_RETRIES"] = "1"
    os.environ["GEMINI_MAX_FALLBACK_MODELS"] = "0"
    if args.reader:
        os.environ["OLLAMA_SHEET_READER_MODEL"] = args.reader


def sheet_pages(student: str) -> list[bytes]:
    import pymupdf

    # Only the raw sheets. Corrected_Pdf holds the teacher's marks.
    path = DATA / "Student_Pdf" / f"{student}.pdf"
    with pymupdf.open(path) as document:
        return [page.get_pixmap(dpi=RENDER_DPI).tobytes("jpeg") for page in document]


def approved_rubric(exam_id: str):
    """Draft criteria from the key and auto-approve them, labelled as such in the report."""
    from models.schemas import QuestionRubric
    from services.criteria_draft import approval_problems, draft_criteria, parse_answer_key
    from services.db import db_service
    from services.paper_intake import parse_question_paper_text

    paper = parse_question_paper_text((DATA / "Question.txt").read_text())
    key, key_problems = parse_answer_key((DATA / "answerkey.txt").read_text())
    drafted, problems = draft_criteria(paper.questions, key)
    rubric = [QuestionRubric(**question) for question in drafted]
    problems = key_problems + problems + approval_problems(rubric)
    if problems:
        sys.exit(f"Refusing: the drafted criteria are incomplete: {problems[:5]}")
    db_service.save_approved_rubric(exam_id=exam_id, questions=rubric, teacher_confirmed=True)
    saved = db_service.get_approved_rubric(exam_id)
    return [QuestionRubric(**question) for question in saved["questions"]], key


def read_sheet(student: str) -> dict:
    from services.ocr import extract_text

    pages = sheet_pages(student)
    readers: list[dict] = []
    started = time.perf_counter()
    answers, _ = extract_text(pages, source_hash="", page_readers=readers)
    return {
        "answers": {str(k[0]): v["extracted_text"] for k, v in answers.items() if k[0] > 0},
        "unmapped": sum(
            1 for k, v in answers.items() if k[0] == 0 or v.get("mapping_status") != "MAPPED"
        ),
        "seconds": round(time.perf_counter() - started, 1),
        "page_readers": readers,
    }


def grade_sheet(student: str, exam_id: str, rubric: list) -> dict:
    from pipeline import process_paper

    pages = sheet_pages(student)
    started = time.perf_counter()
    result = process_paper(
        exam_id=exam_id, hall_ticket=student, student_name="",
        image_parts=pages, rubric_list=rubric, image_bytes_list=pages,
    )
    return {**result.model_dump(exclude={"ocr_pages"}), "seconds": round(time.perf_counter() - started, 1)}


def main(argv=None) -> None:
    args = parse_args(argv)
    if args.split == "report" and args.read_only:
        sys.exit("Refusing: reading comparisons belong on the tuning sheets.")
    lock = guard_report_split(args) if args.split == "report" else None
    set_environment(args)
    sys.path.insert(0, str(BACKEND))

    from config import OLLAMA_SHEET_READER_MODEL
    from services.evaluation import (
        load_split,
        load_teacher_marks,
        render_report,
        score_reading,
        score_run,
    )
    from services.router import model_router

    students = load_split(EVALUATION / "split.json")[args.split]
    if args.limit:
        students = students[: args.limit]
    run_dir = RUNS / args.name
    run_dir.mkdir(parents=True, exist_ok=True)
    exam_id = f"eval-{args.name}"
    model_router.start_gemini_run()

    rubric, key = (None, None) if args.read_only else approved_rubric(exam_id)
    failures: dict[str, str] = {}
    results: dict[str, dict] = {}
    started = time.perf_counter()
    for index, student in enumerate(students, start=1):
        path = run_dir / f"{student}.json"
        if path.exists():
            results[student] = json.loads(path.read_text())
            continue
        try:
            data = read_sheet(student) if args.read_only else grade_sheet(student, exam_id, rubric)
        except Exception as error:  # noqa: BLE001 - reported per sheet, never hidden
            failures[student] = f"{type(error).__name__}: {error}"
            print(f"[Eval] {student} FAILED: {failures[student]}", flush=True)
            continue
        path.write_text(json.dumps(data, default=str))
        results[student] = data
        print(f"[Eval] {index}/{len(students)} {student} {data['seconds']}s", flush=True)

    # Teacher marks are read from here on, after every sheet is done.
    teacher = load_teacher_marks(DATA / "Teacher_manual_marks_Anonymized.csv")
    meta = {
        "Split": f"{args.split} ({len(results)} of {len(students)} sheets graded)",
        "Sheet reader": OLLAMA_SHEET_READER_MODEL,
        "Commit": git("rev-parse", "--short", "HEAD"),
        "Criteria": "drafted from answerkey.txt and auto-approved by the script, with no teacher review",
        "Answer key": "answerkey.txt as written (Q1 = B)",
        "Failed sheets": failures or "none",
        "Run time": f"{round((time.perf_counter() - started) / 60, 1)} minutes in this session",
    }
    if lock and lock["reasons"]:
        meta["Earlier final runs, reasons for rerun"] = lock["reasons"]

    if args.read_only:
        summary = score_reading(results, teacher)
        (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary, indent=2))
        return

    mcq_key = {question: answer for question, answer in key.items() if question <= 20}
    local = score_run(results, teacher, answer_key=mcq_key, local_only=True)
    assisted = score_run(results, teacher, answer_key=mcq_key, local_only=False) if args.gemini else None
    if args.gemini:
        meta["Gemini calls used"] = 20 - model_router.gemini_calls_left()
    report = render_report(f"Grading evaluation: {args.name}", meta, local, assisted)
    (run_dir / "summary.json").write_text(json.dumps({"local": local, "assisted": assisted}, indent=2))
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"{args.name}.md").write_text(report)
    print(report)


if __name__ == "__main__":
    main()
