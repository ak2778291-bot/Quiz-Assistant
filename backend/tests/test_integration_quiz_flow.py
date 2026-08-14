"""One full integration path (spec Section 8).

quiz -> answer -> proficiency update -> next quiz reflects the new difficulty,
plus the negative path: a topic with no ingested content must fail gracefully
instead of silently generating from general knowledge.

Requires Postgres with pgvector. Skipped unless TEST_DATABASE_URL is set;
CI provides one via a pgvector service container.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.db import SessionLocal, engine, init_db
from app.main import app
from app.models import (
    Attempt,
    CacheStat,
    ContentChunk,
    GenerationCache,
    Proficiency,
    Question,
    Quiz,
    Subject,
    User,
)
from app.services import proficiency as prof
from app.services.chunking import chunk_text
from app.services.embeddings import LocalEmbedder
from scripts.ingest import load_text_document
from tests.conftest import requires_db

pytestmark = [requires_db, pytest.mark.integration]

CONTENT_ROOT = Path(__file__).resolve().parent.parent / "content"
SEED_FILES = [
    CONTENT_ROOT / "science_grade8" / "03-force-and-pressure.txt",
    CONTENT_ROOT / "mathematics_grade10" / "03-quadratic-equations.txt",
]


@pytest.fixture
async def seeded_db():
    # `app.db.engine` is module-level, but pytest-asyncio gives each test its
    # own event loop. Pooled asyncpg connections are bound to the loop that
    # created them, so a connection carried over from the previous test raises
    # "Event loop is closed". Dispose on both sides of the test to guarantee
    # every test opens fresh connections on its own loop.
    await engine.dispose()

    await init_db()
    embedder = LocalEmbedder()

    async with SessionLocal() as session:
        for model in (
            Attempt,
            Question,
            Quiz,
            Proficiency,
            ContentChunk,
            GenerationCache,
            CacheStat,
            Subject,
            User,
        ):
            await session.execute(delete(model))
        await session.commit()

        for path in SEED_FILES:
            document = load_text_document(path)
            subject = Subject(name=document.subject, grade=document.grade)
            session.add(subject)
            await session.flush()

            chunks = chunk_text(document.text)
            vectors = await embedder.embed(chunks)
            for index, (content, vector) in enumerate(
                zip(chunks, vectors, strict=True)
            ):
                session.add(
                    ContentChunk(
                        subject_id=subject.id,
                        chapter=document.chapter,
                        topic=document.topic,
                        content=content,
                        embedding=vector,
                        chunk_index=index,
                        source_hash=document.source_hash,
                    )
                )
        await session.commit()

    yield

    await engine.dispose()


@pytest.fixture
async def client(seeded_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _auth(client: AsyncClient, email: str = "student@edugen.live") -> dict:
    response = await client.post(
        "/auth/register", json={"email": email, "password": "correct-horse-8"}
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_catalogue_lists_only_ingested_topics(client: AsyncClient):
    response = await client.get("/subjects")
    assert response.status_code == 200
    subjects = response.json()
    topics = {t["topic"] for s in subjects for t in s["topics"]}
    assert topics == {"force-and-pressure", "quadratic-equations"}
    assert all(t["chunk_count"] >= 2 for s in subjects for t in s["topics"])


async def test_auth_is_required(client: AsyncClient):
    response = await client.post("/quizzes", json={"subject_id": 1, "topic": "force-and-pressure"})
    assert response.status_code == 401


async def test_full_quiz_answer_proficiency_next_quiz_path(client: AsyncClient):
    headers = await _auth(client)

    subjects = (await client.get("/subjects")).json()
    science = next(s for s in subjects if s["name"] == "Science")

    # --- first quiz: new topic, so proficiency is the 0.50 default ----------
    created = await client.post(
        "/quizzes",
        json={"subject_id": science["id"], "topic": "force-and-pressure"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    quiz = created.json()

    assert quiz["adaptive"]["proficiency_score"] == prof.INITIAL_SCORE
    assert quiz["adaptive"]["band"] == "medium"
    assert quiz["adaptive"]["is_first_quiz_on_topic"] is True
    assert quiz["questions"], "a grounded quiz must contain questions"
    assert all("correct_answer" not in q for q in quiz["questions"])

    # --- grounding: every question traces to a chunk of *this* topic --------
    async with SessionLocal() as session:
        allowed = set(
            (
                await session.execute(
                    select(ContentChunk.id).where(
                        ContentChunk.topic == "force-and-pressure"
                    )
                )
            ).scalars().all()
        )
    for question in quiz["questions"]:
        assert question["source_chunk_ids"], "question has no traceable source"
        assert set(question["source_chunk_ids"]) <= allowed

    # --- answer every question, replaying the formula alongside the API -----
    # The answer key is not exposed until the results endpoint, so the test
    # answers "A" throughout and re-derives the expected score from the
    # correctness the API reports. That keeps this an end-to-end check of the
    # *same* formula the unit test pins down.
    score = prof.INITIAL_SCORE
    for question in quiz["questions"]:
        response = await client.post(
            f"/quizzes/{quiz['id']}/answer",
            json={"question_id": question["id"], "selected_answer": "A"},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["counted"] is True
        score = prof.update_score(score, question["difficulty"], body["is_correct"])
        assert body["proficiency_score"] == score, "API score diverged from the formula"

    # --- results reflect the same numbers ----------------------------------
    results = (await client.get(f"/quizzes/{quiz['id']}/results", headers=headers)).json()
    assert results["answered"] == len(quiz["questions"])
    assert results["score_before"] == prof.INITIAL_SCORE
    assert results["score_after"] == score
    assert results["next_quiz_mix"] == prof.difficulty_mix(score, 5)
    assert results["sources"], "results must expose the source chunks"

    # --- second quiz uses the updated score --------------------------------
    second = await client.post(
        "/quizzes",
        json={"subject_id": science["id"], "topic": "force-and-pressure"},
        headers=headers,
    )
    assert second.status_code == 201, second.text
    second_quiz = second.json()
    assert second_quiz["adaptive"]["proficiency_score"] == score
    assert second_quiz["adaptive"]["band"] == prof.score_to_band(score)
    assert second_quiz["adaptive"]["mix"] == prof.difficulty_mix(
        score, len(second_quiz["questions"])
    )
    assert second_quiz["adaptive"]["is_first_quiz_on_topic"] is False


async def test_answering_twice_does_not_double_count(client: AsyncClient):
    headers = await _auth(client, "repeat@edugen.live")
    subjects = (await client.get("/subjects")).json()
    science = next(s for s in subjects if s["name"] == "Science")
    quiz = (
        await client.post(
            "/quizzes",
            json={"subject_id": science["id"], "topic": "force-and-pressure"},
            headers=headers,
        )
    ).json()
    question = quiz["questions"][0]

    first = (
        await client.post(
            f"/quizzes/{quiz['id']}/answer",
            json={"question_id": question["id"], "selected_answer": "B"},
            headers=headers,
        )
    ).json()
    second = (
        await client.post(
            f"/quizzes/{quiz['id']}/answer",
            json={"question_id": question["id"], "selected_answer": "C"},
            headers=headers,
        )
    ).json()

    assert first["counted"] is True
    assert second["counted"] is False
    assert second["proficiency_score"] == first["proficiency_score"]
    assert second["answers_count"] == first["answers_count"]


async def test_proficiency_endpoint_exposes_the_formula(client: AsyncClient):
    headers = await _auth(client, "prof@edugen.live")
    me = (await client.get("/auth/me", headers=headers)).json()
    subjects = (await client.get("/subjects")).json()
    science = next(s for s in subjects if s["name"] == "Science")
    await client.post(
        "/quizzes",
        json={"subject_id": science["id"], "topic": "force-and-pressure"},
        headers=headers,
    )

    response = await client.get(f"/users/{me['id']}/proficiency", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["alpha"] == prof.ALPHA
    assert body["initial_score"] == prof.INITIAL_SCORE
    assert len(body["topics"]) == 1
    assert body["topics"][0]["topic"] == "force-and-pressure"

    # Another user's proficiency is not readable.
    other = await _auth(client, "other@edugen.live")
    assert (
        await client.get(f"/users/{me['id']}/proficiency", headers=other)
    ).status_code == 403


# --- the negative path ------------------------------------------------------


async def test_topic_with_no_ingested_content_fails_gracefully(client: AsyncClient):
    """Spec Section 8: refuse, do not fall back to ungrounded generation."""
    headers = await _auth(client, "empty@edugen.live")
    subjects = (await client.get("/subjects")).json()
    science = next(s for s in subjects if s["name"] == "Science")

    response = await client.post(
        "/quizzes",
        json={"subject_id": science["id"], "topic": "photosynthesis-in-plants"},
        headers=headers,
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "photosynthesis-in-plants" in detail
    assert "general knowledge" in detail

    # No quiz row was created, and no proficiency was silently invented for a
    # topic that cannot be quizzed.
    async with SessionLocal() as session:
        quizzes = (await session.execute(select(Quiz))).scalars().all()
        assert all(q.topic != "photosynthesis-in-plants" for q in quizzes)


async def test_cache_serves_a_repeat_request_and_records_the_hit(client: AsyncClient):
    headers_a = await _auth(client, "cache-a@edugen.live")
    headers_b = await _auth(client, "cache-b@edugen.live")
    subjects = (await client.get("/subjects")).json()
    maths = next(s for s in subjects if s["name"] == "Mathematics")
    payload = {"subject_id": maths["id"], "topic": "quadratic-equations"}

    first = (await client.post("/quizzes", json=payload, headers=headers_a)).json()
    second = (await client.post("/quizzes", json=payload, headers=headers_b)).json()

    assert first["served_from_cache"] is False
    assert second["served_from_cache"] is True
    assert [q["question_text"] for q in first["questions"]] == [
        q["question_text"] for q in second["questions"]
    ]

    stats = (await client.get("/cache/stats")).json()
    assert stats["lookups"] >= 2
    assert stats["hits"] >= 1
    assert 0.0 < stats["hit_rate"] <= 1.0
