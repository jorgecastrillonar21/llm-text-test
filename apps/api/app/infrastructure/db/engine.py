"""Async engine/session factory.

Schema creation is deliberately absent: `alembic upgrade head` owns the schema so
a stale dev database can never be papered over by create_all at startup.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings


def _sqlite_pragmas(enforce_foreign_keys: bool) -> Callable[[Any, Any], None]:
    def apply(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute(f"PRAGMA foreign_keys={'ON' if enforce_foreign_keys else 'OFF'}")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    return apply


def create_engine(settings: Settings, *, enforce_foreign_keys: bool = True) -> AsyncEngine:
    """The application's engine. Foreign keys are enforced unless a caller says not to.

    The one caller that says not to is the migration runner, and it has a specific
    reason: SQLite cannot alter a column in place, so Alembic's batch mode rebuilds
    the table -- copy, `DROP TABLE`, rename. With enforcement on, that DROP fires
    every `ON DELETE CASCADE` aimed at the table, so rebuilding `worlds` would take
    every character, session, message and memory in the database down with it. See
    `migrations/env.py`.
    """
    engine = create_async_engine(settings.database_url, echo=False, future=True)
    if engine.dialect.name == "sqlite":
        event.listen(engine.sync_engine, "connect", _sqlite_pragmas(enforce_foreign_keys))
    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Session with commit-on-success / rollback-on-error semantics."""
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def verify_schema(engine: AsyncEngine) -> bool:
    """True when migrations have been applied."""
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='worlds'")
        )
        return result.first() is not None
