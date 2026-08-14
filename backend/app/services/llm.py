"""LLM clients.

`GeminiClient` is the real integration (REST, JSON mode with a response
schema). `StubQuestionClient` is an explicitly opt-in offline generator used
for local demos and tests -- it is selected only when LLM_PROVIDER=stub, never
as a silent fallback, and it still builds every question out of the retrieved
chunk text, so it cannot produce an ungrounded question either.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any

import httpx

from app.config import settings
from app.errors import LLMUnavailableError

QUESTION_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question_text": {"type": "string"},
                    "options": {
                        "type": "object",
                        "properties": {
                            "A": {"type": "string"},
                            "B": {"type": "string"},
                            "C": {"type": "string"},
                            "D": {"type": "string"},
                        },
                        "required": ["A", "B", "C", "D"],
                    },
                    "correct_answer": {"type": "string", "enum": ["A", "B", "C", "D"]},
                    "difficulty": {
                        "type": "string",
                        "enum": ["easy", "medium", "hard"],
                    },
                    "explanation": {"type": "string"},
                    "source_chunk_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                },
                "required": [
                    "question_text",
                    "options",
                    "correct_answer",
                    "difficulty",
                    "explanation",
                    "source_chunk_ids",
                ],
            },
        }
    },
    "required": ["questions"],
}


class LLMClient(ABC):
    name: str

    @abstractmethod
    async def generate_questions(self, prompt: str, *, context: dict) -> dict:
        """Return a dict shaped like QUESTION_RESPONSE_SCHEMA."""


class GeminiClient(LLMClient):
    name = "gemini"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key or settings.gemini_api_key
        self.model = model or settings.gemini_model

    async def generate_questions(self, prompt: str, *, context: dict) -> dict:
        if not self.api_key:
            raise LLMUnavailableError(
                "LLM_PROVIDER=gemini but GEMINI_API_KEY is not set. Set the key, "
                "or set LLM_PROVIDER=stub for an offline demo."
            )
        url = f"{settings.gemini_api_base}/models/{self.model}:generateContent"
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
                "responseSchema": QUESTION_RESPONSE_SCHEMA,
            },
        }
        try:
            async with httpx.AsyncClient(
                timeout=settings.gemini_timeout_seconds
            ) as client:
                response = await client.post(
                    url, params={"key": self.api_key}, json=body
                )
        except httpx.HTTPError as exc:  # network-level failure
            raise LLMUnavailableError(f"Gemini request failed: {exc}") from exc

        if response.status_code >= 400:
            raise LLMUnavailableError(
                f"Gemini returned {response.status_code}: {response.text[:300]}"
            )

        data = response.json()
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise LLMUnavailableError(
                f"Unexpected Gemini response shape: {json.dumps(data)[:300]}"
            ) from exc

        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMUnavailableError(
                f"Gemini did not return valid JSON: {text[:300]}"
            ) from exc


class StubQuestionClient(LLMClient):
    """Deterministic offline generator (LLM_PROVIDER=stub).

    Builds cloze questions by blanking the most distinctive term out of a
    sentence taken from a retrieved chunk. Distractors are drawn from terms in
    the *other* retrieved chunks of the same topic, so every option is also
    curriculum text.
    """

    name = "stub"

    _SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
    _TERM_RE = re.compile(r"\b[A-Za-z][A-Za-z-]{5,}\b")

    async def generate_questions(self, prompt: str, *, context: dict) -> dict:
        chunks: list[dict] = context["chunks"]
        difficulties: list[str] = context["difficulties"]

        candidates: list[tuple[int, str, str]] = []  # (chunk_id, sentence, term)
        for chunk in chunks:
            for sentence in self._SENTENCE_RE.split(chunk["content"].replace("\n", " ")):
                sentence = sentence.strip()
                if not (40 <= len(sentence) <= 220):
                    continue
                terms = sorted(
                    {m.group(0) for m in self._TERM_RE.finditer(sentence)},
                    key=lambda t: (-len(t), t),
                )
                if terms:
                    candidates.append((chunk["id"], sentence, terms[0]))

        # Deduplicate case-insensitively: the corpus contains both "Force" and
        # "force", and two options differing only in case are rejected by
        # validation as duplicates, silently shortening the quiz.
        seen_lower: set[str] = set()
        vocabulary: list[str] = []
        for term in sorted(
            {
                m.group(0)
                for chunk in chunks
                for m in self._TERM_RE.finditer(chunk["content"])
            },
            key=lambda t: (-len(t), t),
        ):
            if term.lower() not in seen_lower:
                seen_lower.add(term.lower())
                vocabulary.append(term)

        questions = []
        used_terms: set[str] = set()
        for difficulty in difficulties:
            picked = next(
                (c for c in candidates if c[2].lower() not in used_terms), None
            )
            if picked is None:
                break
            chunk_id, sentence, term = picked
            candidates.remove(picked)
            used_terms.add(term.lower())

            # Offset the distractor window per question so successive questions
            # don't all reuse the same three terms.
            pool = [v for v in vocabulary if v.lower() != term.lower()]
            if len(pool) < 3:
                break
            offset = (len(questions) * 3) % len(pool)
            distractors = (pool + pool)[offset : offset + 3]
            options_values = [term, *distractors]
            # Fixed rotation so the answer key is not always "A", but still
            # deterministic for a given question index.
            shift = len(questions) % 4
            rotated = options_values[-shift:] + options_values[:-shift] if shift else options_values
            letters = ["A", "B", "C", "D"]
            options = dict(zip(letters, rotated, strict=True))
            correct = letters[rotated.index(term)]

            questions.append(
                {
                    "question_text": (
                        "Complete the statement from the chapter: "
                        + sentence.replace(term, "______", 1)
                    ),
                    "options": options,
                    "correct_answer": correct,
                    "difficulty": difficulty,
                    "explanation": f"The source passage states: “{sentence}”",
                    "source_chunk_ids": [chunk_id],
                }
            )

        return {"questions": questions}


def get_llm_client() -> LLMClient:
    if settings.llm_provider == "stub":
        return StubQuestionClient()
    return GeminiClient()
