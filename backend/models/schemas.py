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


class QuestionGrade(BaseModel):
    """Complete grading result for one question."""
    question_number: int
    question_type: str = "THEORY"  # THEORY | NUMERICAL | DIAGRAM
    max_marks: float = 0.0
    extracted_text: str = ""
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
    illegible_regions: list[str] = Field(default_factory=list)


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
    rubric: list[QuestionRubric]


class StudentMeta(BaseModel):
    """Optional student metadata for bulk upload."""
    hall_ticket: str
    name: str = ""


class ReviewConfirmRequest(BaseModel):
    """Request body for POST /review/confirm."""
    exam_id: str
    hall_ticket: str
    question_number: int


class ReviewEditRequest(BaseModel):
    """Request body for POST /review/edit."""
    exam_id: str
    hall_ticket: str
    question_number: int
    new_score: float
    edit_reason: str = ""


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


class MathResult(BaseModel):
    """Output from the Math Agent."""
    c_math: float = 1.0
    symbolic_correct: bool | None = None
    latex_extracted: str = ""
