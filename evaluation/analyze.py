"""Score the saved Gemini replies against the teachers' and experts' marks. No model calls.

Usage: python analyze.py        writes evaluation/reports/results.md and results.json

Variants computed from the same saved replies (one Gemini request per sheet):
  full          AutoGrade as deployed: code adds up the per-point marks, applies the length cap,
                no negative marks; flagged marks count as suggestions
  no_length_cap the same, with the length cap switched off
  raw_llm       Gemini's own total for each answer (marks_awarded), clipped to 0..max, no checks
Baselines:
  constant      every answer gets the most common mark in the tuning data (never reads the answer)
"""

from __future__ import annotations

import contextlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from common import DATASETS, REPORTS, RUNS, agreement, fmt, load_json, pearson, save_json, setup_backend

setup_backend()

import datasets as ds  # noqa: E402
import services.gemini_sheet as gs  # noqa: E402
from models.schemas import QuestionRubric  # noqa: E402

NEEDS_REVIEW = {"FLAGGED", "MANUAL"}


@contextlib.contextmanager
def variant(name: str):
    saved = gs._length_cap
    if name == "no_length_cap":
        gs._length_cap = lambda rubric, answer, style: (None, "")
    try:
        yield
    finally:
        gs._length_cap = saved


def readings_of(path: Path):
    data = load_json(path)
    return [(gs.SheetExtraction.model_validate(r["extraction"]), r["offset"]) for r in data["readings"]], data


def grade(rubric_dicts, path, style="GENEROUS", name="full"):
    rubric = [QuestionRubric(**q) for q in rubric_dicts]
    readings, data = readings_of(path)
    with variant(name):
        grades, _ = gs.grade_readings(rubric, readings, data["problems"], data["model"], style)
    return {g.question_number: g for g in grades}, readings, data


def raw_marks(readings, rubric_dicts) -> dict[int, float]:
    maxima = {int(q["question_number"]): float(q["max_marks"]) for q in rubric_dicts}
    out = {}
    for extraction, _ in readings:
        for a in extraction.answers:
            q = a.question_number
            if q in maxima and a.marks_awarded is not None and q not in out:
                out[q] = min(max(float(a.marks_awarded), 0.0), maxima[q])
    return out


# ------------------------------------------------------------------------------------- Mendeley

def mendeley(condition: str) -> dict | None:
    run_dir = RUNS / "mendeley" / condition
    files = {p.stem: p for p in run_dir.glob("*.json")} if run_dir.exists() else {}
    test = [s for s in ds.MENDELEY_TEST if s in files]
    if not test:
        return None
    rubric = ds.mendeley_rubric()
    gold = ds.mendeley_ground_truth()
    key = ds.mendeley_key()

    # Constant baseline: the most common teacher mark on the tuning sheets.
    tune_marks = [m for s in ds.MENDELEY_TUNE for m in gold[s]["short"].values() if m is not None]
    constant = Counter(tune_marks).most_common(1)[0][0]

    out = {"sheets": len(test), "constant_mark": constant}
    for name in ("full", "no_length_cap", "raw_llm"):
        pred, gl, flagged = [], [], []
        mcq_read_ok = mcq_n = 0
        tot_pred, tot_gold = [], []
        for s in test:
            grades, readings, data = grade(rubric, files[s], name="no_length_cap" if name == "no_length_cap" else "full")
            raw = raw_marks(readings, rubric) if name == "raw_llm" else {}
            written_total = 0.0
            for q in range(21, 36):
                g = grades.get(q)
                m = gold[s]["short"].get(q)
                p = raw.get(q, 0.0) if name == "raw_llm" else (g.final_score if g else 0.0)
                written_total += p
                if m is None:
                    continue
                pred.append(p)
                gl.append(m)
                flagged.append(bool(g and g.routing in NEEDS_REVIEW))
            mcq_total = 0.0
            for q in range(1, 21):
                g = grades.get(q)
                letter = (g.selected_option if g else "") or ""
                teacher_letter = gold[s]["mcq"].get(q)
                if teacher_letter:
                    mcq_n += 1
                    mcq_read_ok += letter == teacher_letter
                mcq_total += 1.0 if letter == key.get(q) else 0.0
            teacher_written = sum(v for v in gold[s]["short"].values() if v is not None)
            tot_pred.append(written_total + mcq_total)
            tot_gold.append(teacher_written + gold[s]["mcq_mark"])
        res = {"written": agreement(pred, gl, 0.5),
               "mcq_letter_accuracy": mcq_read_ok / mcq_n if mcq_n else None, "mcq_n": mcq_n,
               "total_mae": sum(abs(a - b) for a, b in zip(tot_pred, tot_gold)) / len(tot_pred),
               "total_pearson": pearson(tot_pred, tot_gold)}
        if name == "full":
            res["flags"] = flag_quality(pred, gl, flagged)
        out[name] = res
        if name == "full":
            out["constant"] = {"written": agreement([constant] * len(gl), gl, 0.5)}
    return out


def flag_quality(pred, gold, flagged, tol=0.5) -> dict:
    """How well the flags pick out the marks a teacher would change (off by more than tol)."""
    wrong = [abs(p - g) > tol + 1e-9 for p, g in zip(pred, gold)]
    n = len(pred)
    tp = sum(w and f for w, f in zip(wrong, flagged))
    fl = sum(flagged)
    nw = sum(wrong)
    unflagged = [(p, g) for p, g, f in zip(pred, gold, flagged) if not f]
    return {
        "flag_rate": fl / n if n else None,
        "wrong_rate": nw / n if n else None,
        "recall_of_wrong": tp / nw if nw else None,
        "precision": tp / fl if fl else None,
        "unflagged_exact": (sum(abs(p - g) < 1e-9 for p, g in unflagged) / len(unflagged)) if unflagged else None,
        "unflagged_within": (sum(abs(p - g) <= tol + 1e-9 for p, g in unflagged) / len(unflagged)) if unflagged else None,
        "unflagged_n": len(unflagged),
    }


# ------------------------------------------------------------------------------------- CHECK-MAT

def checkmat() -> dict | None:
    run_dir = RUNS / "checkmat" / "default"
    files = {p.stem: p for p in run_dir.glob("*.json")} if run_dir.exists() else {}
    if not files:
        return None
    gold = ds.checkmat_ground_truth()
    criteria = ds.checkmat_criteria()
    out = {"solutions": len(files)}
    for name in ("full", "raw_llm"):
        pred, gl, flagged = [], [], []
        per_task = defaultdict(lambda: ([], []))
        for sid, path in sorted(files.items()):
            g0 = gold[sid]
            rubric = ds.checkmat_rubric(g0["task"], criteria)
            grades, readings, data = grade(rubric, path)
            if name == "raw_llm":
                p = raw_marks(readings, rubric).get(g0["task"], 0.0)
            else:
                g = grades.get(g0["task"])
                p = g.final_score if g else 0.0
                flagged.append(bool(g and g.routing in NEEDS_REVIEW))
            pred.append(p)
            gl.append(float(g0["score"]))
            per_task[g0["task"]][0].append(p)
            per_task[g0["task"]][1].append(float(g0["score"]))
        rounded = [float(int(x + 0.5)) for x in pred]  # EGE marks are whole numbers; .5 rounds up
        res = {"all": agreement(pred, gl, 1.0), "rounded": agreement(rounded, gl, 1.0),
               "half_marks_given": sum(abs(x - round(x)) > 1e-9 for x in pred),
               "per_task": {t: agreement(a, b, 1.0) for t, (a, b) in sorted(per_task.items())}}
        if name == "full":
            res["flags"] = flag_quality(pred, gl, flagged, tol=0.0)
        out[name] = res
    out["published_baselines"] = checkmat_published(set(files))
    # Constant baseline: the most common expert mark for each task (computed on the same data, so optimistic).
    by_task = defaultdict(list)
    for sid in files:
        by_task[gold[sid]["task"]].append(gold[sid]["score"])
    common = {t: Counter(v).most_common(1)[0][0] for t, v in by_task.items()}
    cg = [float(gold[sid]["score"]) for sid in sorted(files)]
    cp = [float(common[gold[sid]["task"]]) for sid in sorted(files)]
    out["constant"] = agreement(cp, cg, 1.0)
    secs, toks_in, toks_out = [], [], []
    for path in files.values():
        d = load_json(path)
        secs.append(d.get("seconds", 0))
        toks_in.append(d.get("usage", {}).get("input_tokens", 0))
        toks_out.append(d.get("usage", {}).get("output_tokens", 0))
    out["cost"] = {"median_seconds": sorted(secs)[len(secs) // 2], "mean_input_tokens": sum(toks_in) / len(toks_in),
                   "mean_output_tokens": sum(toks_out) / len(toks_out)}
    return out


def checkmat_published(ids: set[str]) -> dict:
    """Scores the CHECK-MAT authors published for other models, without the answer or solution."""
    base = DATASETS / "03_CHECK-MAT_russian_handwritten_math" / "dataset_benchmark" / "benchmark_results"
    out = {}
    for f in base.glob("*/*.json"):
        if "without_answer" not in f.parent.name or "analysis" in f.name:
            continue
        try:
            rows = json.loads(f.read_text())
        except Exception:
            continue
        if not isinstance(rows, list):
            continue
        pairs = [(float(r["score"]), float(r["expected_score"])) for r in rows
                 if r.get("score") is not None and r.get("expected_score") is not None and r.get("solution_id") in ids]
        if len(pairs) < 20:
            continue
        model = rows[0].get("model_id", f.parent.name)
        out[model] = agreement([p for p, _ in pairs], [g for _, g in pairs], 1.0)
    return out


# ------------------------------------------------------------------------------------- Handwritten ASAP

def asap_human_agreement() -> dict:
    """How well ASAP's two human markers agree with each other (training answers for sets 3 and 4)."""
    import csv
    pairs = defaultdict(lambda: ([], []))
    with (ds.ASAP / "huggingface_D_ASAP-SAS" / "train.csv").open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            sid = int(float(row["essay_set"]))
            if sid in (3, 4) and row["Score1"] and row["Score2"]:
                pairs[sid][0].append(float(row["Score1"]))
                pairs[sid][1].append(float(row["Score2"]))
    out = {p: agreement(a, b, 1.0) for p, (a, b) in sorted(pairs.items())}
    out["all"] = agreement(pairs[3][0] + pairs[4][0], pairs[3][1] + pairs[4][1], 1.0)
    return out


def asap(condition: str = "default") -> dict | None:
    run_dir = RUNS / "asap" / condition
    files = {p.stem: p for p in run_dir.glob("*.json")} if run_dir.exists() else {}
    if not files:
        return None
    gold = ds.asap_ground_truth()
    passage = ds.asap_passage()
    style = "STRICT" if condition == "strict" else "GENEROUS"
    out = {"answers": len(files), "of": len(gold), "style": style}
    for name in ("full", "no_length_cap", "raw_llm"):
        pred, gl, flagged = [], [], []
        per_prompt = defaultdict(lambda: ([], []))
        for sid, path in sorted(files.items()):
            g0 = gold[sid]
            rubric = ds.asap_rubric(g0["prompt"], passage)
            grades, readings, data = grade(rubric, path, style=style, name="no_length_cap" if name == "no_length_cap" else "full")
            if name == "raw_llm":
                p = raw_marks(readings, rubric).get(1, 0.0)
            else:
                g = grades.get(1)
                p = g.final_score if g else 0.0
                # Each ASAP image holds one answer with no question number written, so the
                # "number inferred" flag fires on every answer; it is left out here.
                flagged.append(bool(g and g.routing in NEEDS_REVIEW and g.flag_reason != "NUMBER_INFERRED"))
            pred.append(p)
            gl.append(float(g0["score"]))
            per_prompt[g0["prompt"]][0].append(p)
            per_prompt[g0["prompt"]][1].append(float(g0["score"]))
        rounded = [float(int(x + 0.5)) for x in pred]  # ASAP marks are whole numbers; .5 rounds up
        res = {"all": agreement(rounded, gl, 1.0), "unrounded_mae": agreement(pred, gl, 0.5)["mae"],
               "half_marks_given": sum(abs(x - round(x)) > 1e-9 for x in pred),
               "per_prompt": {p: agreement([float(int(x + 0.5)) for x in a], b, 1.0)
                              for p, (a, b) in sorted(per_prompt.items())}}
        if name != "raw_llm":
            res["flags"] = flag_quality(rounded, gl, flagged, tol=0.0)
        conf = defaultdict(Counter)
        for x, y in zip(rounded, gl):
            conf[str(int(y))][str(int(x))] += 1
        res["confusion"] = {k: dict(v) for k, v in conf.items()}
        out[name] = res
    common = {p: Counter(v["score"] for v in gold.values() if v["prompt"] == p).most_common(1)[0][0] for p in (3, 4)}
    ids = sorted(files)
    out["constant"] = agreement([float(common[gold[i]["prompt"]]) for i in ids], [float(gold[i]["score"]) for i in ids], 1.0)
    out["human"] = asap_human_agreement()
    return out


# ------------------------------------------------------------------------------------- report

def pct(v):
    return fmt(v, True)


def row(name, a, half=False):
    """One table row in plain words: answers, same mark, close, average difference, agreement score."""
    return (f"| {name} | {a['n']} | {pct(a['exact'])} | {pct(a['within_step'])} | "
            f"{fmt(a['mae'], digits=2)} | {fmt(a['qwk'], digits=2)} |")


def table_head(close):
    return [f"| Who marked | Answers | Same mark as the teacher | {close} | Average difference (marks) | Agreement score (0 to 1) |",
            "|---|---|---|---|---|---|"]


def flags_line(fq, tol_words):
    return (f"**Double-check requests:** AutoGrade asked the teacher to double-check {pct(fq['flag_rate'])} of the answers. "
            f"Of the answers it marked wrongly ({tol_words}), it asked about {pct(fq['recall_of_wrong'])}.")


HOW_TO_READ = [
    "# How well AutoGrade marks, compared with real teachers",
    "",
    "Real students' handwritten answers, already marked by real teachers or examiners, were given to AutoGrade. "
    "AutoGrade never saw the teachers' marks. Then each of AutoGrade's marks was compared with the teacher's mark.",
    "",
    "## How to read the tables",
    "",
    "| Column | What it means | Better is |",
    "|---|---|---|",
    "| Answers | How many answers were compared | |",
    "| Same mark as the teacher | How often AutoGrade gave exactly the teacher's mark | Higher |",
    "| At most ½ mark off / At most 1 mark off | How often AutoGrade was right or only a little off | Higher |",
    "| Average difference (marks) | How far AutoGrade's mark was from the teacher's, on average | Lower |",
    "| Agreement score (0 to 1) | Does AutoGrade tell good answers from bad ones? 0 = no better than guessing, "
    "about 0.5 = moderate, about 0.75 = as good as a second teacher, 1 = perfect | Higher |",
    "",
    "Two rows in some tables are there only for comparison:",
    "",
    "- **Lazy marker:** never reads the answer and gives everyone the most common mark. It can look good on "
    "\"Same mark as the teacher\" when most students got the same mark, but its agreement score is 0. "
    "A real marking system has to beat it.",
    "- **A second human teacher:** how often two real teachers agree with each other. This is about the best "
    "anyone can expect, because teachers do not always agree either.",
    "",
]

GLOSSARY = [
    "## Technical names (for the paper)",
    "",
    "| Plain name here | Name in research papers |",
    "|---|---|",
    "| Same mark as the teacher | Exact agreement (accuracy) |",
    "| At most ½ / 1 mark off | Adjacent agreement (within one step) |",
    "| Average difference (marks) | Mean absolute error (MAE) |",
    "| Agreement score (0 to 1) | Quadratic weighted kappa (QWK) |",
    "| Totals move together with the teacher's | Pearson correlation of sheet totals |",
    "| Double-check requests that catch wrong marks | Flag recall (and precision) |",
    "| AutoGrade without the length rule / AI's own mark without checks | Ablations |",
    "| Lazy marker | Majority-class baseline |",
    "| A second human teacher | Inter-rater agreement (human ceiling) |",
    "",
    "Exact numbers for every result are in `results.json`.",
    "",
]

CHECKMAT_SHORT = {13: "Equations (trig / exponential)", 14: "3D geometry", 15: "Inequality", 16: "Money word problem",
                  17: "2D geometry", 18: "Equation with a parameter", 19: "Number theory"}


def short_table(rows, close):
    out = [f"| Who marked | Same mark | {close} | Agreement (0–1) |", "|---|---|---|---|"]
    for name, a in rows:
        out.append(f"| {name} | {pct(a['exact'])} | {pct(a['within_step'])} | {fmt(a['qwk'], digits=2)} |")
    return out


def main():
    results = {"mendeley_default": mendeley("default"), "mendeley_calibrated": mendeley("calibrated"),
               "checkmat": checkmat(), "asap_default": asap("default"), "asap_strict": asap("strict")}
    save_json(REPORTS / "results.json", results)
    md, cm = results["mendeley_default"], results["checkmat"]
    ag, ast = results["asap_default"], results["asap_strict"]

    lines = ["# AutoGrade vs real teachers", "",
             "AutoGrade marked real students' answers without seeing the teachers' marks. "
             "Then we checked how often it agreed.", "",
             "- **Same mark:** how often AutoGrade gave exactly the teacher's mark.",
             "- **Close:** right, or only a little off.",
             "- **Agreement (0–1):** does it tell good answers from bad? 0 = guessing, 0.75 = as good as a second teacher.",
             "- **Lazy marker:** gives everyone the most common mark without reading. AutoGrade must beat it.", ""]

    if md:
        f = md["full"]
        lines += ["## 1. English exam (40 sheets)", ""]
        lines += short_table([("AutoGrade", f["written"]), ("Lazy marker", md["constant"]["written"])], "Close (±½)")
        lines += ["", f"- Multiple-choice letters read correctly: **{pct(f['mcq_letter_accuracy'])}**",
                  f"- Sheet totals: about **{fmt(f['total_mae'], digits=1)} marks** off out of 50", ""]

    if cm:
        lines += ["## 2. Russian maths exam (122 solutions)", ""]
        rows = [("AutoGrade", cm["full"]["all"]), ("Lazy marker (per task)", cm["constant"])]
        rows += [(f"OpenAI {m.split('/')[-1]} (published)", a) for m, a in sorted(cm["published_baselines"].items())]
        lines += short_table(rows, "Close (±1)")
        lines += [""]

    if ag or ast:
        base = ast or ag
        lines += ["## 3. Reading answers (349 answers)", ""]
        rows = []
        if ag:
            rows.append(("AutoGrade, Generous style", ag["full"]["all"]))
        if ast:
            rows.append(("AutoGrade, Strict style", ast["full"]["all"]))
        rows += [("Lazy marker", base["constant"]), ("A second human teacher", base["human"]["all"])]
        lines += short_table(rows, "Close (±1)")
        lines += [""]

    lines += ["## What it means", "",
              "- **Strong:** multiple-choice reading and sheet totals.",
              "- **Moderate:** written answers. Rarely far off, but the exact mark is right only about half the time.",
              "- **Strict style is much better** than Generous on written answers.",
              "- **Weak:** the double-check requests miss most wrong marks.", "",
              "Full numbers for the paper: `results.json`."]
    (REPORTS / "results.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
