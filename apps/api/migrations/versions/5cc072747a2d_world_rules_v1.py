"""world rules v1

Revision ID: 5cc072747a2d
Revises: 05f3334fbe04
Create Date: 2026-08-08 15:10:00.000000

Adds `worlds.rules_json`, the full WorldRules document for each world.

Existing worlds are backfilled with the balanced defaults. Those defaults are
written out as a literal below rather than imported from
`app.domain.world_rules`: a migration must keep producing what it produced the day
it ran, and importing the model would silently rewrite history the first time a
preset's numbers change.

The column is added nullable, backfilled, and only then made NOT NULL, so the
statement order is safe on a database that already has rows.
"""
from __future__ import annotations

import json
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '5cc072747a2d'
down_revision: str | None = '05f3334fbe04'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# The balanced WorldRulesV1 document as of this migration. Frozen on purpose.
DEFAULT_RULES = {
    "version": 1,
    "narrative": {
        "optimism": 50,
        "darkness": 50,
        "protagonist_centrality": 40,
        "deus_ex_machina": 5,
        "coincidence_frequency": 20,
        "consequence_persistence": 90,
        "plot_armor": {
            "player": 25,
            "important_npcs": 10,
            "ordinary_npcs": 0,
        },
    },
    "mortality": {
        "player_death": True,
        "npc_death": True,
        "death_finality": "permanent",
        "permanent_injury": True,
        "dismemberment": False,
        "aging": True,
        "incapacitation_before_death": "contextual",
        "resurrection": {
            "enabled": False,
            "rarity": "impossible",
        },
    },
    "danger": {
        "baseline": 50,
        "encounter_frequency": 40,
        "encounter_severity": 50,
        "escalation_rate": 40,
        "environmental_hazards": 30,
        "hostile_actor_frequency": 45,
        "lethality": 40,
        "safe_zones_exist": True,
    },
    "consequences": {
        "severity": 70,
        "persistence": 90,
        "social_memory": 80,
        "legal_response": 70,
        "faction_response": 75,
        "economic_response": 40,
        "collateral_damage_matters": True,
        "actions_can_close_content": True,
        "irreversible_outcomes": True,
    },
    "power": {
        "scale": "superhuman",
        "mundane_ceiling": 10,
        "superhuman_ceiling": 25,
        "legendary_ceiling": 50,
        "power_gap_significance": "high",
        "training_matters": 80,
        "talent_matters": 60,
        "experience_matters": 75,
        "improvisation": 40,
        "rule_breaking": {
            "allowed": "rare",
            "requires_explanation": True,
        },
        "hidden_power": {
            "allowed": True,
        },
        "awakenings": {
            "enabled": True,
            "rarity": "rare",
        },
    },
    "supernatural": {
        "enabled": True,
        "prevalence": "uncommon",
        "public_awareness": "common",
        "regulation": "moderate",
        "discoverability": "high",
        "innate_powers_exist": True,
        "learnable_powers_exist": True,
        "supernatural_items_exist": True,
        "supernatural_creatures_exist": True,
    },
    "progression": {
        "enabled": True,
        "pace": "medium",
        "ceiling": "soft",
        "skill_improvement_from_use": True,
        "training_required": "contextual",
        "breakthroughs": {
            "enabled": True,
            "rarity": "rare",
        },
        "power_loss": {
            "possible": True,
        },
        "regression": {
            "possible": True,
        },
    },
    "society": {
        "law_strength": 60,
        "corruption": 40,
        "inequality": 50,
        "social_mobility": 45,
        "violence_tolerance": 30,
        "institutional_stability": 70,
        "information_spread": "medium",
        "reputation_matters": 75,
        "slavery": {
            "exists": False,
        },
        "discrimination": {
            "intensity": 10,
        },
    },
    "resources": {
        "scarcity": 40,
        "food": {
            "scarcity": 20,
        },
        "medicine": {
            "scarcity": 35,
        },
        "weapons": {
            "scarcity": 30,
        },
        "supernatural_resources": {
            "scarcity": 65,
        },
        "money": {
            "scarcity": 45,
        },
        "healing": {
            "accessibility": 50,
        },
        "recovery": {
            "speed": "medium",
        },
    },
    "simulation": {
        "world_continues_without_player": True,
        "npc_autonomy": 70,
        "faction_autonomy": 60,
        "offscreen_events": True,
        "event_frequency": 40,
        "simulation_scope": "relevant",
        "time_progression": "active",
        "missed_opportunities": True,
    },
    "chance": {
        "model": "seeded",
        "transparency": "hidden",
        "critical_success": True,
        "critical_failure": True,
        "luck_influence": 25,
        "narrative_rerolls": False,
    },
    "rules": {
        "enforcement": "strict",
    },
    "content": {
        "violence": "high",
        "gore": "low",
        "horror": "medium",
        "romance": "enabled",
        "sexual_content": "fade_to_black",
        "substance_use": "contextual",
        "profanity": "medium",
    },
}


def upgrade() -> None:
    with op.batch_alter_table('worlds', schema=None) as batch_op:
        batch_op.add_column(sa.Column('rules_json', sa.JSON(), nullable=True))

    op.execute(
        sa.text('UPDATE worlds SET rules_json = :rules WHERE rules_json IS NULL').bindparams(
            rules=json.dumps(DEFAULT_RULES)
        )
    )

    with op.batch_alter_table('worlds', schema=None) as batch_op:
        batch_op.alter_column('rules_json', existing_type=sa.JSON(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table('worlds', schema=None) as batch_op:
        batch_op.drop_column('rules_json')
