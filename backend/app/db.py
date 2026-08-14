"""Async SQLAlchemy engine / session wiring."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.database_url, pool_pre_ping=True, future=True)

SessionLocal = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


#: Arbitrary constant identifying the schema-init advisory lock.
_INIT_LOCK_KEY = 4_181_977_201


async def init_db() -> None:
    """Create the pgvector extension and all tables.

    No Alembic: the schema is small, this project has no production data to
    migrate, and `create_all` keeps the SQLAlchemy models the single source of
    truth. Documented as a scoping decision in DESIGN.md.

    The advisory lock matters under `docker compose up`: the `backend` and
    `ingest` services both start the moment the database reports healthy, and
    both call this function. Concurrent `CREATE EXTENSION` / `CREATE TABLE`
    against the same catalogue rows can fail with "tuple concurrently
    updated". The lock is transaction-scoped, so it is released with the
    surrounding `engine.begin()` block whether it commits or raises.
    """
    from sqlalchemy import text

    from app import models  # noqa: F401  (register mappers)

    async with engine.begin() as conn:
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": _INIT_LOCK_KEY}
        )
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
