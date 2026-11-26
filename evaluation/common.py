"""Shared helpers for the AutoGrade evaluation.

The evaluation calls AutoGrade's own marking code (backend/services/gemini_sheet.py), saves every
Gemini reply as JSON, and computes all results offline from those saved replies. Ground-truth marks
are loaded only for scoring, never passed to the marking code.
"""

from __future__ import annotations

import json
import math
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
DATASETS = ROOT / "datasets"
EVAL = ROOT / "evaluation"
RUNS = EVAL / "runs"
RUBRICS = EVAL / "rubrics"
REPORTS = EVAL / "reports"


def setup_backend() -> None:
    """Make AutoGrade's backend importable and load its settings, without touching Firestore."""
    from dotenv import load_dotenv

    load_dotenv(BACKEND / ".env", override=False)
    os.environ.setdefault("GEMINI_USAGE_DIR", str(EVAL / "usage"))
    # The evaluation never writes to a database; make sure nothing points at the real one.
    os.environ.pop("FIREBASE_SERVICE_ACCOUNT_JSON", None)
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))


def images_to_pdf(paths: list[Path]) -> bytes:
    """Join page images into one PDF, the way a teacher would scan a multi-page answer."""
    import pymupdf

    doc = pymupdf.open()
    for path in paths:
        with pymupdf.open(path) as img:
            rect = img[0].rect
            pdf_bytes = img.convert_to_pdf()
        with pymupdf.open("pdf", pdf_bytes) as page_doc:
            doc.insert_pdf(page_doc)
        del rect
    data = doc.tobytes()
    doc.close()
    return data


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    tmp.replace(path)


def load_json(path: Path):
    return json.loads(path.read_text())


# ----------------------------------------------------------------------------------------- metrics

def quadratic_weighted_kappa(a: list[int], b: list[int]) -> float | None:
    """Cohen's quadratic weighted kappa for two lists of integer ratings."""
    if len(a) != len(b) or not a:
        return None
    lo, hi = min(a + b), max(a + b)
    n = hi - lo + 1
    if n == 1:
        return 1.0
    observed = [[0.0] * n for _ in range(n)]
    for x, y in zip(a, b):
        observed[x - lo][y - lo] += 1
    hist_a = Counter(x - lo for x in a)
    hist_b = Counter(y - lo for y in b)
    total = len(a)
    num = den = 0.0
    for i in range(n):
        for j in range(n):
            w = (i - j) ** 2 / (n - 1) ** 2
            expected = hist_a[i] * hist_b[j] / total
            num += w * observed[i][j]
            den += w * expected
    return 1.0 - num / den if den else None


def agreement(pred: list[float], gold: list[float], step: float = 1.0) -> dict:
    """Exact, within-one-step, MAE and QWK. step is the smallest mark unit (0.5 for half marks)."""
    n = len(pred)
    if not n:
        return {"n": 0}
    diffs = [abs(p - g) for p, g in zip(pred, gold)]
    to_int = lambda x: int(round(x / step))  # noqa: E731
    qwk = quadratic_weighted_kappa([to_int(p) for p in pred], [to_int(g) for g in gold])
    return {
        "n": n,
        "exact": sum(d < 1e-9 for d in diffs) / n,
        "within_step": sum(d <= step + 1e-9 for d in diffs) / n,
        "mae": sum(diffs) / n,
        "qwk": qwk,
    }


def pearson(x: list[float], y: list[float]) -> float | None:
    n = len(x)
    if n < 3:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sx = math.sqrt(sum((v - mx) ** 2 for v in x))
    sy = math.sqrt(sum((v - my) ** 2 for v in y))
    if not sx or not sy:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy)


def fmt(v, pct=False, digits=3):
    if v is None:
        return "—"
    return f"{v * 100:.1f}%" if pct else f"{v:.{digits}f}"
