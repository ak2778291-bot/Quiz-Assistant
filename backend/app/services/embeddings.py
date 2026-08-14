"""Embedding providers.

Two implementations behind one interface:

* `GeminiEmbedder`  - the real thing (text-embedding-004, 768 dims).
* `LocalEmbedder`   - a deterministic signed hashing-trick bag-of-words
                      embedder, also 768 dims, no network.

The local one exists so the ingestion pipeline, the retrieval tests and a
`docker compose up` demo all work without an API key, and so retrieval tests
are reproducible. It is a real lexical retriever (sublinear TF + L2 norm), not
a mock -- on a curated ~12-chapter corpus it separates topics cleanly. Both
providers emit the same dimensionality, so the pgvector column is unchanged
when you switch `EMBEDDING_PROVIDER`.
"""

from __future__ import annotations

import hashlib
import math
import re
from abc import ABC, abstractmethod
from collections import Counter

import httpx

from app.config import settings
from app.errors import LLMUnavailableError

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Very small stoplist: enough to stop function words dominating the vector on
# a corpus this size, not a full linguistic pipeline.
_STOPWORDS = frozenset(
    """a an the of and or to in is are was were be been being it its this that
    these those for with as by on at from can could will would shall should may
    might must do does did have has had not no if then than so such which who
    whom what when where how we you they he she i our your their his her them us
    also into more most other some any each both all only own same too very""".split()
)


class Embedder(ABC):
    dim: int

    @abstractmethod
    async def embed(self, texts: list[str], *, is_query: bool = False) -> list[list[float]]: ...

    async def embed_one(self, text: str, *, is_query: bool = False) -> list[float]:
        return (await self.embed([text], is_query=is_query))[0]


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1]


class LocalEmbedder(Embedder):
    """Deterministic signed hashing-trick embedder."""

    def __init__(self, dim: int | None = None) -> None:
        self.dim = dim or settings.embedding_dim

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        counts = Counter(_tokenize(text))
        for token, count in counts.items():
            weight = 1.0 + math.log(count)  # sublinear TF
            # Two independent hashes per token halves collision damage.
            for salt in ("0", "1"):
                digest = hashlib.blake2b(
                    f"{salt}:{token}".encode(), digest_size=8
                ).digest()
                value = int.from_bytes(digest, "big")
                index = value % self.dim
                sign = 1.0 if (value >> 63) & 1 else -1.0
                vec[index] += sign * weight

        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            return vec
        return [v / norm for v in vec]

    async def embed(self, texts: list[str], *, is_query: bool = False) -> list[list[float]]:
        return [self._vector(t) for t in texts]


class GeminiEmbedder(Embedder):
    """Google `text-embedding-004` via the REST API."""

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key or settings.gemini_api_key
        self.model = model or settings.gemini_embedding_model
        self.dim = settings.embedding_dim

    async def embed(self, texts: list[str], *, is_query: bool = False) -> list[list[float]]:
        if not self.api_key:
            raise LLMUnavailableError(
                "EMBEDDING_PROVIDER=gemini but GEMINI_API_KEY is not set."
            )
        task_type = "RETRIEVAL_QUERY" if is_query else "RETRIEVAL_DOCUMENT"
        url = f"{settings.gemini_api_base}/models/{self.model}:batchEmbedContents"
        body = {
            "requests": [
                {
                    "model": f"models/{self.model}",
                    "content": {"parts": [{"text": t}]},
                    "taskType": task_type,
                    "outputDimensionality": self.dim,
                }
                for t in texts
            ]
        }
        async with httpx.AsyncClient(timeout=settings.gemini_timeout_seconds) as client:
            response = await client.post(
                url, params={"key": self.api_key}, json=body
            )
        if response.status_code >= 400:
            raise LLMUnavailableError(
                f"Gemini embedding call failed ({response.status_code}): {response.text[:300]}"
            )
        data = response.json()
        return [item["values"] for item in data["embeddings"]]


def get_embedder() -> Embedder:
    if settings.embedding_provider == "gemini":
        return GeminiEmbedder()
    return LocalEmbedder()
