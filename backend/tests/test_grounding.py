"""Spec 4.1: every generated question is traceable to retrieved content.

These tests run without a database. They target the validation layer, which is
the layer that actually enforces grounding -- a model that drifts to general
knowledge shows up here as a citation to a chunk that was never retrieved.
"""

from __future__ import annotations

import pytest

from app.errors import UngroundedGenerationError
from app.services.generation import (
    build_prompt,
    generate_questions,
    validate_generated_questions,
)
from app.services.llm import LLMClient, StubQuestionClient
from app.services.retrieval import RetrievedChunk

SCIENCE_CHUNKS = [
    RetrievedChunk(
        id=101,
        subject_id=1,
        chapter="Force and Pressure",
        topic="force-and-pressure",
        content=(
            "A push or a pull on an object is called a force. Force comes into play "
            "only when two objects interact with each other. The force acting on a "
            "unit area of a surface is called pressure."
        ),
        chunk_index=0,
        source_hash="a" * 64,
        score=0.91,
    ),
    RetrievedChunk(
        id=102,
        subject_id=1,
        chapter="Force and Pressure",
        topic="force-and-pressure",
        content=(
            "Liquids exert pressure on the walls of the container, and this pressure "
            "increases with depth. The envelope of air around the earth exerts "
            "atmospheric pressure."
        ),
        chunk_index=1,
        source_hash="a" * 64,
        score=0.88,
    ),
]

ALLOWED = {c.id for c in SCIENCE_CHUNKS}
# A chunk belonging to a *different* topic that was never retrieved.
FOREIGN_CHUNK_ID = 900


def _question(**overrides) -> dict:
    base = {
        "question_text": "The force acting on a unit area of a surface is called what?",
        "options": {"A": "Pressure", "B": "Friction", "C": "Weight", "D": "Momentum"},
        "correct_answer": "A",
        "difficulty": "easy",
        "explanation": "Stated directly in the excerpt.",
        "source_chunk_ids": [101],
    }
    base.update(overrides)
    return base


class RecordingClient(LLMClient):
    """Returns a canned payload and records whether it was called at all."""

    name = "recording"

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[str] = []

    async def generate_questions(self, prompt: str, *, context: dict) -> dict:
        self.calls.append(prompt)
        return self.payload


# --- the prompt itself ------------------------------------------------------


def test_prompt_contains_only_retrieved_chunks_and_their_ids():
    prompt = build_prompt(
        SCIENCE_CHUNKS,
        subject="Science",
        grade=8,
        chapter="Force and Pressure",
        difficulties=["easy", "medium"],
    )
    for chunk in SCIENCE_CHUNKS:
        assert f"[excerpt id={chunk.id}]" in prompt
        assert chunk.content in prompt
    assert "ONLY" in prompt
    assert "Grade 8" in prompt
    assert str(FOREIGN_CHUNK_ID) not in prompt


# --- the grounding invariant ------------------------------------------------


def test_valid_questions_keep_their_source_chunk_ids():
    questions, dropped = validate_generated_questions(
        {"questions": [_question(), _question(question_text="Pressure increases with what?", source_chunk_ids=[102], difficulty="medium")]},
        allowed_chunk_ids=ALLOWED,
    )
    assert dropped == []
    assert [q.source_chunk_ids for q in questions] == [[101], [102]]
    assert all(set(q.source_chunk_ids) <= ALLOWED for q in questions)


def test_question_citing_a_chunk_from_another_topic_is_rejected():
    """The silent-fallback-to-general-knowledge detector."""
    with pytest.raises(UngroundedGenerationError) as excinfo:
        validate_generated_questions(
            {"questions": [_question(source_chunk_ids=[FOREIGN_CHUNK_ID])]},
            allowed_chunk_ids=ALLOWED,
        )
    assert str(FOREIGN_CHUNK_ID) in str(excinfo.value)


def test_one_bad_citation_rejects_the_whole_generation():
    """A partially ungrounded set is not quietly trimmed down and served."""
    with pytest.raises(UngroundedGenerationError):
        validate_generated_questions(
            {
                "questions": [
                    _question(),
                    _question(
                        question_text="Which planet is closest to the sun?",
                        source_chunk_ids=[101, FOREIGN_CHUNK_ID],
                    ),
                ]
            },
            allowed_chunk_ids=ALLOWED,
        )


def test_question_without_any_citation_is_dropped():
    questions, dropped = validate_generated_questions(
        {"questions": [_question(), _question(question_text="Uncited question here?", source_chunk_ids=[])]},
        allowed_chunk_ids=ALLOWED,
    )
    assert len(questions) == 1
    assert "no source_chunk_ids" in dropped[0]


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"options": {"A": "x", "B": "y", "C": "z"}}, "options must have keys A-D"),
        ({"correct_answer": "E"}, "correct_answer not one of A-D"),
        ({"difficulty": "impossible"}, "unknown difficulty"),
        ({"question_text": "short"}, "question_text too short"),
        (
            {"options": {"A": "Pressure", "B": "Pressure", "C": "z", "D": "w"}},
            "duplicate options",
        ),
    ],
)
def test_malformed_questions_are_dropped_with_a_reason(overrides: dict, reason: str):
    malformed = _question(question_text="A second, well formed question?")
    malformed.update(overrides)
    questions, dropped = validate_generated_questions(
        {"questions": [_question(), malformed]}, allowed_chunk_ids=ALLOWED
    )
    assert len(questions) == 1
    assert any(reason in d for d in dropped)


def test_generation_fails_when_nothing_survives_validation():
    with pytest.raises(UngroundedGenerationError):
        validate_generated_questions(
            {"questions": [_question(correct_answer="Z")]}, allowed_chunk_ids=ALLOWED
        )


def test_empty_model_response_is_an_error_not_an_empty_quiz():
    with pytest.raises(UngroundedGenerationError):
        validate_generated_questions({"questions": []}, allowed_chunk_ids=ALLOWED)


# --- end-to-end through the generation service ------------------------------


async def test_generate_questions_returns_only_grounded_questions():
    client = RecordingClient({"questions": [_question(), _question(question_text="What increases with depth in a liquid?", source_chunk_ids=[102], difficulty="medium")]})
    result = await generate_questions(
        SCIENCE_CHUNKS,
        subject="Science",
        grade=8,
        chapter="Force and Pressure",
        difficulties=["easy", "medium"],
        client=client,
    )
    assert len(client.calls) == 1
    assert [q.difficulty for q in result.questions] == ["easy", "medium"]
    for question in result.questions:
        assert set(question.source_chunk_ids) <= ALLOWED


async def test_generate_questions_refuses_to_prompt_with_no_context():
    """No chunks -> the model is never called at all."""
    client = RecordingClient({"questions": [_question()]})
    with pytest.raises(UngroundedGenerationError):
        await generate_questions(
            [],
            subject="Science",
            grade=8,
            chapter="Force and Pressure",
            difficulties=["easy"],
            client=client,
        )
    assert client.calls == []


async def test_offline_stub_generator_is_also_grounded():
    """Even the dev stub can only cite chunks it was handed."""
    result = await generate_questions(
        SCIENCE_CHUNKS,
        subject="Science",
        grade=8,
        chapter="Force and Pressure",
        difficulties=["easy", "medium", "hard"],
        client=StubQuestionClient(),
    )
    assert result.questions
    for question in result.questions:
        assert set(question.source_chunk_ids) <= ALLOWED
        assert question.correct_answer in question.options
