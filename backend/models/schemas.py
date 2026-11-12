"""
AutoGrade — Pydantic v2 Schemas

All data models used across the grading pipeline.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# =========================================================
# RUBRIC SCHEMAS
# =========================================================

class RubricCriterion(BaseModel):
    """A single criterion within a question rubric."""
    description: str
    max_marks: float


class QuestionRubric(BaseModel):
    """Full rubric for one question."""
    question_number: int
    question_text: str
    max_marks: float
    criteria: list[RubricCriterion]
    question_type: str | None = None
    sub_part: str = ""
    choice_group: str = ""
    is_or_choice: bool = False
    section: str = ""
    section_attempt_count: int | None = None
    section_max_marks: float | None = None
    attempt_instruction: str = ""
    question_format: str = ""  # MCQ | SHORT_ANSWER | LONG_ANSWER | "" when not known
    options: list[dict[str, str]] = Field(default_factory=list)  # [{"label", "text"}]
    correct_option: str = ""  # MCQ only: the approved option label
    suggested_option: str = ""  # MCQ only: Gemini's suggestion, never used for marking
    marks_evidence: str = ""  # The printed text the marks came from
    choice_alternative: str = ""  # Side of an OR choice, when it has several parts
    has_figure: bool = False
    figure_page: int | None = None


# =========================================================
# GRADING RESULT SCHEMAS
# =========================================================

class CriterionResult(BaseModel):
    """Grading result for a single rubric criterion."""
    criterion: str
    verbatim_quote: str = ""
    marks_awarded: float = 0.0
    rationale: str = ""
    quote_verified: bool = False
    evidence_region_ids: list[str] = Field(default_factory=list)


class QuestionGrade(BaseModel):
    """Complete grading result for one question."""
    question_number: int
    sub_part: str = ""
    choice_group: str = ""
    question_type: str = "THEORY"  # THEORY | NUMERICAL | DIAGRAM
    max_marks: float = 0.0
    extracted_text: str = ""
    grading_input_text: str = ""
    teacher_corrected_text: str = ""
    ocr_region_ids: list[str] = Field(default_factory=list)
    source_page_references: list[dict] = Field(default_factory=list)
    regrade_required: bool = False
    grader_score: float = 0.0
    critic_score: float = 0.0
    final_score: float = 0.0  # counts toward the total; a flagged answer's is its suggestion
    suggested_score: float | None = None  # draft shown to the teacher when flagged
    selected_option: str = ""  # MCQ only: the letter read from the sheet
    blank: bool = False  # no answer was found on the sheet, so 0
    extra: bool = False  # answered beyond what the paper asks; a better answer counts instead
    flag_reason: str = ""  # why this answer needs review
    local_routing: str = ""  # routing before any Gemini check
    criteria: list[CriterionResult] = Field(default_factory=list)
    c_ocr: float = 0.0
    c_agree: float = 0.0
    c_evid: float = 0.0
    c_math: float = 0.0
    c_final: float = 0.0
    routing: str = "FLAGGED"  # AUTO_ACCEPT | FLAGGED | MANUAL
    ocr_confidence_class: str = "UNCALIBRATED"
    illegible_regions: list[str] = Field(default_factory=list)
    model_used: str = ""
    routing_reason: str = ""
    verification_status: str = "NOT_REQUESTED"
    independent_verification: dict | None = None


class PaperResult(BaseModel):
    """Full result for one student's answer sheet."""
    hall_ticket: str
    student_name: str = ""
    exam_id: str
    questions: list[QuestionGrade] = Field(default_factory=list)
    total_score: float = 0.0
    max_total: float = 0.0
    avg_confidence: float = 0.0
    flagged_count: int = 0
    status: str = "PROCESSING"  # PROCESSING | COMPLETE | NEEDS_REVIEW
    finality_status: str = "PENDING_VALIDATION"
    source_file_reference: str = ""
    rubric_version: str = ""
    unmapped_answers: list[dict] = Field(default_factory=list)
    page_readers: list[dict] = Field(default_factory=list)  # which model read each page
    sheet_problems: list[str] = Field(default_factory=list)
    source_sha256: str = ""  # which file these marks came from
    page_count: int = 0
    teacher_approved: bool = False  # only a teacher's approval makes a sheet final
    gemini_usage: dict = Field(default_factory=dict)  # calls, tokens, notional cost
    calibration_id: str = ""  # the teacher's calibration this sheet was marked to, if any
    calibrated_from: str = ""  # the hall ticket of that calibration sheet


# =========================================================
# API REQUEST / RESPONSE SCHEMAS
# =========================================================

class RubricApproveRequest(BaseModel):
    """Request body for POST /exam/rubric/approve."""
    exam_id: str
    teacher_confirmed: bool = False
    rubric: list[QuestionRubric]
    doc_fingerprint: str = ""
    gen_fingerprint: str = ""
    total_marks: float | None = None
    marking_style: str = ""  # GENEROUS | BALANCED | STRICT; empty uses the configured default
    marking_notes: str = ""  # the teacher's own marking rules, in plain words


class ReviewConfirmRequest(BaseModel):
    """Request body for POST /review/confirm."""
    exam_id: str
    hall_ticket: str
    question_number: int
    sub_part: str = ""


class ReviewEditRequest(BaseModel):
    """Request body for POST /review/edit."""
    exam_id: str
    hall_ticket: str
    question_number: int
    sub_part: str = ""
    new_score: float
    edit_reason: str = ""
    corrected_text: str | None = None  # the teacher's reading; Gemini's stays on record


class ReviewApproveRequest(BaseModel):
    """Request body for POST /review/approve."""
    exam_id: str
    hall_ticket: str
    unmatched_checked: bool = False  # the teacher looked at writing that fit no question
