"""Creates the demo world if it is not already present.

    uv run python -m app.scripts.seed_demo

Idempotent: re-running it does nothing once the world exists. The content exists
to exercise the architecture, not to be good lore.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TypedDict

from sqlalchemy import select

from app.config import get_settings
from app.domain.enums import Language
from app.domain.world_rules import WorldRulesPreset, build_preset
from app.infrastructure.db import models
from app.infrastructure.db.engine import create_engine, create_session_factory, session_scope

logger = logging.getLogger(__name__)

DEMO_WORLD_NAME = "The Fractured Crown"

# Written explicitly rather than left to the column default, so the seed exercises the
# same path the API uses. Shonen suits the genre string and keeps the demo playable --
# constant trouble, rarely fatal -- and it makes the danger/lethality split visible in
# the one world people actually run: danger 75 against lethality 30.
DEMO_WORLD_PRESET = WorldRulesPreset.SHONEN


class SeedCharacter(TypedDict):
    """Shape of the literals below.

    Without it the dict values widen to `str | list[str]`, and joining the names
    stops type-checking -- the one error mypy found when it was first run.
    """

    name: str
    description: str
    appearance: str
    personality: str
    backstory: str
    speech_style: str
    goals: list[str]
    secrets: list[str]


CHARACTERS: list[SeedCharacter] = [
    {
        "name": "Elena",
        "description": "A court mage who reads people faster than she reads books.",
        "appearance": (
            "Dark hair pinned with a silver clasp; ink-stained fingers; travel-worn coat."
        ),
        "personality": "intelligent, sarcastic, cautious",
        "backstory": (
            "Trained at the Academy and dismissed from it. She stayed in the capital "
            "when everyone with sense left."
        ),
        "speech_style": "Dry, precise, allergic to sentiment. Answers questions with questions.",
        "goals": [
            "Find out who is hollowing out the wards around the city",
            "Avoid owing anyone a favour",
        ],
        "secrets": [
            "She helped design the wards that are now failing",
            "She has met the player before, briefly, and remembers it",
        ],
    },
    {
        "name": "Kael",
        "description": "A swordsman who solves problems in the order he trips over them.",
        "appearance": "Broad-shouldered, sunburnt, a chipped blade he refuses to replace.",
        "personality": "outgoing, reckless, loyal",
        "backstory": (
            "Came to the capital chasing a mercenary contract that did not exist. "
            "Stayed because he liked the bread."
        ),
        "speech_style": "Loud, warm, blunt. Jokes when nervous, which is often.",
        "goals": ["Find work worth doing", "Keep the people he likes alive"],
        "secrets": ["He is being hunted for a debt he did not incur"],
    },
]


async def seed() -> None:
    settings = get_settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)

    try:
        async with session_scope(factory) as db:
            existing = (
                await db.execute(select(models.World).where(models.World.name == DEMO_WORLD_NAME))
            ).scalar_one_or_none()

            if existing is not None:
                logger.info("Demo world already present (id=%s). Nothing to do.", existing.id)
                print(f"Demo world already exists: {existing.id}")
                return

            world = models.World(
                name=DEMO_WORLD_NAME,
                genre="Fantasy / anime / adventure",
                setting="A walled capital where the wards are failing and nobody will say why.",
                description=(
                    "The player wakes in a city on the edge of a political and magical "
                    "crisis. The crown is contested, the wards are thinning, and everyone "
                    "in the capital is deciding which side they were always on."
                ),
                language=Language.EN,
                rules_json=build_preset(DEMO_WORLD_PRESET).model_dump(mode="json"),
            )
            db.add(world)
            await db.flush()

            for data in CHARACTERS:
                db.add(models.Character(world_id=world.id, **data))
            await db.flush()

            print(f"Created demo world: {world.id}")
            print(f"Rules: {DEMO_WORLD_PRESET.value}")
            print(f"Characters: {', '.join(c['name'] for c in CHARACTERS)}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(seed())
