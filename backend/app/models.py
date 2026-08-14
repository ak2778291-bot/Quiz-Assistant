"""SQLAlchemy models.

Mirrors the schema in Section 5 of the build spec. Columns marked `# extra`
are additions to that starting schema; each one is justified in DESIGN.md.
"""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import settings
from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Subject(Base):
    __tablename__ = "subjects"
    __table_args__ = (UniqueConstraint("name", "grade", name="uq_subject_name_grade"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    grade: Mapped[int] = mapped_column(Integer)

    chunks: Mapped[list[ContentChunk]] = relationship(
        back_populates="subject", cascade="all, delete-orphan"
    )


class ContentChunk(Base):
    __tablename__ = "content_chunks"
    __table_args__ = (
        Index("ix_content_chunks_subject_topic", "subject_id", "topic"),
        UniqueConstraint(
            "subject_id", "topic", "chunk_index", name="uq_chunk_subject_topic_index"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE")
    )
    chapter: Mapped[str] = mapped_column(String(200))
    # extra: url/lookup-safe slug of `chapter`; the join key for retrieval and
    # for the per-topic proficiency row.
    topic: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(settings.embedding_dim))
    chunk_index: Mapped[int] = mapped_column(Integer)
    # extra: lets the ingestion script detect unchanged source files, and feeds
    # the generation cache key.
    source_hash: Mapped[str] = mapped_column(String(64))

    subject: Mapped[Subject] = relationship(back_populates="chunks")


class Quiz(Base):
    __tablename__ = "quizzes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE")
    )
    # extra: a quiz is always scoped to one topic (chapter); proficiency is
    # tracked per topic, so the quiz has to record which one it targeted.
    topic: Mapped[str] = mapped_column(String(200))
    difficulty: Mapped[str] = mapped_column(String(16))
    # extra: the proficiency score that produced this quiz's difficulty, frozen
    # at creation time. Makes the adaptive decision auditable after the fact.
    proficiency_at_creation: Mapped[float] = mapped_column(Numeric(6, 4))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    questions: Mapped[list[Question]] = relationship(
        back_populates="quiz",
        cascade="all, delete-orphan",
        order_by="Question.position",
    )


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    quiz_id: Mapped[int] = mapped_column(ForeignKey("quizzes.id", ondelete="CASCADE"))
    question_text: Mapped[str] = mapped_column(Text)
    options: Mapped[dict] = mapped_column(JSONB)
    correct_answer: Mapped[str] = mapped_column(String(8))
    # Grounding traceability (spec 4.1): every question points at the retrieved
    # chunks it was generated from.
    source_chunk_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer))
    # extra: per-question difficulty, because a quiz is a *distribution* of
    # difficulties derived from proficiency, not a single level.
    difficulty: Mapped[str] = mapped_column(String(16))
    explanation: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(Integer)

    quiz: Mapped[Quiz] = relationship(back_populates="questions")


class Attempt(Base):
    __tablename__ = "attempts"
    __table_args__ = (
        UniqueConstraint("question_id", "user_id", name="uq_attempt_question_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE")
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    selected_answer: Mapped[str] = mapped_column(String(8))
    is_correct: Mapped[bool] = mapped_column(Boolean)
    answered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Proficiency(Base):
    __tablename__ = "proficiency"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "subject_id", "topic", name="uq_proficiency_user_subject_topic"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE")
    )
    topic: Mapped[str] = mapped_column(String(200))
    score: Mapped[float] = mapped_column(Numeric(6, 4))
    # extra: how many answers went into the score. Surfaced in the UI so a
    # score of 0.50 after 0 answers is distinguishable from a real 0.50.
    answers_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class GenerationCache(Base):
    """Cache on (topic, difficulty-mix, content hash) — spec Section 2."""

    __tablename__ = "generation_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    topic: Mapped[str] = mapped_column(String(200))
    difficulty: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict] = mapped_column(JSONB)
    hits: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CacheStat(Base):
    """Single-row counter table so the cache hit-rate is a *measured* number."""

    __tablename__ = "cache_stats"

    id: Mapped[int] = mapped_column(primary_key=True)
    lookups: Mapped[int] = mapped_column(Integer, default=0)
    hits: Mapped[int] = mapped_column(Integer, default=0)
