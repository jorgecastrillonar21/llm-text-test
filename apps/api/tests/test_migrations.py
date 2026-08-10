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
    "resolutions",
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


def test_the_migration_builds_the_constraints_idempotency_and_ordering_rely_on(
    tmp_path: Path, monkeypatch
) -> None:
    """Some of this system's guarantees are database constraints, not Python.

    Idempotency is enforced by a unique index -- two concurrent retries of one
    submission race, and the loser gets an IntegrityError rather than both writing a
    resolution. Event ordering is enforced the same way. Neither survives being merely
    declared in the model if the migration does not build it, so the migration is what
    is checked here.
    """
    db_path = tmp_path / "constraints.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")

    from app.config import get_settings

    get_settings.cache_clear()
    try:
        command.upgrade(build_config(db_path), "head")
    finally:
        get_settings.cache_clear()

    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    inspector = inspect(engine)

    # One resolution per (session, key). This is the whole of idempotency.
    assert {tuple(c["column_names"]) for c in inspector.get_unique_constraints("resolutions")} >= {
        ("session_id", "idempotency_key")
    }
    # One sequence number per session, so "what happened first" has one answer.
    assert {tuple(c["column_names"]) for c in inspector.get_unique_constraints("game_events")} >= {
        ("session_id", "event_sequence")
    }

    resolution_indexes = {i["name"] for i in inspector.get_indexes("resolutions")}
    assert {
        "ix_resolutions_session_source",  # the mechanical trail, filtered by what caused it
        "ix_resolutions_session_time",  # and read newest-first
        "ix_resolutions_parent_resolution_id",  # a future compound action's children
    } <= resolution_indexes

    event_indexes = {i["name"] for i in inspector.get_indexes("game_events")}
    assert {
        "ix_game_events_session_time",  # fictional order, which is how history is read
        "ix_game_events_session_category",  # category and subtype, the open vocabulary
        "ix_game_events_session_importance",  # landmark retrieval for StoryContext
        "ix_game_events_resolution_id",  # every event one resolution wrote
    } <= event_indexes

    events = {c["name"] for c in inspector.get_columns("game_events")}
    # Causality across resolutions, and grouping within one.
    assert {"caused_by_event_id", "resolution_id"} <= events
    resolutions = {c["name"] for c in inspector.get_columns("resolutions")}
    assert {"parent_resolution_id", "source_type", "source_id"} <= resolutions

    session_fk = {
        fk["referred_table"]
        for fk in inspector.get_foreign_keys("resolutions")
        if fk["constrained_columns"] == ["session_id"]
    }
    assert session_fk == {"game_sessions"}
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
                "SELECT subtype, summary, category, importance, occurred_at, event_sequence "
                "FROM game_events ORDER BY event_sequence"
            )
        ).all()
        # `type` became `subtype` in Event/Resolution V1 and the values came with it.
        assert [row.subtype for row in events] == ["first", "second", "third"]
        assert [row.event_sequence for row in events] == [1, 2, 3]
        assert {row.occurred_at for row in events} == {0}
        # Free text from before there were categories is 'other', not a guess.
        assert {row.category for row in events} == {"other"}
        assert {row.importance for row in events} == {2}
    engine.dispose()


# A save from just before Event/Resolution V1: a session with history, and two rows in
# other tables pointing at one of those events. `game_events` is rebuilt by that
# migration, and a rebuild is where references to it get silently cut.
PRIOR_WORLD = "2" * 32
PRIOR_SESSION = "3" * 32
PRIOR_EVENT = "4" * 32

SEED_ROWS_BEFORE_RESOLUTION = (
    """
    INSERT INTO worlds (id, name, description, genre, setting, language, rules_json,
                        initial_datetime, initial_facts, initial_situations,
                        created_at, updated_at)
    VALUES (:world, 'Asterfall', '', 'fantasy', '', 'en', '{"version": 1}',
            '{"year": 842, "month": 10, "day": 4, "hour": 6, "minute": 0}',
            '[]', '[]',
            '2026-01-01 10:00:00.000000', '2026-01-01 10:00:00.000000')
    """,
    """
    INSERT INTO game_sessions (id, world_id, title, player_name, player_description,
                               current_location, summary, turn_index, elapsed_minutes,
                               state_revision, created_at, updated_at)
    VALUES (:session, :world, 'Run', 'Rin', '', '', '', 4, 720, 6,
            '2026-01-01 10:00:00.000000', '2026-01-01 10:00:00.000000')
    """,
    """
    INSERT INTO game_events (id, session_id, turn_index, occurred_at, event_sequence,
                             type, description, created_at)
    VALUES (:event, :session, 4, 720, 1, 'time.advanced', 'Twelve hours passed.',
            '2026-01-01 10:00:00.000000'),
           ('5a', :session, 4, 720, 2, 'BRIDGE COLLAPSED', 'The east bridge fell.',
            '2026-01-01 10:00:01.000000')
    """,
    # Points at the event above. If the rebuild cascades, this row disappears.
    """
    INSERT INTO world_facts (id, session_id, kind, subject_type, subject_id, property,
                             value, importance, current_value_since, authority,
                             source_event_id, tags, created_at, updated_at)
    VALUES ('6a', :session, 'world_truth', 'world', NULL, 'world.east_bridge',
            '"collapsed"', 4, 720, 'engine', :event, '[]',
            '2026-01-01 10:00:00.000000', '2026-01-01 10:00:00.000000')
    """,
    """
    INSERT INTO situations (id, session_id, category, subtype, title, description,
                            status, intensity, threat, momentum, importance, scope,
                            started_at, last_progressed_at, source_event_id,
                            situation_metadata, tags, created_at, updated_at)
    VALUES ('7a', :session, 'conflict', 'siege', 'Siege of Asterfall', '',
            'active', 60, 70, 10, 4, 'regional', 0, 720, :event, '{}', '[]',
            '2026-01-01 10:00:00.000000', '2026-01-01 10:00:00.000000')
    """,
)


def test_reshaping_game_events_keeps_the_rows_that_point_at_them(
    tmp_path: Path, monkeypatch
) -> None:
    """`game_events` is rebuilt, and everything referencing it must survive.

    Batch mode rewrites a table by copying, dropping and renaming. The DROP is what
    fires `ON DELETE CASCADE` on every child -- and `world_facts` and `situations` both
    carry a `source_event_id`. `migrations/env.py` disables enforcement for exactly this
    reason; this test is what proves it worked, because a migration that cascaded the
    rows away would otherwise report success.
    """
    db_path = tmp_path / "prior.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")

    from app.config import get_settings

    get_settings.cache_clear()
    config = build_config(db_path)
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        command.upgrade(config, "f429e3bc3d11")
        with engine.begin() as conn:
            for statement in SEED_ROWS_BEFORE_RESOLUTION:
                conn.execute(
                    text(statement),
                    {"world": PRIOR_WORLD, "session": PRIOR_SESSION, "event": PRIOR_EVENT},
                )

        command.upgrade(config, "head")

        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM world_facts")).scalar() == 1
            assert conn.execute(text("SELECT COUNT(*) FROM situations")).scalar() == 1

            # The links themselves, not just the row counts. A rebuild that renumbered
            # ids would leave both rows in place pointing at nothing.
            fact_source = conn.execute(text("SELECT source_event_id FROM world_facts")).scalar()
            situation_source = conn.execute(text("SELECT source_event_id FROM situations")).scalar()
            assert fact_source == PRIOR_EVENT
            assert situation_source == PRIOR_EVENT

            events = conn.execute(
                text("SELECT category, subtype, summary FROM game_events ORDER BY event_sequence")
            ).all()
            assert [row.subtype for row in events] == ["time_advanced", "bridge_collapsed"]
            assert [row.category for row in events] == ["system", "other"]
            assert events[1].summary == "The east bridge fell."

            # env.py checks this too, but a migration that broke it must fail a test
            # rather than only a log line.
            assert conn.execute(text("PRAGMA foreign_key_check")).all() == []

        # Down and back up, with data in the table both ways.
        command.downgrade(config, "-1")
        with engine.connect() as conn:
            restored = conn.execute(
                text("SELECT type, description FROM game_events ORDER BY event_sequence")
            ).all()
            assert [row.type for row in restored] == ["time_advanced", "bridge_collapsed"]
            assert restored[0].description == "Twelve hours passed."

        command.upgrade(config, "head")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM game_events")).scalar() == 2
            assert conn.execute(text("SELECT COUNT(*) FROM resolutions")).scalar() == 0
    finally:
        get_settings.cache_clear()
        engine.dispose()
