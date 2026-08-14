"""Generation cache, keyed on (topic, difficulty mix, content hash).

Cheapest thing that works: a Postgres table. No Redis -- there is no
throughput problem here, and adding a second datastore for a table this size
would be complexity without a reason (spec Section 3).

The key deliberately excludes the user id: two students with the same
proficiency band on the same topic get the same question set, which is exactly
the repeat-request case worth saving. It includes the content hash of the
retrieved chunks, so re-ingesting changed curriculum text invalidates the
entry automatically.

`lookups`/`hits` are counted in `cache_stats` so the hit rate reported by
GET /cache/stats is a measured number rather than a claimed one.
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import CacheStat, GenerationCache
from app.services.generation import GeneratedQuestion, GenerationResult
from app.services.retrieval import RetrievedChunk

#: Bump when the prompt or validation contract changes, so stale entries from
#: an older prompt are never served.
PROMPT_VERSION = "v1"


def build_cache_key(
    *,
    subject_id: int,
    topic: str,
    difficulties: list[str],
    chunks: list[RetrievedChunk],
    provider: str,
) -> str:
    content_fingerprint = "|".join(
        f"{c.id}:{c.source_hash}" for c in sorted(chunks, key=lambda c: c.id)
    )
    material = json.dumps(
        {
            "v": PROMPT_VERSION,
            "subject_id": subject_id,
            "topic": topic,
            "difficulties": difficulties,
            "content": content_fingerprint,
            "provider": provider,
            "model": settings.gemini_model,
        },
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _serialize(questions: list[GeneratedQuestion]) -> dict:
    return {
        "questions": [
            {
                "question_text": q.question_text,
                "options": q.options,
                "correct_answer": q.correct_answer,
                "difficulty": q.difficulty,
                "explanation": q.explanation,
                "source_chunk_ids": q.source_chunk_ids,
            }
            for q in questions
        ]
    }


def _deserialize(payload: dict) -> list[GeneratedQuestion]:
    return [GeneratedQuestion(**item) for item in payload["questions"]]


async def _bump_stats(session: AsyncSession, *, hit: bool) -> None:
    stmt = (
        pg_insert(CacheStat)
        .values(id=1, lookups=1, hits=1 if hit else 0)
        .on_conflict_do_update(
            index_elements=[CacheStat.id],
            set_={
                "lookups": CacheStat.lookups + 1,
                "hits": CacheStat.hits + (1 if hit else 0),
            },
        )
    )
    await session.execute(stmt)


async def get_cached(session: AsyncSession, cache_key: str) -> GenerationResult | None:
    if not settings.enable_generation_cache:
        return None

    row = (
        await session.execute(
            select(GenerationCache).where(GenerationCache.cache_key == cache_key)
        )
    ).scalar_one_or_none()

    await _bump_stats(session, hit=row is not None)
    if row is None:
        return None

    await session.execute(
        update(GenerationCache)
        .where(GenerationCache.id == row.id)
        .values(hits=GenerationCache.hits + 1)
    )
    return GenerationResult(
        questions=_deserialize(row.payload), provider="cache", cached=True
    )


async def store(
    session: AsyncSession,
    *,
    cache_key: str,
    topic: str,
    difficulty: str,
    questions: list[GeneratedQuestion],
) -> None:
    if not settings.enable_generation_cache:
        return
    stmt = (
        pg_insert(GenerationCache)
        .values(
            cache_key=cache_key,
            topic=topic,
            difficulty=difficulty,
            payload=_serialize(questions),
            hits=0,
        )
        .on_conflict_do_nothing(index_elements=[GenerationCache.cache_key])
    )
    await session.execute(stmt)


async def stats(session: AsyncSession) -> dict:
    row = (
        await session.execute(select(CacheStat).where(CacheStat.id == 1))
    ).scalar_one_or_none()
    lookups = row.lookups if row else 0
    hits = row.hits if row else 0
    entries = (
        await session.execute(select(GenerationCache))
    ).scalars().all()
    return {
        "enabled": settings.enable_generation_cache,
        "lookups": lookups,
        "hits": hits,
        "misses": lookups - hits,
        "hit_rate": round(hits / lookups, 4) if lookups else None,
        "cached_question_sets": len(entries),
        "llm_calls_avoided": hits,
    }
