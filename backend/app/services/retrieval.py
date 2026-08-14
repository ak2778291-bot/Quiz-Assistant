"""Vector retrieval over the ingested curriculum corpus.

The topic filter is applied in SQL, *before* vector ranking, not as a
post-filter. That is deliberate: it makes it structurally impossible for a
chunk from another chapter to reach the generation prompt, so the grounding
guarantee in spec 4.1 does not depend on the embedding being good enough to
keep topics apart.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import ContentChunk, Subject
from app.services.embeddings import Embedder, get_embedder


@dataclass(frozen=True)
class RetrievedChunk:
    id: int
    subject_id: int
    chapter: str
    topic: str
    content: str
    chunk_index: int
    source_hash: str
    score: float  # cosine similarity in [-1, 1]; higher is closer


async def count_chunks(session: AsyncSession, subject_id: int, topic: str) -> int:
    stmt = select(func.count(ContentChunk.id)).where(
        ContentChunk.subject_id == subject_id, ContentChunk.topic == topic
    )
    return int((await session.execute(stmt)).scalar_one())


async def retrieve(
    session: AsyncSession,
    *,
    subject_id: int,
    topic: str,
    query: str,
    top_k: int | None = None,
    embedder: Embedder | None = None,
) -> list[RetrievedChunk]:
    """Top-k chunks for (subject, topic), ranked by cosine similarity to `query`."""
    top_k = top_k or settings.retrieval_top_k
    embedder = embedder or get_embedder()

    query_vector = await embedder.embed_one(query, is_query=True)
    distance = ContentChunk.embedding.cosine_distance(query_vector)

    stmt = (
        select(ContentChunk, distance.label("distance"))
        .where(
            ContentChunk.subject_id == subject_id,
            ContentChunk.topic == topic,
        )
        .order_by(distance)
        .limit(top_k)
    )
    rows = (await session.execute(stmt)).all()

    return [
        RetrievedChunk(
            id=chunk.id,
            subject_id=chunk.subject_id,
            chapter=chunk.chapter,
            topic=chunk.topic,
            content=chunk.content,
            chunk_index=chunk.chunk_index,
            source_hash=chunk.source_hash,
            score=round(1.0 - float(dist), 6),
        )
        for chunk, dist in rows
    ]


async def list_topics(session: AsyncSession) -> list[dict]:
    """Every (subject, grade, topic) that actually has ingested content.

    Driven off `content_chunks`, not a hand-maintained list, so the API can
    never advertise a topic that would fail the grounding check.
    """
    stmt = (
        select(
            Subject.id,
            Subject.name,
            Subject.grade,
            ContentChunk.topic,
            ContentChunk.chapter,
            func.count(ContentChunk.id).label("chunk_count"),
        )
        .join(ContentChunk, ContentChunk.subject_id == Subject.id)
        .group_by(Subject.id, Subject.name, Subject.grade, ContentChunk.topic, ContentChunk.chapter)
        .order_by(Subject.name, Subject.grade, ContentChunk.chapter)
    )
    rows = (await session.execute(stmt)).all()
    return [
        {
            "subject_id": subject_id,
            "subject": name,
            "grade": grade,
            "topic": topic,
            "chapter": chapter,
            "chunk_count": chunk_count,
        }
        for subject_id, name, grade, topic, chapter, chunk_count in rows
    ]
