from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection

from app.config import get_settings

# `models` is imported for its side effect: it registers every table on Base.metadata.
from app.infrastructure.db import models  # noqa: F401
from app.infrastructure.db.base import Base
from app.infrastructure.db.engine import create_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
settings = get_settings()


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # SQLite cannot ALTER most things in place; batch mode rewrites tables.
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def verify_no_orphans(connection: Connection) -> None:
    """Prove that turning enforcement off did not leave dangling references behind.

    Cheap insurance: without it, "we disabled foreign keys for the migration" would
    be a hope rather than a checked claim. A violation fails the upgrade loudly
    instead of leaving a database that only misbehaves later.
    """
    if connection.dialect.name != "sqlite":
        return
    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(
            f"Migration left {len(violations)} orphaned row(s): {violations[:5]}. "
            "The database was not upgraded cleanly."
        )


async def run_migrations_online() -> None:
    # Foreign keys are enforced at runtime, but must not be while migrating. SQLite
    # cannot alter a column in place, so batch mode rebuilds the table: copy it,
    # DROP the original, rename the copy. With enforcement on, that DROP fires every
    # ON DELETE CASCADE pointing at the table -- rebuilding `worlds` would delete
    # every character, session, message and memory in the database, silently and
    # successfully. SQLite's own ALTER TABLE guidance is to disable enforcement
    # around a rebuild; `verify_no_orphans` is what makes that safe to do.
    engine = create_engine(settings, enforce_foreign_keys=False)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
        await connection.run_sync(verify_no_orphans)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
