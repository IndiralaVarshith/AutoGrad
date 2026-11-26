"""Load each evaluation dataset as items for AutoGrade, plus their ground-truth marks.

An item is one student's sheet: {"id", "file_bytes", "extension", "rubric": [QuestionRubric dicts]}.
Ground truth is loaded separately by ground_truth() and is never given to the marking code.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from common import DATASETS, RUBRICS, images_to_pdf, load_json, save_json

# ---------------------------------------------------------------------------------- 01 Mendeley

MENDELEY = DATASETS / "01_mendeley_digitized_exam_papers_english"
# The split used during development (seed 20261105, one sheet per band of five teacher totals).
MENDELEY_TUNE = ["Student_4", "Student_6", "Student_7", "Student_11", "Student_19",
                 "Student_21", "Student_26", "Student_36", "Student_43", "Student_49"]
MENDELEY_ALL = [f"Student_{n}" for n in range(1, 51)]
MENDELEY_TEST = [s for s in MENDELEY_ALL if s not in MENDELEY_TUNE]
MENDELEY_RUBRIC = RUBRICS / "mendeley.json"


def mendeley_rubric() -> list[dict]:
    """Read the question paper and answer key with AutoGrade's own paper reader (one Gemini request).

    Saved after the first run so every evaluation uses the same approved rubric, as a teacher would.
    """
    if MENDELEY_RUBRIC.exists():
        return load_json(MENDELEY_RUBRIC)["questions"]
    from services.gemini_paper import read_question_paper
    from services.upload_limits import actual_extension

    paper = (MENDELEY / "Question.pdf").read_bytes()
    key = (MENDELEY / "answerkey.txt").read_bytes()
    result = read_question_paper(
        paper, actual_extension(paper, ".pdf", "Question.pdf"),
        answer_key=key, answer_key_extension=".txt", paper_type="MIXED",
    )
    if result.get("unreadable"):
        raise RuntimeError(f"Paper not readable: {result}")
    for q in result["questions"]:
        # The key is the teacher's own upload, so it sets the correct option (as in the app).
        q["correct_option"] = q.get("suggested_option", "")
    save_json(MENDELEY_RUBRIC, {"source": "AutoGrade paper reader on Question.pdf + answerkey.txt",
                                "problems": result.get("problems", []), "questions": result["questions"]})
    return result["questions"]


def mendeley_items(ids: list[str]) -> list[dict]:
    rubric = mendeley_rubric()
    return [{"id": sid, "file_bytes": (MENDELEY / "Student_Pdf" / f"{sid}.pdf").read_bytes(),
             "extension": ".pdf", "rubric": rubric} for sid in ids]


def mendeley_ground_truth() -> dict:
    """Teacher marks: MCQ letters for Q1-20, marks out of 2 for Q21-35 (None = no usable mark)."""
    out = {}
    with (MENDELEY / "Teacher_manual_marks_Anonymized.csv").open(newline="") as fh:
        for row in csv.DictReader(fh):
            mcq = {q: (row[str(q)].strip() if row[str(q)].strip() in {"A", "B", "C", "D"} else None)
                   for q in range(1, 21)}
            short = {}
            for q in range(21, 36):
                try:
                    v = float(row[str(q)].strip())
                    short[q] = v if 0 <= v <= 2 else None
                except ValueError:
                    short[q] = None
            out[row["Student_ID"]] = {"mcq": mcq, "short": short, "mcq_mark": float(row["MCQ mark"])}
    return out


def mendeley_key() -> dict[int, str]:
    key = {}
    for line in (MENDELEY / "answerkey.txt").read_text().splitlines()[1:]:
        parts = line.split(",", 2)
        if len(parts) >= 3 and parts[1] == "MCQ":
            key[int(parts[0])] = parts[2].strip()
    return key


# ---------------------------------------------------------------------------------- 03 CHECK-MAT

CHECKMAT = DATASETS / "03_CHECK-MAT_russian_handwritten_math" / "dataset_benchmark"
CHECKMAT_MAX = {13: 2, 14: 3, 15: 2, 16: 2, 17: 3, 18: 4, 19: 4}
CHECKMAT_TOPIC = {13: "trigonometric / exponential equation with root selection (parts a, b)",
                  14: "stereometry: a) proof, b) computation",
                  15: "inequality",
                  16: "financial mathematics (economic word problem)",
                  17: "planimetry: a) proof, b) computation",
                  18: "equation or system with a parameter",
                  19: "number theory (parts a, b, c)"}
# Additive marking points that follow the official EGE criteria (text below) for each task.
CHECKMAT_POINTS = {
    13: [("Part a): correct answer, obtained with justification", 1), ("Part b): correct roots selected on the given interval, with justification", 1)],
    14: [("Part a): correct proof of the statement", 1), ("Part b): correct answer obtained with justification", 2)],
    15: [("Correct sequence of all solution steps (method)", 1), ("Correct final answer, justified", 1)],
    16: [("Correct mathematical model of the problem", 1), ("Correct answer obtained with justification", 1)],
    17: [("Part a): correct proof of the statement", 1), ("Part b): correct answer obtained with justification", 2)],
    18: [("Roots found and the problem correctly reduced to studying them under the conditions", 1),
         ("Correct reasoning gives an interval of the parameter values", 1),
         ("Set of values correct except possibly boundary points", 1),
         ("Fully correct, justified answer", 1)],
    19: [("Part a): correct justified answer", 1), ("Part b): correct justified answer", 1), ("Part c): correct justified answer", 2)],
}


def checkmat_criteria() -> dict[int, str]:
    src = (DATASETS / "03_CHECK-MAT_russian_handwritten_math" / "backend" / "app" / "utils"
           / "specialized_prompts.py").read_text(encoding="utf-8")
    out = {}
    for t in CHECKMAT_MAX:
        m = re.search(r"## Критерии оценивания задания %d\n(.*?)\n\n" % t, src, re.S)
        out[t] = m.group(1).strip() if m else ""
    return out


def checkmat_rubric(task: int, criteria: dict[int, str]) -> list[dict]:
    return [{
        "question_number": task,
        "sub_part": "",
        "question_text": (f"Russian Unified State Exam (EGE) mathematics, task {task}: {CHECKMAT_TOPIC[task]}. "
                          f"The problem statement is written at the top of the student's solution or is implied by it. "
                          f"Mark the whole solution against the official criteria:\n{criteria[task]}"),
        "max_marks": float(CHECKMAT_MAX[task]),
        "criteria": [{"description": d, "max_marks": float(m)} for d, m in CHECKMAT_POINTS[task]],
        "question_format": "LONG_ANSWER",
        "section": "EGE",
    }]


def checkmat_solutions() -> list[dict]:
    """One entry per student solution: id, task, expert score, page images (student work only)."""
    sols = []
    for task_dir in sorted(p for p in CHECKMAT.iterdir() if p.is_dir() and p.name.isdigit()):
        for example in sorted(p for p in task_dir.iterdir() if p.is_dir()):
            for sol in sorted(p for p in example.iterdir() if p.is_dir()):
                pages = sorted((sol / "without_answer").glob("*.png"),
                               key=lambda p: int(re.search(r"_part_(\d+)", p.name).group(1)) if "_part_" in p.name else 1)
                if not pages:
                    continue
                score = int(re.search(r"estimate_(\d+)", pages[0].name).group(1))
                sols.append({"id": sol.name, "task": int(task_dir.name), "score": score, "pages": pages})
    return sols


def checkmat_items(ids: list[str] | None = None) -> list[dict]:
    criteria = checkmat_criteria()
    save_json(RUBRICS / "checkmat.json", {t: checkmat_rubric(t, criteria) for t in CHECKMAT_MAX})
    items = []
    for s in checkmat_solutions():
        if ids and s["id"] not in ids:
            continue
        items.append({"id": s["id"], "file_bytes": images_to_pdf(s["pages"]), "extension": ".pdf",
                      "rubric": checkmat_rubric(s["task"], criteria)})
    return items


def checkmat_ground_truth() -> dict:
    return {s["id"]: {"task": s["task"], "score": s["score"], "max": CHECKMAT_MAX[s["task"]]}
            for s in checkmat_solutions()}


# ---------------------------------------------------------------------------------- 02 Handwritten ASAP

ASAP = DATASETS / "02_handwritten_ASAP_short_answers_zenodo"
ASAP_IMAGES = ASAP / "extracted" / "Handwritten ASAP SAS"
ASAP_SCORES = ASAP / "kaggle" / "public_leaderboard_solution.csv"
# The question text and reading passage come from the ASAP training file (sets 3 and 4 share the passage).
# The official rubric pages need a login, so the marking points below follow the published ASAP scoring
# guide for these two 0-2 reading items: 2 = complete and supported, 1 = partial, 0 = no relevant answer.
ASAP_QUESTION = {
    3: "Explain how pandas in China are similar to koalas in Australia and how they both are different from pythons. "
       "Support your response with information from the article.",
    4: "Explain the significance of the word \"invasive\" to the rest of the article. "
       "Support your response with information from the article.",
}
ASAP_POINTS = {
    3: [("Explains how pandas and koalas are similar (both are specialists: each eats almost only one food, "
         "bamboo or eucalyptus, and needs a stable environment), using information from the article", 1),
        ("Explains how pythons are different (a generalist: eats many things, adapts to and spreads into new places "
         "such as the Everglades), using information from the article", 1)],
    4: [("Explains what \"invasive\" means and why it matters to the article (non-native species spreading unchecked "
         "and threatening native and endangered species, or the debate over the label: MacInnes calls them "
         "'introduced' and says 'invasive' passes judgment)", 1),
        ("Supports the explanation with specific information from the article (for example the Burmese pythons "
         "in the Everglades, the Key Largo wood rat, the permit law, the title)", 1)],
}


def asap_passage() -> str:
    path = ASAP / "huggingface_D_ASAP-SAS" / "train.csv"
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["essay_set"].split(".")[0] == "3":
                text = row["prompt"]
                start = text.find("One Man's Pet")
                end = text.find("Prompt—")
                return text[start:end].strip()
    raise RuntimeError("ASAP passage not found")


def asap_rubric(prompt: int, passage: str) -> list[dict]:
    return [{
        "question_number": 1,
        "sub_part": "",
        "question_text": (f"Reading comprehension (ASAP short answer, set {prompt}). Students read this article first:\n\n"
                          f"{passage}\n\nQuestion: {ASAP_QUESTION[prompt]}"),
        "max_marks": 2.0,
        "criteria": [{"description": d, "max_marks": float(m)} for d, m in ASAP_POINTS[prompt]],
        "question_format": "SHORT_ANSWER",
        "section": "ASAP",
    }]


def asap_ground_truth() -> dict:
    scores = {}
    with ASAP_SCORES.open(newline="") as fh:
        for row in csv.DictReader(fh):
            scores[row["id"]] = (int(row["essay_set"]), int(row["essay_score"]))
    out = {}
    for path in sorted(ASAP_IMAGES.glob("prompt-*/SAS_*.png")):
        prompt, asap_id = re.search(r"SAS_(\d)_(\d+)", path.name).groups()
        if asap_id in scores and scores[asap_id][0] == int(prompt):
            out[path.stem] = {"prompt": int(prompt), "score": scores[asap_id][1], "path": path}
    return out


def asap_items(ids: list[str] | None = None) -> list[dict]:
    passage = asap_passage()
    save_json(RUBRICS / "asap.json", {p: asap_rubric(p, passage) for p in ASAP_QUESTION})
    gold = asap_ground_truth()
    # Alternate prompts 3 and 4 so a partial run still covers both.
    by_prompt = {p: [k for k in sorted(gold) if gold[k]["prompt"] == p] for p in ASAP_QUESTION}
    order = [k for pair in __import__("itertools").zip_longest(*by_prompt.values()) for k in pair if k]
    return [{"id": k, "file_bytes": gold[k]["path"].read_bytes(), "extension": ".png",
             "rubric": asap_rubric(gold[k]["prompt"], passage)}
            for k in order if not ids or k in ids]
