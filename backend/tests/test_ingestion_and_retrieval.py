"""Chunking, corpus sanity, and retrieval-ranking quality (no database)."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from app.services.chunking import TARGET_WORDS, chunk_text
from app.services.embeddings import LocalEmbedder
from app.utils import slugify
from scripts.ingest import discover

CONTENT_ROOT = Path(__file__).resolve().parent.parent / "content"


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


# --- chunking ---------------------------------------------------------------


def test_chunker_respects_the_word_budget_and_loses_nothing():
    text = "\n\n".join(f"Paragraph {i}. " + "word " * 60 for i in range(10))
    chunks = chunk_text(text)
    assert len(chunks) > 1
    # Overlap means chunks are allowed to exceed TARGET_WORDS a little, but not
    # unboundedly.
    assert all(len(c.split()) <= TARGET_WORDS * 2 for c in chunks)
    assert "Paragraph 0." in chunks[0]
    assert "Paragraph 9." in chunks[-1]


def test_chunker_does_not_split_a_short_document():
    assert len(chunk_text("A single short paragraph about force and pressure.")) == 1


def test_chunker_is_deterministic():
    text = (CONTENT_ROOT / "science_grade8" / "03-force-and-pressure.txt").read_text(
        encoding="utf-8"
    )
    assert chunk_text(text) == chunk_text(text)


# --- the curated corpus -----------------------------------------------------


def test_corpus_is_the_documented_scope():
    """2 subjects x 2 grades, three chapters each -- README states this number."""
    documents = discover(CONTENT_ROOT)
    pairs = {(d.subject, d.grade) for d in documents}
    assert pairs == {
        ("Science", 8),
        ("Science", 10),
        ("Mathematics", 8),
        ("Mathematics", 10),
    }
    assert len(documents) == 12
    assert len({d.topic for d in documents}) == 12
    for document in documents:
        assert document.topic == slugify(document.chapter)
        assert len(chunk_text(document.text)) >= 2, (
            f"{document.chapter} produces too few chunks to ground a quiz"
        )


# --- retrieval ranking ------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "expected_topic"),
    [
        ("atmospheric pressure and the force on a unit area", "force-and-pressure"),
        ("the discriminant decides the nature of the roots", "quadratic-equations"),
        ("antibiotics and vaccines produced by microorganisms", "microorganisms-friend-and-foe"),
        ("terminating decimal expansion of a rational number", "real-numbers"),
    ],
)
async def test_local_embedder_ranks_the_right_chapter_first(query: str, expected_topic: str):
    """The offline embedder must actually separate the ingested chapters.

    If this fails, the local-provider demo is retrieving noise and the
    grounding guarantee, while still enforced, would be grounded in the
    *wrong* chapter.
    """
    documents = discover(CONTENT_ROOT)
    embedder = LocalEmbedder()

    texts = [d.text for d in documents]
    vectors = await embedder.embed(texts)
    query_vector = await embedder.embed_one(query, is_query=True)

    ranked = sorted(
        zip(documents, vectors, strict=True),
        key=lambda pair: -_cosine(query_vector, pair[1]),
    )
    assert ranked[0][0].topic == expected_topic


async def test_embeddings_are_deterministic_and_normalised():
    embedder = LocalEmbedder()
    first = await embedder.embed_one("force and pressure in liquids")
    second = await embedder.embed_one("force and pressure in liquids")
    assert first == second
    assert len(first) == embedder.dim
    assert math.isclose(math.sqrt(sum(v * v for v in first)), 1.0, rel_tol=1e-9)
