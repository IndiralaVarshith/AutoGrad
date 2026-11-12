"""Score limits on teacher changes. No database."""

import pytest
from fastapi import HTTPException

import routes.review as review_module


def test_exam_total_limit_applies_to_teacher_score_changes():
    with pytest.raises(HTTPException) as raised:
        review_module._validated_total_score(
            {"max_total": 4.0},
            [{"final_score": 2.5, "max_marks": 3.0}, {"final_score": 2.0, "max_marks": 2.0}],
        )
    assert raised.value.status_code == 400


def test_a_question_score_outside_its_range_is_refused():
    with pytest.raises(HTTPException):
        review_module._validated_total_score({"max_total": 10.0}, [{"final_score": 3.0, "max_marks": 2.0}])


def test_a_sheet_is_final_only_when_nothing_is_flagged_and_the_teacher_approved():
    data = {"questions": [{"routing": "FLAGGED", "teacher_confirmed": False}], "teacher_approved": True}
    review_module._refresh_validation_status(data)
    assert data["status"] == "NEEDS_REVIEW" and data["finality_status"] == "PENDING_VALIDATION"
    data["questions"][0]["teacher_confirmed"] = True
    review_module._refresh_validation_status(data)
    assert data["status"] == "COMPLETE" and data["finality_status"] == "FINAL"
