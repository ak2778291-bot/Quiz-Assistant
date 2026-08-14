"""Per-topic proficiency — the adaptive-logic demo surface."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.config import settings
from app.deps import CurrentUser, SessionDep
from app.models import ContentChunk, Proficiency, Subject
from app.schemas import ProficiencyListOut, ProficiencyOut
from app.services import proficiency as prof

router = APIRouter(prefix="/users", tags=["proficiency"])

FORMULA = (
    f"score <- score + {prof.ALPHA} * (target - score), starting at {prof.INITIAL_SCORE}; "
    "target = 0.60/0.80/1.00 for a correct easy/medium/hard answer and "
    "0.00/0.20/0.40 for an incorrect one. Band: <0.40 easy, <0.70 medium, "
    "else hard."
)


@router.get("/{user_id}/proficiency", response_model=ProficiencyListOut)
async def get_proficiency(
    user_id: int, user: CurrentUser, session: SessionDep
) -> ProficiencyListOut:
    if user_id != user.id:
        raise HTTPException(status_code=403, detail="You can only read your own proficiency")

    rows = (
        await session.execute(
            select(Proficiency, Subject)
            .join(Subject, Subject.id == Proficiency.subject_id)
            .where(Proficiency.user_id == user_id)
            .order_by(Subject.name, Subject.grade, Proficiency.topic)
        )
    ).all()

    chapters = dict(
        (
            await session.execute(
                select(ContentChunk.topic, ContentChunk.chapter).distinct()
            )
        ).all()
    )

    topics = [
        ProficiencyOut(
            subject_id=subject.id,
            subject=subject.name,
            grade=subject.grade,
            topic=row.topic,
            chapter=chapters.get(row.topic, row.topic),
            score=float(row.score),
            band=prof.score_to_band(float(row.score)),
            answers_count=row.answers_count,
            next_quiz_mix=prof.difficulty_mix(
                float(row.score), settings.questions_per_quiz
            ),
            updated_at=row.updated_at,
        )
        for row, subject in rows
    ]

    return ProficiencyListOut(
        user_id=user_id,
        formula=FORMULA,
        alpha=prof.ALPHA,
        initial_score=prof.INITIAL_SCORE,
        topics=topics,
    )
