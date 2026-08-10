"""Event / Resolution V1: the mechanical trail, and history that is only history.

Revision ID: 65bea0eded6c
Revises: f429e3bc3d11
Create Date: 2026-08-09 23:00:37.334790

One new table and one reshaped one.

`resolutions` is the mechanical audit trail: every attempt that reached a verdict, with
the revision either side of it and the key that makes retrying it safe. Its unique
`(session_id, idempotency_key)` is not a nicety -- it is the idempotency guarantee. Two
concurrent submissions of one player action cannot both get past it, which a
`SELECT`-then-`INSERT` in the application could never promise on its own.

`game_events` becomes what it was always described as: significant world history. It
gains a category, an open subtype, a clamped importance, the resolution that produced
it, where it happened, what it followed from, and a small structured payload.

# The rename, and why the data survives it

    type        -> subtype
    description -> summary

`type` held dotted pseudo-categories -- `time.advanced`, `world_state.seeded` -- because
there was no category column to put that half in. Now there is, so the two halves
separate and the columns are renamed to say which is which. Leaving a column called
`type` next to one called `category` would leave the old convention looking
authoritative, and the next person would extend it.

Existing rows are carried across, not dropped:

    subtype  = the old type, lowercased with '.', ' ' and '-' folded to '_'
    summary  = the old description
    category = 'system' for the engine's own rows, 'other' for everything else

'other' rather than a guess. Inferring a category from a free-text string would work for
`bridge_collapsed` and quietly miscategorise everything a world invented, and retrieval
filters on this column -- a wrong category hides an event rather than mislabelling it.

Legacy subtypes are folded, not validated: a pre-existing row whose type contained a
character the current shape rules would reject stays readable, because the read model
constrains length and emptiness rather than spelling. A migration is not the place to
start refusing saves.

# Two batches, on purpose

SQLite cannot add a NOT NULL column without a default, so the new columns arrive
nullable, get backfilled, and are tightened afterwards. The old columns are only dropped
in the second batch -- after their contents have been copied. A single batch would drop
them before the UPDATE could read them.

# Nothing is invented

No resolutions are fabricated for events that predate this boundary. `resolution_id` is
null on every existing row, which is the truth: those events happened before there was a
resolution to attribute them to.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# Available to every migration: models use app.infrastructure.db.types.UtcDateTime.
import app.infrastructure.db.types


revision: str = '65bea0eded6c'
down_revision: str | None = 'f429e3bc3d11'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# The engine's own event types are the only ones whose category is knowable. Everything
# else was free text from a story provider and becomes 'other'.
BACKFILL_EVENTS = """
UPDATE game_events
SET
    subtype = lower(replace(replace(replace(type, '.', '_'), ' ', '_'), '-', '_')),
    summary = description,
    category = CASE
        WHEN type IN ('time.advanced', 'world_state.seeded') THEN 'system'
        WHEN type LIKE 'situation.%' THEN 'situation'
        ELSE 'other'
    END
"""

RESTORE_EVENTS = """
UPDATE game_events
SET
    type = subtype,
    description = summary
"""


def upgrade() -> None:
    op.create_table('resolutions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('session_id', sa.Uuid(), nullable=False),
    sa.Column('source_type', sa.String(length=30), nullable=False),
    sa.Column('source_id', sa.Uuid(), nullable=True),
    sa.Column('parent_resolution_id', sa.Uuid(), nullable=True),
    sa.Column('idempotency_key', sa.String(length=200), nullable=False),
    sa.Column('disposition', sa.String(length=20), nullable=False),
    sa.Column('reason_code', sa.String(length=60), nullable=True),
    sa.Column('resolver_name', sa.String(length=80), nullable=False),
    sa.Column('resolver_version', sa.String(length=20), nullable=False),
    sa.Column('state_revision_before', sa.BigInteger(), nullable=False),
    sa.Column('state_revision_after', sa.BigInteger(), nullable=False),
    sa.Column('occurred_at', sa.BigInteger(), nullable=False),
    sa.Column('turn_index', sa.Integer(), nullable=True),
    sa.Column('event_count', sa.Integer(), nullable=False),
    sa.Column('mutation_count', sa.Integer(), nullable=False),
    sa.Column('created_at', app.infrastructure.db.types.UtcDateTime(), nullable=False),
    sa.CheckConstraint("disposition <> 'rejected' OR reason_code IS NOT NULL", name='ck_resolutions_rejection_has_a_reason'),
    sa.CheckConstraint("disposition = 'applied' OR (state_revision_after = state_revision_before AND event_count = 0 AND mutation_count = 0)", name='ck_resolutions_only_applied_changes_anything'),
    sa.CheckConstraint('event_count >= 0 AND mutation_count >= 0', name='ck_resolutions_counts_nonnegative'),
    sa.CheckConstraint('occurred_at >= 0', name='ck_resolutions_occurred_at_nonnegative'),
    sa.CheckConstraint('state_revision_before >= 0 AND state_revision_after >= state_revision_before', name='ck_resolutions_revision_monotonic'),
    sa.ForeignKeyConstraint(['parent_resolution_id'], ['resolutions.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['session_id'], ['game_sessions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('session_id', 'idempotency_key', name='uq_resolutions_session_idempotency')
    )
    with op.batch_alter_table('resolutions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_resolutions_parent_resolution_id'), ['parent_resolution_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_resolutions_session_id'), ['session_id'], unique=False)
        batch_op.create_index('ix_resolutions_session_source', ['session_id', 'source_type'], unique=False)
        batch_op.create_index('ix_resolutions_session_time', ['session_id', 'occurred_at', 'created_at'], unique=False)

    # -- game_events, part one: room for the new shape ---------------------------
    #
    # Nullable or defaulted, so existing rows survive the ALTER. `type` and
    # `description` are still here; the UPDATE below reads them.
    with op.batch_alter_table('game_events', schema=None) as batch_op:
        batch_op.add_column(sa.Column('resolution_id', sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column('category', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('subtype', sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column('summary', sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column('importance', sa.Integer(), nullable=False, server_default='2')
        )
        batch_op.add_column(sa.Column('primary_location_id', sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column('caused_by_event_id', sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column('payload', sa.JSON(), nullable=False, server_default='{}'))

    op.execute(BACKFILL_EVENTS)

    # -- game_events, part two: tighten, constrain, and drop the old halves -------
    with op.batch_alter_table('game_events', schema=None) as batch_op:
        batch_op.alter_column('category', existing_type=sa.String(length=20), nullable=False)
        batch_op.alter_column('subtype', existing_type=sa.String(length=80), nullable=False)
        batch_op.alter_column('summary', existing_type=sa.Text(), nullable=False)
        batch_op.create_index(batch_op.f('ix_game_events_resolution_id'), ['resolution_id'], unique=False)
        batch_op.create_index('ix_game_events_session_category', ['session_id', 'category', 'subtype'], unique=False)
        batch_op.create_index('ix_game_events_session_importance', ['session_id', 'importance', 'occurred_at'], unique=False)
        batch_op.create_foreign_key(
            'fk_game_events_caused_by', 'game_events', ['caused_by_event_id'], ['id'], ondelete='SET NULL'
        )
        batch_op.create_foreign_key(
            'fk_game_events_primary_location', 'location_definitions', ['primary_location_id'], ['id'], ondelete='SET NULL'
        )
        batch_op.create_foreign_key(
            'fk_game_events_resolution', 'resolutions', ['resolution_id'], ['id'], ondelete='SET NULL'
        )
        batch_op.create_check_constraint('ck_game_events_importance_range', 'importance BETWEEN 1 AND 5')
        batch_op.create_check_constraint('ck_game_events_not_self_caused', 'id <> caused_by_event_id')
        batch_op.drop_column('type')
        batch_op.drop_column('description')

    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.add_column(sa.Column('resolution_id', sa.Uuid(), nullable=True))
        batch_op.create_index(batch_op.f('ix_messages_resolution_id'), ['resolution_id'], unique=False)
        batch_op.create_foreign_key(
            'fk_messages_resolution', 'resolutions', ['resolution_id'], ['id'], ondelete='SET NULL'
        )


def downgrade() -> None:
    """Put the two halves back into one column and drop the rest.

    Lossy, and honestly so: category, importance, payload, causality and the link to
    the resolution have nowhere to go in the old shape. What is preserved is what the
    old shape could hold -- the subtype becomes the type and the summary becomes the
    description, so a downgraded save still has readable history rather than empty rows.
    """
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_messages_resolution_id'))
        batch_op.drop_column('resolution_id')

    with op.batch_alter_table('game_events', schema=None) as batch_op:
        batch_op.add_column(sa.Column('type', sa.VARCHAR(length=80), nullable=True))
        batch_op.add_column(sa.Column('description', sa.TEXT(), nullable=True))

    op.execute(RESTORE_EVENTS)

    with op.batch_alter_table('game_events', schema=None) as batch_op:
        batch_op.alter_column('type', existing_type=sa.VARCHAR(length=80), nullable=False)
        batch_op.alter_column('description', existing_type=sa.TEXT(), nullable=False)
        batch_op.drop_constraint('ck_game_events_not_self_caused', type_='check')
        batch_op.drop_constraint('ck_game_events_importance_range', type_='check')
        batch_op.drop_index('ix_game_events_session_importance')
        batch_op.drop_index('ix_game_events_session_category')
        batch_op.drop_index(batch_op.f('ix_game_events_resolution_id'))
        batch_op.drop_column('payload')
        batch_op.drop_column('caused_by_event_id')
        batch_op.drop_column('primary_location_id')
        batch_op.drop_column('importance')
        batch_op.drop_column('summary')
        batch_op.drop_column('subtype')
        batch_op.drop_column('category')
        batch_op.drop_column('resolution_id')

    with op.batch_alter_table('resolutions', schema=None) as batch_op:
        batch_op.drop_index('ix_resolutions_session_time')
        batch_op.drop_index('ix_resolutions_session_source')
        batch_op.drop_index(batch_op.f('ix_resolutions_session_id'))
        batch_op.drop_index(batch_op.f('ix_resolutions_parent_resolution_id'))

    op.drop_table('resolutions')
