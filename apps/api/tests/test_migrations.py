"""The Alembic migration must build the same schema the models describe."""

from __future__ import annotations

import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.config import REPO_ROOT

EXPECTED_TABLES = {
    "worlds",
    "characters",
    "game_sessions",
    "messages",
    "memories",
    "relationships",
    "game_events",
    "scheduled_events",
}

# A save written before simulation time existed: one world, one session, and three
# events in the order they were recorded.
LEGACY_WORLD = "0" * 32
LEGACY_SESSION = "1" * 32

SEED_LEGACY_ROWS = (
    """
    INSERT INTO worlds (id, name, description, genre, setting, language, rules_json,
                        created_at, updated_at)
    VALUES (:world, 'Old World', '', 'fantasy', '', 'en', '{"version": 1}',
            '2026-01-01 10:00:00.000000', '2026-01-01 10:00:00.000000')
    """,
    # A child of `worlds`, which every migration so far rebuilds. If enforcement is
    # left on during a batch rebuild, dropping the old table cascades and this
    # disappears -- along with every session, message and memory in the database.
    """
    INSERT INTO characters (id, world_id, name, description, appearance, personality,
                            backstory, speech_style, goals, secrets, created_at, updated_at)
    VALUES ('c0', :world, 'Elena', '', '', 'dry', '', '', '[]', '[]',
            '2026-01-01 10:00:00.000000', '2026-01-01 10:00:00.000000')
    """,
    """
    INSERT INTO game_sessions (id, world_id, title, player_name, player_description,
                               current_location, summary, turn_index, created_at, updated_at)
    VALUES (:session, :world, 'Run', 'Rin', '', '', '', 3,
            '2026-01-01 10:00:00.000000', '2026-01-01 10:00:00.000000')
    """,
    """
    INSERT INTO game_events (id, session_id, turn_index, type, description, created_at)
    VALUES ('a0', :session, 1, 'first', '', '2026-01-01 10:00:00.000000'),
           ('a1', :session, 2, 'second', '', '2026-01-01 10:00:01.000000'),
           ('a2', :session, 3, 'third', '', '2026-01-01 10:00:02.000000')
    """,
)


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
    assert "elapsed_minutes" in {c["name"] for c in inspector.get_columns("game_sessions")}
    assert {"occurred_at", "event_sequence"} <= {
        c["name"] for c in inspector.get_columns("game_events")
    }
    engine.dispose()


def test_a_save_written_before_the_clock_existed_is_migrated_safely(
    tmp_path: Path, monkeypatch
) -> None:
    """Existing rows get a defensible default rather than an invented history.

    A session that predates simulation time has no record of when anything happened,
    so it starts at zero and its events are ordered by the only signal they carry:
    the order they were written.
    """
    db_path = tmp_path / "legacy.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")

    from app.config import get_settings

    get_settings.cache_clear()
    config = build_config(db_path)
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        command.upgrade(config, "5cc072747a2d")
        with engine.begin() as conn:
            for statement in SEED_LEGACY_ROWS:
                conn.execute(text(statement), {"world": LEGACY_WORLD, "session": LEGACY_SESSION})

        command.upgrade(config, "head")
    finally:
        get_settings.cache_clear()

    with engine.connect() as conn:
        # Nothing was cascaded away by a table rebuild. This is the assertion that
        # would have caught the migration deleting every child row it touched.
        assert conn.execute(text("SELECT COUNT(*) FROM characters")).scalar() == 1

        session_row = conn.execute(text("SELECT elapsed_minutes FROM game_sessions")).one()
        assert session_row.elapsed_minutes == 0

        world_row = conn.execute(text("SELECT initial_datetime FROM worlds")).one()
        assert json.loads(world_row.initial_datetime) == {
            "year": 1,
            "month": 1,
            "day": 1,
            "hour": 8,
            "minute": 0,
        }

        events = conn.execute(
            text(
                "SELECT type, occurred_at, event_sequence FROM game_events ORDER BY event_sequence"
            )
        ).all()
        assert [row.type for row in events] == ["first", "second", "third"]
        assert [row.event_sequence for row in events] == [1, 2, 3]
        assert {row.occurred_at for row in events} == {0}
    engine.dispose()
