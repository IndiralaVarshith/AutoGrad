"""A4: the metrics, on small hand-made cases, and the guards around the untouched sheets."""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

import pytest

from services.evaluation import (
    ALL_STUDENTS,
    TeacherSheet,
    load_split,
    load_teacher_marks,
    render_report,
    most_common_mark_baseline,
    score_run,
)

# Another installed package also owns the name "scripts", so load this file by path.
_SPEC = importlib.util.spec_from_file_location(
    "evaluate_grading", Path(__file__).resolve().parents[1] / "scripts" / "evaluate_grading.py"
)
script = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(script)

KEY = {q: "A" for q in range(1, 21)}


def teacher_sheet(mcq=None, short=None, mcq_mark=0.0):
    sheet = TeacherSheet(mcq_mark=mcq_mark)
    sheet.mcq = {q: "A" for q in range(1, 21)}
    sheet.short = {q: 2.0 for q in range(21, 36)}
    sheet.mcq.update(mcq or {})
    sheet.short.update(short or {})
    return sheet


def paper(**overrides):
    questions = []
    for q in range(1, 21):
        questions.append({"question_number": q, "selected_option": "A", "final_score": 1.0,
                          "routing": "AUTO_ACCEPT", "local_routing": "AUTO_ACCEPT"})
    for q in range(21, 36):
        questions.append({"question_number": q, "final_score": 2.0,
                          "routing": "AUTO_ACCEPT", "local_routing": "AUTO_ACCEPT"})
    for q, change in overrides.items():
        questions[int(q[1:]) - 1].update(change)
    return {"questions": questions}


def score(p, sheet, **kwargs):
    return score_run({"Student_1": p}, {"Student_1": sheet}, answer_key=KEY, **kwargs)


def test_perfect_sheet():
    result = score(paper(), teacher_sheet())
    assert result["mcq"]["exact_match_pct_of_all"] == 100.0
    assert result["short"]["agreement_pct_of_unflagged"] == 100.0
    assert result["silent_wrong"] == []
    assert result["flagged_overall_pct"] == 0.0


def test_wrong_unflagged_mcq_is_silent_wrong():
    result = score(paper(q3={"selected_option": "B"}), teacher_sheet())
    assert result["mcq"]["silent_wrong"] == 1
    assert result["silent_wrong"] == [
        {"student": "Student_1", "question": 3, "kind": "MCQ", "app": "B", "teacher": "A"}
    ]


def test_flagged_mcq_is_not_silent_and_not_correct():
    flagged = {"routing": "MANUAL", "local_routing": "MANUAL", "flag_reason": "UNCLEAR_OPTION"}
    result = score(paper(q3=flagged), teacher_sheet())
    assert result["mcq"]["flagged"] == 1 and result["mcq"]["silent_wrong"] == 0
    assert result["mcq"]["correct"] == 19
    assert result["flag_reasons"] == {"UNCLEAR_OPTION": 1}


@pytest.mark.parametrize("mark,agree", [(2.0, True), (1.5, True), (1.0, False), (0.0, False)])
def test_short_answer_window_is_half_a_mark(mark, agree):
    result = score(paper(q21={"final_score": mark}), teacher_sheet())
    assert result["short"]["agree"] == (15 if agree else 14)
    assert result["short"]["silent_wrong"] == (0 if agree else 1)


def test_flagged_draft_is_reported_but_never_counted_as_agreement():
    flagged = {"routing": "FLAGGED", "local_routing": "FLAGGED", "final_score": 0.0,
               "suggested_score": 2.0, "flag_reason": "SCORE_GAP"}
    result = score(paper(q21=flagged), teacher_sheet())
    assert result["short"]["agree"] == 14 and result["short"]["flagged"] == 1
    assert result["short"]["flagged_drafts"] == {"with_draft": 1, "within_0_5": 1, "within_0_5_pct": 100.0}
    assert result["short"]["agreement_pct_of_all"] == round(100 * 14 / 15, 1)


def test_flagging_everything_cannot_look_good():
    flagged = {"routing": "MANUAL", "local_routing": "MANUAL", "flag_reason": "X"}
    p = paper(**{f"q{q}": flagged for q in range(1, 36)})
    result = score(p, teacher_sheet())
    assert result["silent_wrong"] == []
    assert result["flagged_overall_pct"] == 100.0
    assert result["short"]["agreement_pct_of_all"] == 0.0
    assert result["short"]["agreement_pct_of_unflagged"] is None


def test_unusable_teacher_cells_are_left_out_and_counted():
    sheet = teacher_sheet(mcq={1: None}, short={21: None})
    blank = {"routing": "MANUAL", "local_routing": "MANUAL", "flag_reason": "BLANK_ANSWER"}
    result = score(paper(q1={"selected_option": "B"}, q21=blank), sheet)
    assert result["mcq"]["total"] == 19 and result["mcq"]["unusable_teacher_cell"] == 1
    assert result["mcq"]["silent_wrong"] == 0
    assert result["short"]["total"] == 14
    assert result["short"]["no_teacher_mark_and_app_found_no_answer"] == 1


def test_app_marking_where_teacher_gave_nothing_is_visible():
    result = score(paper(), teacher_sheet(short={21: None}))
    assert result["short"]["no_teacher_mark_but_app_marked"] == 1




def test_mcq_totals_compare_under_both_keys():
    sheet = teacher_sheet(mcq_mark=20.0)
    result = score(paper(), sheet)
    gaps = result["mcq"]["sheet_totals_vs_teacher"]
    assert gaps["as_written"] == {"sheets": 1, "exact": 1, "within_1": 1}
    assert gaps["teacher_effective"] == {"sheets": 1, "exact": 0, "within_1": 1}  # Q1 becomes D


def test_teacher_file_parsing(tmp_path):
    header = "Student_ID,Student_Name,MCQ mark,2 marks,total," + ",".join(str(q) for q in range(1, 36))
    cells = ["A", "-", "TRUE", "", "2"] + ["B"] * 15 + ["2", "1.5", "0", "", "NC", "NM", "4.0"] + ["1.0"] * 8
    path = tmp_path / "marks.csv"
    path.write_text(f"{header}\nStudent_1,Anon,5,1,6,{','.join(cells)}\n")
    sheet = load_teacher_marks(path)["Student_1"]
    assert [sheet.mcq[q] for q in range(1, 6)] == ["A", None, None, None, None]
    assert [sheet.short[q] for q in range(21, 28)] == [2.0, 1.5, 0.0, None, None, None, None]




def test_constant_mark_baseline_shows_a_lenient_window():
    sheet = teacher_sheet(short={21: 0.0, 22: 1.0})  # the other 13 are 2.0
    baseline = score(paper(), sheet)["constant_mark_baseline"]
    assert baseline == {"1.5": round(100 * 14 / 15, 1), "2.0": round(100 * 13 / 15, 1)}


def test_report_renders():
    result = score(paper(q3={"selected_option": "B"}), teacher_sheet())
    text = render_report("Title", {"Split": "tune"}, result, result)
    assert "Silent wrong marks" in text and "Student_1" in text and "Q3" in text
    assert "mean absolute error" in text and "Earlier local models" in text


# ---------- split and guards ----------

SPLIT = Path(script.EVALUATION) / "split.json"


def test_split_is_10_and_40_with_no_overlap():
    split = load_split(SPLIT)
    assert len(split["tune"]) == 10 and len(split["report"]) == 40
    assert not set(split["tune"]) & set(split["report"])
    assert sorted(split["tune"] + split["report"]) == sorted(ALL_STUDENTS)


def test_report_split_needs_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(script, "FINAL_LOCK", tmp_path / "lock.json")
    with pytest.raises(SystemExit, match="confirm-final"):
        script.main(["--split", "report", "--name", "x"])
    assert not (tmp_path / "lock.json").exists()




def test_report_split_refuses_uncommitted_code(tmp_path, monkeypatch):
    monkeypatch.setattr(script, "FINAL_LOCK", tmp_path / "lock.json")
    monkeypatch.setattr(script, "git", lambda *a: " M backend/pipeline.py" if a[0] == "status" else "abc")
    with pytest.raises(SystemExit, match="commit"):
        script.main(["--split", "report", "--name", "x", "--confirm-final"])


def test_second_final_run_needs_a_stated_reason(tmp_path, monkeypatch):
    lock = tmp_path / "lock.json"
    lock.write_text(json.dumps({"commit": "old", "name": "final", "reasons": []}))
    monkeypatch.setattr(script, "FINAL_LOCK", lock)
    monkeypatch.setattr(script, "git", lambda *a: "" if a[0] == "status" else "new")
    args = script.parse_args(["--split", "report", "--name", "final", "--confirm-final"])
    with pytest.raises(SystemExit, match="rerun-reason"):
        script.guard_report_split(args)
    args = script.parse_args(["--split", "report", "--name", "final", "--confirm-final", "--rerun-reason", "crash"])
    assert script.guard_report_split(args)["reasons"] == ["crash"]




def test_real_firestore_host_is_refused(monkeypatch):
    monkeypatch.setenv("FIRESTORE_EMULATOR_HOST", "firestore.googleapis.com:443")
    with pytest.raises(SystemExit, match="not a local"):
        script.set_environment()


def test_grading_code_never_opens_corrected_sheets_or_teacher_marks():
    source = Path(script.__file__).read_text()
    tree = ast.parse(source)
    strings = {node.value for node in ast.walk(tree)
               if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) < 60}
    assert "Student_Pdf" in strings and "Corrected_Pdf" not in strings
    for path in ("services/gemini_sheet.py", "services/gemini_paper.py", "services/gemini_client.py", "routes/upload.py"):
        text = (Path(script.BACKEND) / path).read_text()
        assert "Corrected_Pdf" not in text and "Teacher_manual_marks" not in text
    # Teacher marks are loaded only after the grading loop.
    assert source.index("for index, student in enumerate(students") < source.index('load_teacher_marks(DATA')


# ---------- the numbers the Gemini report adds ----------

def test_exact_mark_and_mean_absolute_error():
    p = paper(q21={"final_score": 1.5}, q22={"final_score": 0.0}, q23={"routing": "FLAGGED", "suggested_score": 1.0, "flag_reason": "LOW_CONFIDENCE"})
    short = score(p, teacher_sheet())["short"]
    assert short["unflagged"] == 14 and short["exact"] == 12
    assert short["exact_pct_of_all"] == 80.0
    # Unflagged: 12 at 0 off, one 0.5 off, one 2 off.
    assert short["mae_unflagged"] == round(2.5 / 14, 3)
    assert short["silent_wrong"] == 1 and short["silent_wrong_any_difference"] == 2
    assert short["mae_flagged_drafts"] == 1.0


def test_most_common_mark_baseline_uses_all_and_same_answers():
    base = most_common_mark_baseline([(2.0, 1.0), (2.0, 2.0)], [2.0, 2.0, 2.0, 1.0])
    assert base["mark"] == 2.0
    assert base["exact_pct_all"] == 75.0 and base["mae_all"] == 0.25
    assert base["exact_pct_unflagged"] == 50.0 and base["mae_unflagged"] == 0.5


def test_flagged_percent_counts_an_answer_once_and_can_leave_out_blanks():
    p = paper(
        q21={"routing": "MANUAL", "flag_reason": "MISSING"},
        q22={"routing": "FLAGGED", "flag_reason": "LOW_CONFIDENCE,EVIDENCE_NOT_FOUND"},
    )
    result = score(p, teacher_sheet())
    assert result["flagged_overall_pct"] == round(100 * 2 / 35, 1)
    assert result["flagged_excluding_blanks_pct"] == round(100 / 35, 1)
    assert result["flag_reasons"]["EVIDENCE_NOT_FOUND"] == 1


def test_every_proposed_mark_counts_flagged_drafts_too():
    p = paper(q21={"routing": "FLAGGED", "suggested_score": 1.0, "flag_reason": "PARTIAL_HIGH_VALUE"})
    short = score(p, teacher_sheet())["short"]
    assert short["proposed"] == 15
    assert short["exact_pct_of_proposed"] == round(100 * 14 / 15, 1)
    assert short["mae_proposed"] == round(1 / 15, 3)
