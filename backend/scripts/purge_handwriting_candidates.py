"""Delete expired handwriting training-candidate metadata from Firestore.

Cloud Firestore TTL on ``handwriting_training_candidates.expires_at`` is the
primary retention control. This bounded command is an operator-run backstop.
It does not delete the original answer-sheet file; that follows the exam's
existing retention policy.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(dotenv_path=BACKEND_DIR / ".env")

from config import get_firestore  # noqa: E402
from services.handwriting_dataset import purge_expired_candidates  # noqa: E402


def main() -> int:
    db = get_firestore()
    if db is None:
        print("Firestore unavailable; no candidate data was changed.")
        return 1
    try:
        deleted = purge_expired_candidates(db)
    except Exception as error:
        print(f"Candidate cleanup failed ({type(error).__name__}); no sensitive details logged.")
        return 1
    print(f"Deleted {deleted} expired handwriting candidate record(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

