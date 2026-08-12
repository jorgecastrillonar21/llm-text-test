"""Canonical CharacterPosition V1: where an actor is, by id.

Revision ID: 7c41a9f2b6d3
Revises: 2d9f47c1a8be
Create Date: 2026-08-11 09:41:07.552310

One new table. Nothing is dropped, nothing is rebuilt, and no existing column changes
shape -- `game_sessions.current_location` stays exactly where it is and keeps every
value it had. What changes is what that string *means*: it stops being the answer to
"where is the player?" and becomes legacy presentation, deprecated in the model and in
docs/world-state.md. See `app.domain.character_position`.

# The backfill, and why it reads the old string exactly once

Every existing session gets a position row, because "no row" would leave a save in the
one state the new contract does not have: no answer at all. A session whose
`current_location` names exactly one place it can see gets `at_location` pointing at
that place; every other session gets `unlocated`.

This is the last time in the life of the system that a location name is resolved to an
id. The match is exact, trimmed and case-insensitive, and it refuses ambiguity -- two
places called "Market Street" resolve to nothing rather than to whichever row the query
reached first. Folding is `lower()`, which in SQLite is ASCII-only and therefore
narrower than the Python it replaces; a name it cannot fold confidently simply does not
match, and the session starts `unlocated`. An honest gap is the right outcome here. A
wrong guess would hand a director the exits of somewhere else and call it canon.

# What is deliberately not backfilled

No character gets a position. Characters are world-scoped prose in this build and
nothing has ever recorded where one is; inventing a location for every NPC would be
this migration writing fiction into every save. They are `unlocated` by absence until
something says otherwise, and Character Foundation is what will say it.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# Available to every migration: models use app.infrastructure.db.types.UtcDateTime.
import app.infrastructure.db.types


revision: str = '7c41a9f2b6d3'
down_revision: str | None = '2d9f47c1a8be'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Every session gets a row. `unlocated` first, because it is true of every save until
# the join below proves otherwise, and because it means no session can come out of this
# migration with no position at all.
#
# `lower(hex(randomblob(16)))` is how a uuid gets minted in SQLite, which has no uuid
# function. It matches the 32-character hex form SQLAlchemy's Uuid type stores.
BACKFILL_POSITIONS = """
INSERT INTO character_positions (
    id, session_id, actor_kind, actor_id, kind, created_at, updated_at
)
SELECT lower(hex(randomblob(16))), s.id, 'player', s.id, 'unlocated',
       CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
FROM game_sessions s
"""

# The one and only resolution of the legacy string. Correlated rather than grouped
# because visibility is per session: a session sees template geography plus its own,
# and "unique" has to mean unique *within that*.
_VISIBLE_MATCHES = """
    SELECT {selection}
    FROM location_definitions d
    JOIN game_sessions s ON s.id = character_positions.session_id
    WHERE d.world_id = s.world_id
      AND (d.origin_session_id IS NULL OR d.origin_session_id = s.id)
      AND trim(s.current_location) <> ''
      AND lower(trim(d.name)) = lower(trim(s.current_location))
"""

RESOLVE_LEGACY_LOCATION = f"""
UPDATE character_positions
SET kind = 'at_location',
    location_id = ({_VISIBLE_MATCHES.format(selection='d.id')})
WHERE ({_VISIBLE_MATCHES.format(selection='count(*)')}) = 1
"""


def upgrade() -> None:
    op.create_table('character_positions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('session_id', sa.Uuid(), nullable=False),
    sa.Column('actor_kind', sa.String(length=20), nullable=False),
    sa.Column('actor_id', sa.Uuid(), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('location_id', sa.Uuid(), nullable=True),
    sa.Column('zone_id', sa.Uuid(), nullable=True),
    sa.Column('origin_location_id', sa.Uuid(), nullable=True),
    sa.Column('destination_location_id', sa.Uuid(), nullable=True),
    sa.Column('connection_id', sa.Uuid(), nullable=True),
    sa.Column('departed_at', sa.BigInteger(), nullable=True),
    sa.Column('expected_arrival_at', sa.BigInteger(), nullable=True),
    sa.Column('created_at', app.infrastructure.db.types.UtcDateTime(), nullable=False),
    sa.Column('updated_at', app.infrastructure.db.types.UtcDateTime(), nullable=False),
    sa.CheckConstraint('departed_at IS NULL OR departed_at >= 0', name='ck_character_positions_departed_nonnegative'),
    sa.CheckConstraint('expected_arrival_at IS NULL OR departed_at IS NULL OR expected_arrival_at >= departed_at', name='ck_character_positions_arrival_after_departure'),
    sa.CheckConstraint('origin_location_id IS NULL OR destination_location_id IS NULL OR origin_location_id <> destination_location_id', name='ck_character_positions_transit_distinct_ends'),
    sa.CheckConstraint("kind <> 'at_location' OR location_id IS NOT NULL", name='ck_character_positions_at_location_has_location'),
    sa.CheckConstraint("kind <> 'in_transit' OR (origin_location_id IS NOT NULL AND destination_location_id IS NOT NULL AND connection_id IS NOT NULL AND departed_at IS NOT NULL AND expected_arrival_at IS NOT NULL)", name='ck_character_positions_transit_is_complete'),
    sa.ForeignKeyConstraint(['connection_id'], ['location_connections.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['destination_location_id'], ['location_definitions.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['location_id'], ['location_definitions.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['origin_location_id'], ['location_definitions.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['session_id'], ['game_sessions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['zone_id'], ['location_zones.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('session_id', 'actor_kind', 'actor_id', name='uq_character_positions_actor')
    )
    with op.batch_alter_table('character_positions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_character_positions_session_id'), ['session_id'], unique=False)
        # "Who is here" -- the reverse lookup, and the reason no location keeps an
        # occupants list.
        batch_op.create_index('ix_character_positions_session_location', ['session_id', 'location_id'], unique=False)

    op.execute(BACKFILL_POSITIONS)
    op.execute(RESOLVE_LEGACY_LOCATION)


def downgrade() -> None:
    # `current_location` was never removed, so going back down loses only the ids --
    # every session still has the string it was created with, and the name-matching
    # bridge that read it is what the previous revision's code contains.
    with op.batch_alter_table('character_positions', schema=None) as batch_op:
        batch_op.drop_index('ix_character_positions_session_location')
        batch_op.drop_index(batch_op.f('ix_character_positions_session_id'))

    op.drop_table('character_positions')
