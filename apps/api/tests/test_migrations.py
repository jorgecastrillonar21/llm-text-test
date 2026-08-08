"""The Alembic migration must build the same schema the models describe."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.config import REPO_ROOT

EXPECTED_TABLES = {
    "worlds",
    "characters",
    "game_sessions",
    "messages",
    "memories",
    "relationships",
    "game_events",
}


def build_config(db_path: Path) -> Config:
    config = Config(str(REPO_ROOT / "apps" / "api" / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "apps" / "api" / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    return config


def test_upgrade_head_creates_every_table(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "migrated.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")

    # get_settings is cached; migrations/env.py reads it at import time.
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        command.upgrade(build_config(db_path), "head")
    finally:
        get_settings.cache_clear()

    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert tables >= EXPECTED_TABLES

    world_columns = {c["name"] for c in inspector.get_columns("worlds")}
    assert world_columns >= {"id", "name", "genre", "setting", "language", "created_at"}
    engine.dispose()
