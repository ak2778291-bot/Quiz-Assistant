"""Quiz orchestration: retrieval -> adaptive mix -> generation -> persistence.

This module is where spec 4.1 (grounded generation) and 4.2 (adaptive
selection) meet. The order of operations matters and is asserted by the
integration test:

  1. Read the student's current proficiency for (subject, topic) -- a read,
     never a write, so a request that is about to be refused leaves no trace.
  2. Turn that score into a difficulty mix -- deterministically, before any
     model call.
  3. Refuse outright if the topic has too little ingested content.
  4. Retrieve, then generate (or serve from cache), then re-validate grounding.
  5. Persist the quiz with the score that produced it, creating the proficiency
     row only now -- once every path that could still refuse has passed.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.errors import ContentNotAvailableError
from app.models import Attempt, Proficiency, Question, Quiz, Subject
from app.services import cache as cache_service
from app.services import proficiency as prof
from app.services.generation import GenerationResult, generate_questions
from app.services.llm import LLMClient
from app.services.retrieval import count_chunks, retrieve


async def read_proficiency(
    session: AsyncSession, *, user_id: int, subject_id: int, topic: str
) -> Proficiency | None:
    """Look up a proficiency row without creating one.

    Callers that only need the *score* must use this rather than
    `get_or_create_proficiency`: creating the row first means a request that is
    subsequently refused (an unknown topic, a topic with no ingested content)
    still leaves a phantom row behind, and the student's proficiency page then
    lists a topic they can never be quizzed on.
    """
    return (
        await session.execute(
            select(Proficiency).where(
                Proficiency.user_id == user_id,
                Proficiency.subject_id == subject_id,
                Proficiency.topic == topic,
            )
        )
    ).scalar_one_or_none()


async def get_or_create_proficiency(
    session: AsyncSession, *, user_id: int, subject_id: int, topic: str
) -> Proficiency:
    row = await read_proficiency(
        session, user_id=user_id, subject_id=subject_id, topic=topic
    )
    if row is None:
        row = Proficiency(
            user_id=user_id,
            subject_id=subject_id,
            topic=topic,
            score=prof.INITIAL_SCORE,
            answers_count=0,
        )
        session.add(row)
        await session.flush()
    return row


async def create_quiz(
    session: AsyncSession,
    *,
    user_id: int,
    subject_id: int,
    topic: str,
    n_questions: int | None = None,
    llm_client: LLMClient | None = None,
) -> tuple[Quiz, GenerationResult]:
    n_questions = n_questions or settings.questions_per_quiz

    subject = (
        await session.execute(select(Subject).where(Subject.id == subject_id))
    ).scalar_one_or_none()
    if subject is None:
        raise ContentNotAvailableError(topic=topic, found=0, required=1)

    # (1) + (2): the adaptive decision happens before anything expensive.
    # This is a *read*: the row is only created at step (5), after every path
    # that could still refuse the request has passed.
    existing_proficiency = await read_proficiency(
        session, user_id=user_id, subject_id=subject_id, topic=topic
    )
    score = (
        float(existing_proficiency.score)
        if existing_proficiency is not None
        else prof.INITIAL_SCORE
    )
    difficulties = prof.difficulty_sequence(score, n_questions)
    band = prof.score_to_band(score)

    # (3): no content -> refuse, do not call the model.
    available = await count_chunks(session, subject_id, topic)
    if available < settings.min_chunks_for_generation:
        raise ContentNotAvailableError(
            topic=topic,
            found=available,
            required=settings.min_chunks_for_generation,
        )

    # (4): retrieve, then cache lookup, then generate.
    chunks = await retrieve(
        session,
        subject_id=subject_id,
        topic=topic,
        query=f"{subject.name} Grade {subject.grade}: {topic.replace('-', ' ')}",
    )
    if len(chunks) < settings.min_chunks_for_generation:
        raise ContentNotAvailableError(
            topic=topic, found=len(chunks), required=settings.min_chunks_for_generation
        )

    provider_name = llm_client.name if llm_client else settings.llm_provider
    cache_key = cache_service.build_cache_key(
        subject_id=subject_id,
        topic=topic,
        difficulties=difficulties,
        chunks=chunks,
        provider=provider_name,
    )
    result = await cache_service.get_cached(session, cache_key)
    if result is None:
        result = await generate_questions(
            chunks,
            subject=subject.name,
            grade=subject.grade,
            chapter=chunks[0].chapter,
            difficulties=difficulties,
            client=llm_client,
        )
        await cache_service.store(
            session,
            cache_key=cache_key,
            topic=topic,
            difficulty=band,
            questions=result.questions,
        )

    # (5): persist, freezing the score that drove the difficulty.
    # The proficiency row is created here and not earlier — everything above
    # can still raise (unknown subject, no ingested content, an ungrounded or
    # failed generation), and none of those requests should leave state behind.
    await get_or_create_proficiency(
        session, user_id=user_id, subject_id=subject_id, topic=topic
    )

    quiz = Quiz(
        user_id=user_id,
        subject_id=subject_id,
        topic=topic,
        difficulty=band,
        proficiency_at_creation=score,
    )
    session.add(quiz)
    await session.flush()

    for position, generated in enumerate(result.questions):
        session.add(
            Question(
                quiz_id=quiz.id,
                question_text=generated.question_text,
                options=generated.options,
                correct_answer=generated.correct_answer,
                source_chunk_ids=generated.source_chunk_ids,
                difficulty=generated.difficulty,
                explanation=generated.explanation,
                position=position,
            )
        )

    await session.commit()
    # `questions` is a lazy relationship that was never loaded — the Question
    # rows were added directly, not through the collection. Callers read
    # quiz.questions straight after this returns, and a lazy load there would
    # attempt sync IO inside the async context and raise MissingGreenlet.
    # Load it explicitly (ordered by Question.position via the relationship).
    await session.refresh(quiz, attribute_names=["questions"])
    return quiz, result


async def submit_answer(
    session: AsyncSession,
    *,
    user_id: int,
    quiz: Quiz,
    question: Question,
    selected_answer: str,
) -> tuple[Attempt, Proficiency, bool]:
    """Record one answer and apply the proficiency update.

    Re-answering a question is idempotent: the existing attempt is returned and
    proficiency is not double-counted (the unique constraint on
    (question_id, user_id) backs this up at the database level).
    """
    selected_answer = selected_answer.strip().upper()

    existing = (
        await session.execute(
            select(Attempt).where(
                Attempt.question_id == question.id, Attempt.user_id == user_id
            )
        )
    ).scalar_one_or_none()

    proficiency_row = await get_or_create_proficiency(
        session, user_id=user_id, subject_id=quiz.subject_id, topic=quiz.topic
    )

    if existing is not None:
        return existing, proficiency_row, False

    is_correct = selected_answer == question.correct_answer
    attempt = Attempt(
        question_id=question.id,
        user_id=user_id,
        selected_answer=selected_answer,
        is_correct=is_correct,
    )
    session.add(attempt)

    proficiency_row.score = prof.update_score(
        float(proficiency_row.score), question.difficulty, is_correct
    )
    proficiency_row.answers_count += 1

    await session.commit()
    await session.refresh(attempt)
    await session.refresh(proficiency_row)
    return attempt, proficiency_row, True
