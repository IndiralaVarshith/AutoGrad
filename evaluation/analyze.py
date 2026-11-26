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


def asap() -> dict | None:
    run_dir = RUNS / "asap" / "default"
    files = {p.stem: p for p in run_dir.glob("*.json")} if run_dir.exists() else {}
    if not files:
        return None
    gold = ds.asap_ground_truth()
    passage = ds.asap_passage()
    out = {"answers": len(files), "of": len(gold)}
    for name in ("full", "no_length_cap", "raw_llm"):
        pred, gl, flagged = [], [], []
        per_prompt = defaultdict(lambda: ([], []))
        for sid, path in sorted(files.items()):
            g0 = gold[sid]
            rubric = ds.asap_rubric(g0["prompt"], passage)
            grades, readings, data = grade(rubric, path, name="no_length_cap" if name == "no_length_cap" else "full")
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

def main():
    results = {"mendeley_default": mendeley("default"), "mendeley_calibrated": mendeley("calibrated"),
               "checkmat": checkmat(), "asap": asap()}
    save_json(REPORTS / "results.json", results)
    lines = ["# AutoGrade evaluation results", ""]
    for key, label in (("mendeley_default", "Mendeley, 40 held-out sheets, default (Generous)"),
                       ("mendeley_calibrated", "Mendeley, 40 held-out sheets, calibrated from Student_21")):
        r = results[key]
        if not r:
            continue
        lines += [f"## {label}", "", f"Sheets scored: {r['sheets']}", "",
                  "| Variant | n | Exact | Within 0.5 | MAE | QWK |", "|---|---|---|---|---|---|"]
        for name in ("full", "no_length_cap", "raw_llm", "constant"):
            w = r[name]["written"]
            lines.append(f"| {name} | {w['n']} | {fmt(w['exact'], True)} | {fmt(w['within_step'], True)} | {fmt(w['mae'])} | {fmt(w['qwk'])} |")
        f = r["full"]
        fq = f["flags"]
        lines += ["", f"MCQ letters read correctly: {fmt(f['mcq_letter_accuracy'], True)} of {f['mcq_n']}",
                  f"Sheet totals: MAE {fmt(f['total_mae'])}, Pearson {fmt(f['total_pearson'])}",
                  f"Flags: {fmt(fq['flag_rate'], True)} of written answers flagged; "
                  f"{fmt(fq['recall_of_wrong'], True)} of wrong marks (off by more than 0.5) were flagged; "
                  f"precision {fmt(fq['precision'], True)}; unflagged answers within 0.5: {fmt(fq['unflagged_within'], True)} (n={fq['unflagged_n']})", ""]
    r = results["checkmat"]
    if r:
        lines += ["## CHECK-MAT (expert marks 0–4)", "", f"Solutions scored: {r['solutions']}", "",
                  "| System | n | Exact | Within 1 mark | MAE | QWK |", "|---|---|---|---|---|---|"]
        for name in ("full", "raw_llm"):
            for kind in ("all", "rounded"):
                a = r[name][kind]
                label = f"AutoGrade {name}" + (" (rounded to whole marks)" if kind == "rounded" else "")
                lines.append(f"| {label} | {a['n']} | {fmt(a['exact'], True)} | {fmt(a['within_step'], True)} | {fmt(a['mae'])} | {fmt(a['qwk'])} |")
        a = r["constant"]
        lines.append(f"| Constant (most common mark per task) | {a['n']} | {fmt(a['exact'], True)} | {fmt(a['within_step'], True)} | {fmt(a['mae'])} | {fmt(a['qwk'])} |")
        for model, a in sorted(r["published_baselines"].items()):
            lines.append(f"| {model} (published, without answer) | {a['n']} | {fmt(a['exact'], True)} | {fmt(a['within_step'], True)} | {fmt(a['mae'])} | {fmt(a['qwk'])} |")
        lines += ["", "Per task (AutoGrade full):", "", "| Task | n | Exact | MAE | QWK |", "|---|---|---|---|---|"]
        for t, a in r["full"]["per_task"].items():
            lines.append(f"| {t} | {a['n']} | {fmt(a['exact'], True)} | {fmt(a['mae'])} | {fmt(a['qwk'])} |")
        fq = r["full"]["flags"]
        c = r["cost"]
        lines += ["", f"Half marks given by AutoGrade: {r['full']['half_marks_given']} of {r['solutions']}. "
                  f"Median time {c['median_seconds']} s; mean tokens {c['mean_input_tokens']:.0f} in, {c['mean_output_tokens']:.0f} out."]
        lines += ["", f"Flags: {fmt(fq['flag_rate'], True)} flagged; {fmt(fq['recall_of_wrong'], True)} of wrong marks flagged; "
                  f"unflagged exact {fmt(fq['unflagged_exact'], True)} (n={fq['unflagged_n']})", ""]
    r = results["asap"]
    if r:
        lines += ["## Handwritten ASAP short answers (prompts 3 and 4, marks 0–2)", "",
                  f"Answers scored: {r['answers']} of {r['of']}. AutoGrade's half marks are rounded up to whole marks, "
                  "as ASAP marks are whole numbers.", "",
                  "| System | n | Exact | Within 1 mark | MAE | QWK |", "|---|---|---|---|---|---|"]
        for name in ("full", "no_length_cap", "raw_llm"):
            a = r[name]["all"]
            lines.append(f"| AutoGrade {name} | {a['n']} | {fmt(a['exact'], True)} | {fmt(a['within_step'], True)} | {fmt(a['mae'])} | {fmt(a['qwk'])} |")
        a = r["constant"]
        lines.append(f"| Constant (most common mark per prompt) | {a['n']} | {fmt(a['exact'], True)} | {fmt(a['within_step'], True)} | {fmt(a['mae'])} | {fmt(a['qwk'])} |")
        a = r["human"]["all"]
        lines.append(f"| Human marker 1 vs marker 2 (ASAP training answers) | {a['n']} | {fmt(a['exact'], True)} | {fmt(a['within_step'], True)} | {fmt(a['mae'])} | {fmt(a['qwk'])} |")
        lines += ["", "Per prompt (exact / QWK):", "", "| Prompt | n | AutoGrade full | no length cap | Humans |", "|---|---|---|---|---|"]
        for p in (3, 4):
            f, n, h = r["full"]["per_prompt"].get(p), r["no_length_cap"]["per_prompt"].get(p), r["human"][p]
            if f:
                lines.append(f"| {p} | {f['n']} | {fmt(f['exact'], True)} / {fmt(f['qwk'])} | {fmt(n['exact'], True)} / {fmt(n['qwk'])} | {fmt(h['exact'], True)} / {fmt(h['qwk'])} |")
        lines += ["", "AutoGrade (full) against the human mark, all answers scored:", "",
                  "| Human mark | AutoGrade 0 | AutoGrade 1 | AutoGrade 2 |", "|---|---|---|---|"]
        for h in (0, 1, 2):
            row = r["full"]["confusion"].get(str(h), {})
            lines.append(f"| {h} | {row.get('0', 0)} | {row.get('1', 0)} | {row.get('2', 0)} |")
        fq = r["full"]["flags"]
        lines += ["", f"Flags (not counting 'number inferred'): {fmt(fq['flag_rate'], True)} flagged; {fmt(fq['recall_of_wrong'], True)} of wrong marks flagged; "
                  f"precision {fmt(fq['precision'], True)}; unflagged exact {fmt(fq['unflagged_exact'], True)} (n={fq['unflagged_n']})", ""]
    (REPORTS / "results.md").write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
