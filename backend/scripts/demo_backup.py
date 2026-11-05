"""Save a graded exam from the emulator, and put it back without any Gemini call.

    python scripts/demo_backup.py save --exam demo-5
    python scripts/demo_backup.py restore --exam demo-5

Restoring needs no network or quota: the demo can carry on from the review screen.
The emulator keeps data in memory, so a restart loses it. Works on the emulator only.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
BACKUPS = BACKEND / "evaluation" / "runs" / "demo-backup"  # git-ignored
SHEETS = BACKEND.parent / "test_data" / "mendeley" / "Student_Pdf"
EMULATOR = "127.0.0.1:8080"


def require_emulator() -> None:
    """Must run before config is imported, so real Firestore can never be reached."""
    host = os.environ.get("FIRESTORE_EMULATOR_HOST", "").strip() or EMULATOR
    name, _, port = host.partition(":")
    if name not in {"127.0.0.1", "localhost"}:
        sys.exit(f"Refusing: '{host}' is not a local Firestore emulator.")
    try:
        socket.create_connection((name, int(port)), timeout=2).close()
    except OSError:
        sys.exit(f"Refusing: no Firestore emulator at {host}. Start it first.")
    os.environ["FIRESTORE_EMULATOR_HOST"] = host


def backup_path(exam_id: str, folder: Path = BACKUPS) -> Path:
    return folder / f"{exam_id}.json"


def save(exam_id: str, folder: Path = BACKUPS) -> dict:
    from services.db import db_service

    paper = db_service.get_question_paper(exam_id)
    rubric = db_service.get_approved_rubric(exam_id)
    results = db_service.list_exam_results(exam_id)
    if not rubric or not results:
        sys.exit(f"Nothing to save: exam '{exam_id}' needs an approved rubric and graded sheets.")
    backup = {"exam_id": exam_id, "paper": paper, "rubric": rubric, "results": results}
    folder.mkdir(parents=True, exist_ok=True)
    backup_path(exam_id, folder).write_text(json.dumps(backup, indent=1, default=str))
    return backup


def restore(exam_id: str, folder: Path = BACKUPS, sheets: Path = SHEETS) -> list[str]:
    """Put the paper, rubric and results back, with each sheet's pages for the review screen."""
    from services import sheet_store
    from services.db import db_service

    path = backup_path(exam_id, folder)
    if not path.exists():
        sys.exit(f"No backup at {path}. Run 'save' first.")
    backup = json.loads(path.read_text())
    rubric = backup["rubric"]
    if backup.get("paper"):
        db_service.save_question_paper(exam_id, backup["paper"], replace=True)
    db_service.save_approved_rubric(
        exam_id=exam_id,
        questions=rubric["questions"],
        doc_fingerprint=rubric.get("doc_fingerprint", ""),
        gen_fingerprint=rubric.get("gen_fingerprint", ""),
        teacher_confirmed=True,
        checked_total=rubric["total_marks"],
    )
    restored = []
    for result in backup["results"]:
        ticket = result["hall_ticket"]
        db_service.save_grading_result(exam_id, ticket, result)
        sheet = sheets / f"{ticket}.pdf"
        if sheet.exists():
            sheet_store.save(exam_id, ticket, ".pdf", sheet.read_bytes())
        restored.append(ticket)
    return restored


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["save", "restore"])
    parser.add_argument("--exam", default="demo-5")
    args = parser.parse_args(argv)
    require_emulator()
    sys.path.insert(0, str(BACKEND))
    if args.action == "save":
        backup = save(args.exam)
        print(f"Saved {len(backup['results'])} sheets to {backup_path(args.exam)}")
    else:
        print(f"Restored: {', '.join(restore(args.exam))}")


if __name__ == "__main__":
    main()
