"""consolidate world state v1

Revision ID: 2d9f47c1a8be
Revises: 65bea0eded6c
Create Date: 2026-08-10 09:12:44.201883

One column, and a boundary.

    game_sessions.world_state_version   which shape this session's WorldState root is in

Consolidating WorldState was mostly a matter of naming what was already there: the
root of a session's mutable world is `(world_state_version, elapsed_minutes,
state_revision)` on this table, and everything with a cardinality -- facts, locations,
connections, situations, scheduled events, history, audit -- stays in the tables that
already own it. No table is created here, and none is merged. A migration that folded
those into one serialized document is exactly what `docs/world-state.md` argues
against.

What is new is that the version is now *stored* rather than assumed. Until today every
build could only read the shape it wrote, and had no way to notice a row written by a
later one; `app.domain.world_state.read_world_state` now refuses a version it does not
know. Existing rows get 1, which is the shape they are in -- a true statement rather
than an invented one, since version 1 is the only shape that has ever existed.

The column is created nullable, backfilled, then made NOT NULL, so the order is safe on
a database that already has rows. Batch mode rebuilds `game_sessions`, which is why
`migrations/env.py` disables foreign-key enforcement for the duration: the DROP half of
a rebuild would otherwise cascade every message, memory, fact, location state,
situation, scheduled event, resolution and game event in the database into oblivion.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '2d9f47c1a8be'
down_revision: str | None = '65bea0eded6c'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('game_sessions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('world_state_version', sa.Integer(), nullable=True))

    op.execute(
        'UPDATE game_sessions SET world_state_version = 1 WHERE world_state_version IS NULL'
    )

    with op.batch_alter_table('game_sessions', schema=None) as batch_op:
        batch_op.alter_column('world_state_version', existing_type=sa.Integer(), nullable=False)
        # Not `>= 0`. There is no version zero, and a row claiming one is corrupt
        # rather than merely old.
        batch_op.create_check_constraint(
            'ck_game_sessions_world_state_version_positive', 'world_state_version >= 1'
        )


def downgrade() -> None:
    with op.batch_alter_table('game_sessions', schema=None) as batch_op:
        batch_op.drop_constraint(
            'ck_game_sessions_world_state_version_positive', type_='check'
        )
        batch_op.drop_column('world_state_version')
