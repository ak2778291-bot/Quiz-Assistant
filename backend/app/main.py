"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.config import settings
from app.db import init_db
from app.errors import (
    ContentNotAvailableError,
    LLMUnavailableError,
    UngroundedGenerationError,
)
from app.routers import auth, quizzes, subjects, users

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="edugen.live API",
    version=__version__,
    description=(
        "RAG-powered adaptive quiz platform. Questions are generated only from "
        "retrieved NCERT curriculum content, and each student's per-topic "
        "proficiency deterministically drives the next quiz's difficulty."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(subjects.router)
app.include_router(quizzes.router)
app.include_router(quizzes.cache_router)
app.include_router(users.router)


@app.exception_handler(ContentNotAvailableError)
async def _content_not_available(request: Request, exc: ContentNotAvailableError):
    return JSONResponse(
        status_code=422,
        content={"detail": str(exc), "code": "content_not_available"},
    )


@app.exception_handler(UngroundedGenerationError)
async def _ungrounded(request: Request, exc: UngroundedGenerationError):
    return JSONResponse(
        status_code=502, content={"detail": str(exc), "code": "ungrounded_generation"}
    )


@app.exception_handler(LLMUnavailableError)
async def _llm_unavailable(request: Request, exc: LLMUnavailableError):
    return JSONResponse(
        status_code=503, content={"detail": str(exc), "code": "llm_unavailable"}
    )


@app.get("/health", tags=["ops"])
async def health() -> dict:
    return {
        "status": "ok",
        "version": __version__,
        "llm_provider": settings.llm_provider,
        "embedding_provider": settings.embedding_provider,
    }
