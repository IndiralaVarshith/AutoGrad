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
    question_format: str = ""  # MCQ | SHORT_ANSWER | "" when not known
    options: list[dict[str, str]] = Field(default_factory=list)  # [{"label", "text"}]
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
    final_score: float = 0.0
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


class OCRRegion(BaseModel):
    """One OCR line with a normalized box on its original rendered page."""
    region_id: str
    page_number: int
    bbox: list[float] = Field(default_factory=list)  # normalized x0, y0, x1, y1
    extracted_text: str = ""
    question_number_candidate: int | None = None
    question_mapping_status: str = "UNRESOLVED"
    engine: str = ""
    model_version: str = ""
    engine_score: float | None = None
    uncertainty_class: str = "UNCALIBRATED"
    uncertainty_reason: str = ""
    validation_status: str = "PENDING"
    teacher_corrected_text: str | None = None
    teacher_question_number: int | None = None
    teacher_sub_part: str = ""
    validated_at: str = ""


class OCRPageResult(BaseModel):
    """Page-level OCR diagnostics; source page numbering is one-based."""
    page_number: int
    width: int = 0
    height: int = 0
    image_sha256: str = ""
    status: str = "UNAVAILABLE"  # PROCESSED | BLANK_CANDIDATE | UNREADABLE | UNAVAILABLE
    blank_candidate: bool = False
    quality_flags: list[str] = Field(default_factory=list)
    regions: list[OCRRegion] = Field(default_factory=list)
    error: str = ""
    validation_status: str = "PENDING"
    teacher_question_number: int | None = None
    teacher_sub_part: str = ""
    validated_at: str = ""


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
    ocr_pages: list[OCRPageResult] = Field(default_factory=list)
    unmapped_answers: list[dict] = Field(default_factory=list)


# =========================================================
# API REQUEST / RESPONSE SCHEMAS
# =========================================================

class ExamSetupRequest(BaseModel):
    """Request body for POST /exam/setup."""
    exam_id: str
    max_marks_per_question: dict[int, float] = Field(default_factory=dict)


class RubricApproveRequest(BaseModel):
    """Request body for POST /exam/rubric/approve."""
    exam_id: str
    teacher_confirmed: bool = False
    rubric: list[QuestionRubric]
    doc_fingerprint: str = ""
    gen_fingerprint: str = ""
    total_marks: float | None = None


class StudentMeta(BaseModel):
    """Optional student metadata for bulk upload."""
    hall_ticket: str
    name: str = ""


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


class OCRRegionReviewRequest(BaseModel):
    """Teacher validation for one page-level OCR region."""
    exam_id: str
    hall_ticket: str
    page_number: int
    region_id: str
    action: str  # ACCEPT | CORRECT | UNREADABLE
    corrected_text: str = ""
    question_number: int | None = None
    sub_part: str = ""


class OCRPageReviewRequest(BaseModel):
    """Teacher disposition for a blank or unreadable page."""
    exam_id: str
    hall_ticket: str
    page_number: int
    action: str  # ACCEPT_BLANK | UNREADABLE
    question_number: int | None = None
    sub_part: str = ""


class OCRQuestionRegradeRequest(BaseModel):
    """Re-run existing grading agents on teacher-validated OCR text."""
    exam_id: str
    hall_ticket: str
    question_number: int
    sub_part: str = ""


# =========================================================
# INTERNAL PIPELINE SCHEMAS
# =========================================================

class OCRResult(BaseModel):
    """Output from the OCR module for one question."""
    question_number: int
    extracted_text: str = ""
    illegible_regions: list[str] = Field(default_factory=list)


class CriticVerdict(BaseModel):
    """Output from the Critic Agent."""
    verdict: str = "ACCEPT"  # ACCEPT | REJECT
    challenged_criteria: list[str] = Field(default_factory=list)
    suggested_score: float = 0.0
    reason: str = ""
    model_used: str = ""


class MathResult(BaseModel):
    """Output from the Math Agent."""
    c_math: float = 1.0
    symbolic_correct: bool | None = None
    latex_extracted: str = ""
