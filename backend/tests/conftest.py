"""Shared test configuration.

Environment is fixed *before* any `app.*` import, because `app.db` builds the
engine at import time from `Settings`.

The logic tests (proficiency, grounding validation) need no database and
always run. The integration test needs Postgres+pgvector and is skipped unless
TEST_DATABASE_URL is set -- CI sets it from a pgvector service container.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
if TEST_DATABASE_URL:
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL

# Deterministic, offline providers for the whole test session.
os.environ.setdefault("EMBEDDING_PROVIDER", "local")
os.environ.setdefault("LLM_PROVIDER", "stub")
os.environ.setdefault("JWT_SECRET", "test-secret")

requires_db = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set; skipping database integration tests",
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
