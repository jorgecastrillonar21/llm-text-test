"""world state time v1

Revision ID: c4f1ab6d5e73
Revises: 5cc072747a2d
Create Date: 2026-08-08 19:40:00.000000

Gives every session an authoritative simulation clock and every recorded event a
place on it.

    worlds.initial_datetime        what fictional instant elapsed_minutes = 0 means
    game_sessions.elapsed_minutes  the clock itself, counted from the session's start
    game_events.occurred_at        when in the fiction an event happened
    game_events.event_sequence     a stable order for events sharing a minute
    scheduled_events               things due at a future point on that clock

Existing rows get `elapsed_minutes = 0` and `occurred_at = 0`: a save written before
this migration has no record of fictional time, and inventing one would be worse than
saying the story starts now. The world's start date is backfilled with the same
literal the application defaults to, written out below rather than imported -- a
migration must keep producing what it produced the day it ran.

`event_sequence` is backfilled from insertion order, which is the only ordering the
old rows carry. Every column is added nullable, backfilled, then made NOT NULL, so
the statement order is safe on a database that already has rows.
"""
from __future__ import annotations

import json
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'c4f1ab6d5e73'
down_revision: str | None = '5cc072747a2d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# DEFAULT_INITIAL_DATETIME as of this migration: the first morning of year one.
# Frozen, like the rules defaults in 5cc072747a2d.
DEFAULT_INITIAL_DATETIME = {'year': 1, 'month': 1, 'day': 1, 'hour': 8, 'minute': 0}


# Insertion order is all the ordering pre-existing events have. Ties on created_at
# fall back to the primary key so the result is deterministic rather than merely
# plausible.
BACKFILL_EVENT_SEQUENCE = """
UPDATE game_events
SET event_sequence = (
    SELECT COUNT(*)
    FROM game_events AS earlier
    WHERE earlier.session_id = game_events.session_id
      AND (
        earlier.created_at < game_events.created_at
        OR (earlier.created_at = game_events.created_at AND earlier.id <= game_events.id)
      )
)
WHERE event_sequence IS NULL
"""


def upgrade() -> None:
    with op.batch_alter_table('worlds', schema=None) as batch_op:
        batch_op.add_column(sa.Column('initial_datetime', sa.JSON(), nullable=True))

    op.execute(
        sa.text(
            'UPDATE worlds SET initial_datetime = :moment WHERE initial_datetime IS NULL'
        ).bindparams(moment=json.dumps(DEFAULT_INITIAL_DATETIME))
    )

    with op.batch_alter_table('worlds', schema=None) as batch_op:
        batch_op.alter_column('initial_datetime', existing_type=sa.JSON(), nullable=False)

    with op.batch_alter_table('game_sessions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('elapsed_minutes', sa.BigInteger(), nullable=True))

    op.execute('UPDATE game_sessions SET elapsed_minutes = 0 WHERE elapsed_minutes IS NULL')

    with op.batch_alter_table('game_sessions', schema=None) as batch_op:
        batch_op.alter_column('elapsed_minutes', existing_type=sa.BigInteger(), nullable=False)
        batch_op.create_check_constraint('ck_game_sessions_elapsed_nonnegative', 'elapsed_minutes >= 0')

    with op.batch_alter_table('game_events', schema=None) as batch_op:
        batch_op.add_column(sa.Column('occurred_at', sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column('event_sequence', sa.Integer(), nullable=True))

    op.execute('UPDATE game_events SET occurred_at = 0 WHERE occurred_at IS NULL')
    op.execute(BACKFILL_EVENT_SEQUENCE)

    with op.batch_alter_table('game_events', schema=None) as batch_op:
        batch_op.alter_column('occurred_at', existing_type=sa.BigInteger(), nullable=False)
        batch_op.alter_column('event_sequence', existing_type=sa.Integer(), nullable=False)
        batch_op.create_check_constraint(
            'ck_game_events_occurred_at_nonnegative', 'occurred_at >= 0'
        )
        batch_op.create_unique_constraint(
            'uq_game_events_session_sequence', ['session_id', 'event_sequence']
        )
        batch_op.create_index(
            'ix_game_events_session_time', ['session_id', 'occurred_at', 'event_sequence']
        )

    op.create_table(
        'scheduled_events',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('session_id', sa.Uuid(), nullable=False),
        sa.Column('due_at', sa.BigInteger(), nullable=False),
        sa.Column('type', sa.String(length=80), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('interrupt_player_action', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('due_at >= 0', name='ck_scheduled_events_due_at_nonnegative'),
        sa.ForeignKeyConstraint(['session_id'], ['game_sessions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_scheduled_events_session_id', 'scheduled_events', ['session_id'], unique=False
    )
    op.create_index(
        'ix_scheduled_events_pending',
        'scheduled_events',
        ['session_id', 'status', 'due_at'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_scheduled_events_pending', table_name='scheduled_events')
    op.drop_index('ix_scheduled_events_session_id', table_name='scheduled_events')
    op.drop_table('scheduled_events')

    with op.batch_alter_table('game_events', schema=None) as batch_op:
        batch_op.drop_index('ix_game_events_session_time')
        batch_op.drop_constraint('uq_game_events_session_sequence', type_='unique')
        batch_op.drop_constraint('ck_game_events_occurred_at_nonnegative', type_='check')
        batch_op.drop_column('event_sequence')
        batch_op.drop_column('occurred_at')

    with op.batch_alter_table('game_sessions', schema=None) as batch_op:
        batch_op.drop_constraint('ck_game_sessions_elapsed_nonnegative', type_='check')
        batch_op.drop_column('elapsed_minutes')

    with op.batch_alter_table('worlds', schema=None) as batch_op:
        batch_op.drop_column('initial_datetime')
