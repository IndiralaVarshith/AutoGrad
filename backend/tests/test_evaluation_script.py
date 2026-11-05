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
    score_reading,
    score_run,
    view,
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


def test_local_view_ignores_gemini_clearing_a_flag():
    cleared = {"routing": "AUTO_ACCEPT", "local_routing": "FLAGGED", "flag_reason": ""}
    p = paper(q21={**cleared, "final_score": 2.0})
    assert score(p, teacher_sheet(), local_only=True)["short"]["flagged"] == 1
    assert score(p, teacher_sheet(), local_only=False)["short"]["flagged"] == 0
    assert view(p["questions"][20], local_only=True) == (True, None)


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


def test_reading_score():
    read = {"answers": {"1": "A", "2": "(a)", "3": "zz", "21": "text"}, "unmapped": 1, "seconds": 10}
    sheet = teacher_sheet(mcq={4: None}, short={22: None})
    result = score_reading({"Student_1": read}, {"Student_1": sheet})
    assert result["mcq_correct"] == 2 and result["mcq_total"] == 19
    assert result["short_marked_with_text"] == 1 and result["unmapped"] == 1


def test_report_renders():
    result = score(paper(q3={"selected_option": "B"}), teacher_sheet())
    text = render_report("Title", {"Split": "tune"}, result, None)
    assert "Silent wrong marks" in text and "Student_1" in text and "Q3" in text


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


def test_report_split_refuses_limit_and_read_only(tmp_path, monkeypatch):
    monkeypatch.setattr(script, "FINAL_LOCK", tmp_path / "lock.json")
    with pytest.raises(SystemExit, match="limit"):
        script.main(["--split", "report", "--name", "x", "--confirm-final", "--limit", "2"])
    with pytest.raises(SystemExit, match="tuning"):
        script.main(["--split", "report", "--name", "x", "--confirm-final", "--read-only"])


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
    args = script.parse_args(["--split", "tune", "--name", "x"])
    with pytest.raises(SystemExit, match="not a local"):
        script.set_environment(args)


def test_grading_code_never_opens_corrected_sheets_or_teacher_marks():
    source = Path(script.__file__).read_text()
    tree = ast.parse(source)
    strings = {node.value for node in ast.walk(tree)
               if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) < 60}
    assert "Student_Pdf" in strings and "Corrected_Pdf" not in strings
    for path in ("pipeline.py", "services/ocr.py", "services/grading_agent.py", "services/critic_agent.py"):
        text = (Path(script.BACKEND) / path).read_text()
        assert "Corrected_Pdf" not in text and "Teacher_manual_marks" not in text
    # Teacher marks are loaded only after the grading loop.
    assert source.index("for index, student in enumerate(students") < source.index('load_teacher_marks(DATA')
