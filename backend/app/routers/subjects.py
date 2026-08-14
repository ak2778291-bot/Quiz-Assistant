"""Curated catalogue of ingested subjects/grades/chapters."""

from __future__ import annotations

from fastapi import APIRouter

from app.deps import SessionDep
from app.schemas import SubjectOut, TopicOut
from app.services.retrieval import list_topics

router = APIRouter(tags=["catalogue"])


@router.get("/subjects", response_model=list[SubjectOut])
async def get_subjects(session: SessionDep) -> list[SubjectOut]:
    """Only subjects/topics that actually have ingested chunks are listed."""
    rows = await list_topics(session)

    grouped: dict[int, SubjectOut] = {}
    for row in rows:
        subject = grouped.get(row["subject_id"])
        if subject is None:
            subject = SubjectOut(
                id=row["subject_id"],
                name=row["subject"],
                grade=row["grade"],
                topics=[],
            )
            grouped[row["subject_id"]] = subject
        subject.topics.append(
            TopicOut(
                topic=row["topic"],
                chapter=row["chapter"],
                chunk_count=row["chunk_count"],
            )
        )

    return sorted(grouped.values(), key=lambda s: (s.name, s.grade))
