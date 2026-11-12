"""Sheets marked before negative rows were removed are shown without them."""

from routes.results import _without_negative_rows


def row(marks, criterion="Point", rationale=""):
    return {"criterion": criterion, "marks_awarded": marks, "rationale": rationale}


def test_old_taken_off_rows_are_folded_into_the_points_and_the_rows_still_add_up():
    saved = {"questions": [
        {"final_score": 1.0, "criteria": [row(1), row(1), row(-0.5, "Taken off: Spelling"), row(-0.5, "Taken off: Wrong date")]},
        {"final_score": 0.0, "criteria": [row(0), row(-1, "Taken off: Incorrect condition")]},
        {"final_score": 2.0, "criteria": [row(2)]},
    ]}
    shown = _without_negative_rows(saved)["questions"]
    assert [c["marks_awarded"] for c in shown[0]["criteria"]] == [1, 0]
    assert "Spelling" in shown[0]["criteria"][1]["rationale"] and "Wrong date" in shown[0]["criteria"][1]["rationale"]
    assert [c["marks_awarded"] for c in shown[1]["criteria"]] == [0]
    assert shown[2]["criteria"] == [row(2)]
    for question in shown:
        assert all(c["marks_awarded"] >= 0 for c in question["criteria"])
        assert sum(c["marks_awarded"] for c in question["criteria"]) == question["final_score"]


def test_a_missing_result_or_one_without_rows_is_left_alone():
    assert _without_negative_rows(None) is None
    assert _without_negative_rows({"questions": [{"criteria": []}]}) == {"questions": [{"criteria": []}]}
