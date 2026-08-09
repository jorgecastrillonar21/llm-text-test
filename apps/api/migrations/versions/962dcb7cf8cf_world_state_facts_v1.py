"""world state facts v1

Revision ID: 962dcb7cf8cf
Revises: c4f1ab6d5e73
Create Date: 2026-08-09 00:10:06.838110

Gives every session a store of current objective truth, and a counter that moves when
that truth changes.

    world_facts                     one current value per subject and property
    game_sessions.state_revision    bumped once per committed mutation batch
    worlds.initial_facts            the template facts a new session materialises

Existing rows get `state_revision = 0` and `initial_facts = []`: a save written before
this migration has no recorded state changes and no template, and both of those are
true statements rather than invented ones. Sessions created before today therefore
start with an empty fact store, which is also true -- nothing had established anything.

The two unique indexes on `world_facts` are partial, split on whether `subject_id` is
null. That is not a micro-optimisation: a single index including the nullable column
would not constrain world-scoped facts at all, because SQL considers two NULLs
distinct. See the WorldFact model docstring.

Every added column is created nullable, backfilled, then made NOT NULL, so the order
is safe on a database that already has rows.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# Available to every migration: models use app.infrastructure.db.types.UtcDateTime.
import app.infrastructure.db.types


revision: str = '962dcb7cf8cf'
down_revision: str | None = 'c4f1ab6d5e73'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'world_facts',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('session_id', sa.Uuid(), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('subject_type', sa.String(length=20), nullable=False),
        sa.Column('subject_id', sa.Uuid(), nullable=True),
        sa.Column('property', sa.String(length=120), nullable=False),
        sa.Column('value', sa.JSON(), nullable=False),
        sa.Column('importance', sa.Integer(), nullable=False),
        sa.Column('current_value_since', sa.BigInteger(), nullable=False),
        sa.Column('authority', sa.String(length=30), nullable=False),
        sa.Column('source_event_id', sa.Uuid(), nullable=True),
        sa.Column('tags', sa.JSON(), nullable=False),
        sa.Column('created_at', app.infrastructure.db.types.UtcDateTime(), nullable=False),
        sa.Column('updated_at', app.infrastructure.db.types.UtcDateTime(), nullable=False),
        sa.CheckConstraint(
            'current_value_since >= 0', name='ck_world_facts_current_value_since_nonnegative'
        ),
        sa.CheckConstraint('importance BETWEEN 1 AND 5', name='ck_world_facts_importance_range'),
        sa.ForeignKeyConstraint(['session_id'], ['game_sessions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['source_event_id'], ['game_events.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_world_facts_session_id', 'world_facts', ['session_id'], unique=False)
    op.create_index(
        'ix_world_facts_session_subject',
        'world_facts',
        ['session_id', 'subject_type', 'subject_id'],
        unique=False,
    )
    # One current value per logical fact. Two indexes, because `subject_id IS NULL`
    # marks the world-scoped facts and NULL never equals NULL in a unique index.
    op.create_index(
        'uq_world_facts_entity_property',
        'world_facts',
        ['session_id', 'subject_type', 'subject_id', 'property'],
        unique=True,
        sqlite_where=sa.text('subject_id IS NOT NULL'),
        postgresql_where=sa.text('subject_id IS NOT NULL'),
    )
    op.create_index(
        'uq_world_facts_world_property',
        'world_facts',
        ['session_id', 'subject_type', 'property'],
        unique=True,
        sqlite_where=sa.text('subject_id IS NULL'),
        postgresql_where=sa.text('subject_id IS NULL'),
    )

    with op.batch_alter_table('game_sessions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('state_revision', sa.BigInteger(), nullable=True))

    op.execute('UPDATE game_sessions SET state_revision = 0 WHERE state_revision IS NULL')

    with op.batch_alter_table('game_sessions', schema=None) as batch_op:
        batch_op.alter_column('state_revision', existing_type=sa.BigInteger(), nullable=False)
        batch_op.create_check_constraint(
            'ck_game_sessions_revision_nonnegative', 'state_revision >= 0'
        )

    with op.batch_alter_table('worlds', schema=None) as batch_op:
        batch_op.add_column(sa.Column('initial_facts', sa.JSON(), nullable=True))

    op.execute("UPDATE worlds SET initial_facts = '[]' WHERE initial_facts IS NULL")

    with op.batch_alter_table('worlds', schema=None) as batch_op:
        batch_op.alter_column('initial_facts', existing_type=sa.JSON(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table('worlds', schema=None) as batch_op:
        batch_op.drop_column('initial_facts')

    with op.batch_alter_table('game_sessions', schema=None) as batch_op:
        batch_op.drop_constraint('ck_game_sessions_revision_nonnegative', type_='check')
        batch_op.drop_column('state_revision')

    op.drop_index('uq_world_facts_world_property', table_name='world_facts')
    op.drop_index('uq_world_facts_entity_property', table_name='world_facts')
    op.drop_index('ix_world_facts_session_subject', table_name='world_facts')
    op.drop_index('ix_world_facts_session_id', table_name='world_facts')
    op.drop_table('world_facts')
