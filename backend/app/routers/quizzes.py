"""Quiz creation, answering and results."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.deps import CurrentUser, SessionDep
from app.errors import ContentNotAvailableError, LLMUnavailableError, UngroundedGenerationError
from app.models import Attempt, ContentChunk, Quiz, Subject
from app.schemas import (
    AdaptiveInfo,
    AnswerRequest,
    AnswerResponse,
    CreateQuizRequest,
    QuestionOut,
    QuizOut,
    QuizResultOut,
    ResultQuestion,
    SourceChunkOut,
)
from app.services import cache as cache_service
from app.services import proficiency as prof
from app.services.quiz_service import create_quiz, get_or_create_proficiency, submit_answer

router = APIRouter(prefix="/quizzes", tags=["quizzes"])


async def _load_quiz(session, quiz_id: int, user_id: int) -> Quiz:
    quiz = (
        await session.execute(
            select(Quiz)
            .options(selectinload(Quiz.questions))
            .where(Quiz.id == quiz_id)
        )
    ).scalar_one_or_none()
    if quiz is None:
        raise HTTPException(status_code=404, detail="Quiz not found")
    if quiz.user_id != user_id:
        # 404 rather than 403: don't confirm the existence of other users' quizzes.
        raise HTTPException(status_code=404, detail="Quiz not found")
    return quiz


async def _subject(session, subject_id: int) -> Subject:
    return (
        await session.execute(select(Subject).where(Subject.id == subject_id))
    ).scalar_one()


async def _chapter_for(session, subject_id: int, topic: str) -> str:
    row = (
        await session.execute(
            select(ContentChunk.chapter)
            .where(ContentChunk.subject_id == subject_id, ContentChunk.topic == topic)
            .limit(1)
        )
    ).scalar_one_or_none()
    return row or topic


@router.post("", response_model=QuizOut, status_code=status.HTTP_201_CREATED)
async def post_quiz(
    payload: CreateQuizRequest, user: CurrentUser, session: SessionDep
) -> QuizOut:
    existing = await get_or_create_proficiency(
        session, user_id=user.id, subject_id=payload.subject_id, topic=payload.topic
    )
    is_first = existing.answers_count == 0
    await session.commit()

    try:
        quiz, result = await create_quiz(
            session,
            user_id=user.id,
            subject_id=payload.subject_id,
            topic=payload.topic,
            n_questions=payload.n_questions,
        )
    except ContentNotAvailableError as exc:
        # 422, not a fallback: refusing to generate is the correct behaviour.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except UngroundedGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc
    except LLMUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    subject = await _subject(session, quiz.subject_id)
    score = float(quiz.proficiency_at_creation)
    n = len(quiz.questions) or (payload.n_questions or settings.questions_per_quiz)

    return QuizOut(
        id=quiz.id,
        subject_id=subject.id,
        subject=subject.name,
        grade=subject.grade,
        topic=quiz.topic,
        chapter=await _chapter_for(session, subject.id, quiz.topic),
        difficulty=quiz.difficulty,
        created_at=quiz.created_at,
        adaptive=AdaptiveInfo(
            proficiency_score=score,
            band=quiz.difficulty,
            mix=prof.difficulty_mix(score, n),
            rule=prof.explain(score, n)["rule"],
            is_first_quiz_on_topic=is_first,
        ),
        generation_provider=result.provider,
        served_from_cache=result.cached,
        questions=[
            QuestionOut(
                id=q.id,
                position=q.position,
                question_text=q.question_text,
                options=q.options,
                difficulty=q.difficulty,
                source_chunk_ids=q.source_chunk_ids,
            )
            for q in quiz.questions
        ],
    )


@router.get("/{quiz_id}", response_model=QuizOut)
async def get_quiz(quiz_id: int, user: CurrentUser, session: SessionDep) -> QuizOut:
    """Student-facing view of an existing quiz — no answer key.

    Reloading a quiz must not regenerate it: that would burn an LLM call and
    silently swap the questions the proficiency score was computed against.
    """
    quiz = await _load_quiz(session, quiz_id, user.id)
    subject = await _subject(session, quiz.subject_id)

    answered = (
        await session.execute(
            select(Attempt.question_id).where(
                Attempt.user_id == user.id,
                Attempt.question_id.in_([q.id for q in quiz.questions]),
            )
        )
    ).scalars().all()

    score = float(quiz.proficiency_at_creation)
    n = len(quiz.questions) or settings.questions_per_quiz
    return QuizOut(
        id=quiz.id,
        subject_id=subject.id,
        subject=subject.name,
        grade=subject.grade,
        topic=quiz.topic,
        chapter=await _chapter_for(session, subject.id, quiz.topic),
        difficulty=quiz.difficulty,
        created_at=quiz.created_at,
        adaptive=AdaptiveInfo(
            proficiency_score=score,
            band=quiz.difficulty,
            mix=prof.difficulty_mix(score, n),
            rule=prof.explain(score, n)["rule"],
            is_first_quiz_on_topic=False,
        ),
        generation_provider="persisted",
        served_from_cache=False,
        questions=[
            QuestionOut(
                id=q.id,
                position=q.position,
                question_text=q.question_text,
                options=q.options,
                difficulty=q.difficulty,
                source_chunk_ids=q.source_chunk_ids,
            )
            for q in quiz.questions
        ],
        answered_question_ids=sorted(answered),
    )


@router.post("/{quiz_id}/answer", response_model=AnswerResponse)
async def post_answer(
    quiz_id: int, payload: AnswerRequest, user: CurrentUser, session: SessionDep
) -> AnswerResponse:
    quiz = await _load_quiz(session, quiz_id, user.id)
    question = next((q for q in quiz.questions if q.id == payload.question_id), None)
    if question is None:
        raise HTTPException(status_code=404, detail="Question not part of this quiz")

    attempt, proficiency_row, counted = await submit_answer(
        session,
        user_id=user.id,
        quiz=quiz,
        question=question,
        selected_answer=payload.selected_answer,
    )
    score = float(proficiency_row.score)
    return AnswerResponse(
        question_id=question.id,
        selected_answer=attempt.selected_answer,
        correct_answer=question.correct_answer,
        is_correct=attempt.is_correct,
        explanation=question.explanation,
        source_chunk_ids=question.source_chunk_ids,
        proficiency_score=score,
        proficiency_band=prof.score_to_band(score),
        answers_count=proficiency_row.answers_count,
        counted=counted,
    )


@router.get("/{quiz_id}/results", response_model=QuizResultOut)
async def get_results(
    quiz_id: int, user: CurrentUser, session: SessionDep
) -> QuizResultOut:
    quiz = await _load_quiz(session, quiz_id, user.id)
    subject = await _subject(session, quiz.subject_id)

    question_ids = [q.id for q in quiz.questions]
    attempts = (
        await session.execute(
            select(Attempt).where(
                Attempt.user_id == user.id, Attempt.question_id.in_(question_ids)
            )
        )
    ).scalars().all()
    by_question = {a.question_id: a for a in attempts}

    chunk_ids = sorted({cid for q in quiz.questions for cid in q.source_chunk_ids})
    chunks = (
        await session.execute(
            select(ContentChunk).where(ContentChunk.id.in_(chunk_ids))
        )
    ).scalars().all()

    proficiency_row = await get_or_create_proficiency(
        session, user_id=user.id, subject_id=quiz.subject_id, topic=quiz.topic
    )
    await session.commit()

    score_before = float(quiz.proficiency_at_creation)
    score_after = float(proficiency_row.score)

    return QuizResultOut(
        quiz_id=quiz.id,
        subject=subject.name,
        grade=subject.grade,
        topic=quiz.topic,
        chapter=await _chapter_for(session, subject.id, quiz.topic),
        difficulty=quiz.difficulty,
        answered=len(attempts),
        total=len(quiz.questions),
        correct=sum(1 for a in attempts if a.is_correct),
        score_before=score_before,
        score_after=score_after,
        band_before=prof.score_to_band(score_before),
        band_after=prof.score_to_band(score_after),
        next_quiz_mix=prof.difficulty_mix(score_after, settings.questions_per_quiz),
        questions=[
            ResultQuestion(
                id=q.id,
                position=q.position,
                question_text=q.question_text,
                options=q.options,
                correct_answer=(
                    q.correct_answer if q.id in by_question else None
                ),
                selected_answer=(
                    by_question[q.id].selected_answer if q.id in by_question else None
                ),
                is_correct=(
                    by_question[q.id].is_correct if q.id in by_question else None
                ),
                difficulty=q.difficulty,
                explanation=q.explanation,
                source_chunk_ids=q.source_chunk_ids,
            )
            for q in quiz.questions
        ],
        sources=[
            SourceChunkOut(
                id=c.id,
                chapter=c.chapter,
                chunk_index=c.chunk_index,
                content=c.content,
            )
            for c in sorted(chunks, key=lambda c: (c.chapter, c.chunk_index))
        ],
    )


cache_router = APIRouter(tags=["ops"])


@cache_router.get("/cache/stats")
async def get_cache_stats(session: SessionDep) -> dict:
    """Measured cache hit rate. The number on the resume comes from here."""
    return await cache_service.stats(session)
