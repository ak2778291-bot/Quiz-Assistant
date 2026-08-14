"""Application settings, loaded from environment / .env."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Database -----------------------------------------------------------
    database_url: str = "postgresql+asyncpg://edugen:edugen@localhost:5432/edugen"

    # --- Auth ---------------------------------------------------------------
    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7

    # --- LLM / embeddings ---------------------------------------------------
    # "gemini" is the real integration. "stub" is an explicitly opt-in
    # development generator that builds questions from retrieved text only;
    # it is never selected automatically (see DESIGN.md).
    llm_provider: Literal["gemini", "stub"] = "gemini"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    gemini_api_base: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_timeout_seconds: float = 60.0

    # "local" is a deterministic hashed bag-of-words embedder so the pipeline
    # (and its tests) run offline. "gemini" uses text-embedding-004.
    embedding_provider: Literal["gemini", "local"] = "local"
    gemini_embedding_model: str = "text-embedding-004"
    embedding_dim: int = 768

    # --- Retrieval / quiz shape --------------------------------------------
    retrieval_top_k: int = 6
    questions_per_quiz: int = 5
    min_chunks_for_generation: int = 2

    # --- Caching ------------------------------------------------------------
    enable_generation_cache: bool = True

    # --- CORS ---------------------------------------------------------------
    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
