"""Pydantic request/response models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# --- Auth -------------------------------------------------------------------
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# --- Catalogue --------------------------------------------------------------
class TopicOut(BaseModel):
    topic: str
    chapter: str
    chunk_count: int


class SubjectOut(BaseModel):
    id: int
    name: str
    grade: int
    topics: list[TopicOut]


# --- Quizzes ----------------------------------------------------------------
class CreateQuizRequest(BaseModel):
    subject_id: int
    topic: str
    n_questions: int | None = Field(default=None, ge=1, le=20)


class QuestionOut(BaseModel):
    """Student-facing question: no answer key."""

    id: int
    position: int
    question_text: str
    options: dict[str, str]
    difficulty: str
    source_chunk_ids: list[int]


class AdaptiveInfo(BaseModel):
    proficiency_score: float
    band: str
    mix: dict[str, int]
    rule: str
    is_first_quiz_on_topic: bool


class QuizOut(BaseModel):
    id: int
    subject_id: int
    subject: str
    grade: int
    topic: str
    chapter: str
    difficulty: str
    created_at: datetime
    adaptive: AdaptiveInfo
    generation_provider: str
    served_from_cache: bool
    questions: list[QuestionOut]
    answered_question_ids: list[int] = Field(
        default_factory=list,
        description="Lets a refreshed quiz resume instead of being regenerated.",
    )


class AnswerRequest(BaseModel):
    question_id: int
    selected_answer: str = Field(pattern="^[A-Da-d]$")


class AnswerResponse(BaseModel):
    question_id: int
    selected_answer: str
    correct_answer: str
    is_correct: bool
    explanation: str
    source_chunk_ids: list[int]
    proficiency_score: float
    proficiency_band: str
    answers_count: int
    counted: bool = Field(
        description="False if this question was already answered; the score was not changed again."
    )


class ResultQuestion(BaseModel):
    id: int
    position: int
    question_text: str
    options: dict[str, str]
    #: Null until the student has answered this question — the results endpoint
    #: must not double as a way to read the answer key mid-quiz.
    correct_answer: str | None
    selected_answer: str | None
    is_correct: bool | None
    difficulty: str
    explanation: str
    source_chunk_ids: list[int]


class SourceChunkOut(BaseModel):
    id: int
    chapter: str
    chunk_index: int
    content: str


class QuizResultOut(BaseModel):
    quiz_id: int
    subject: str
    grade: int
    topic: str
    chapter: str
    difficulty: str
    answered: int
    total: int
    correct: int
    score_before: float
    score_after: float
    band_before: str
    band_after: str
    next_quiz_mix: dict[str, int]
    questions: list[ResultQuestion]
    sources: list[SourceChunkOut]


# --- Proficiency ------------------------------------------------------------
class ProficiencyOut(BaseModel):
    subject_id: int
    subject: str
    grade: int
    topic: str
    chapter: str
    score: float
    band: str
    answers_count: int
    next_quiz_mix: dict[str, int]
    updated_at: datetime


class ProficiencyListOut(BaseModel):
    user_id: int
    formula: str
    alpha: float
    initial_score: float
    topics: list[ProficiencyOut]


class ErrorOut(BaseModel):
    detail: str
    code: str
