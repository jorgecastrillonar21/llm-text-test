"""Creates the demo world if it is not already present.

    uv run python -m app.scripts.seed_demo

Idempotent: re-running it does nothing once the world exists. The content exists
to exercise the architecture, not to be good lore.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.domain.enums import Language
from app.domain.world_facts import WORLD_SUBJECT, FactSubject, FactSubjectType, SetFact
from app.domain.world_locations import (
    ConnectionCategory,
    LocationCategory,
    LocationScale,
)
from app.domain.world_rules import WorldRulesPreset, build_preset
from app.domain.world_time import FictionalDateTime
from app.infrastructure.db import models
from app.infrastructure.db.engine import create_engine, create_session_factory, session_scope

logger = logging.getLogger(__name__)

DEMO_WORLD_NAME = "The Fractured Crown"

# Written explicitly rather than left to the column default, so the seed exercises the
# same path the API uses. Shonen suits the genre string and keeps the demo playable --
# constant trouble, rarely fatal -- and it makes the danger/lethality split visible in
# the one world people actually run: danger 75 against lethality 30.
DEMO_WORLD_PRESET = WorldRulesPreset.SHONEN
DEMO_WORLD_START = FictionalDateTime(year=842, month=10, day=3, hour=18, minute=20)


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


def _initial_facts(elena_id: uuid.UUID, kael_id: uuid.UUID) -> list[SetFact]:
    """What is already true in the demo world before anyone plays it.

    Deliberately small, and deliberately the two kinds: facts about the world that the
    director must not contradict, and quiet character detail it may build on. Nothing
    mechanical -- `system.alive` is not seeded, because a fact's absence is not the
    same as false and "nothing has happened to Elena yet" is exactly the absent case.
    """
    return [
        SetFact(
            subject=WORLD_SUBJECT,
            property="world.political_status",
            value="contested; the crown has no undisputed heir",
            importance=4,
        ),
        SetFact(
            subject=WORLD_SUBJECT,
            property="world.condition",
            value="the wards around the capital are thinning",
            importance=4,
        ),
        SetFact(
            subject=FactSubject(type=FactSubjectType.CHARACTER, id=elena_id),
            property="narrative.birthplace",
            value="the capital's lower district",
            importance=1,
        ),
        SetFact(
            subject=FactSubject(type=FactSubjectType.CHARACTER, id=kael_id),
            property="narrative.birthplace",
            value="a farming village eight days south",
            importance=1,
        ),
    ]


DEMO_START_LOCATION = "The Broken Crown"
"""Where a demo session begins. Matches `SessionCreate.current_location` exactly, which
is how the scene finds its place until CharacterPosition exists -- see
`app.application.spatial_context.resolve_scene_location`."""


async def _seed_geography(db: AsyncSession, world_id: uuid.UUID) -> list[str]:
    """A small template graph: a district, three places in it, and the ways between.

    Deliberately shallow and deliberately incomplete. It exists to exercise the model
    -- containment, a one-way drop, a blocked crossing a session can later reopen, a
    tavern with zones -- not to be a map of a city. Lazy granularity is the rule: the
    cellar is a location because it can be entered and can hold state; the fireplace is
    a zone because it is somewhere to stand.

    Written directly rather than through the API for the same reason the facts are:
    world, characters and geography go in as one transaction, and the authoring
    endpoints create a world before it has anywhere in it.
    """
    quarter = models.LocationDefinition(
        world_id=world_id,
        name="The Lantern Quarter",
        description="Narrow streets under failing wardlight, north of the palace approach.",
        category=LocationCategory.AREA,
        subtype="city_district",
        scale=LocationScale.DISTRICT,
        importance=4,
    )
    db.add(quarter)
    await db.flush()

    tavern = models.LocationDefinition(
        world_id=world_id,
        name=DEMO_START_LOCATION,
        description="A tavern named for a joke nobody finds funny this year.",
        category=LocationCategory.STRUCTURE,
        subtype="tavern",
        scale=LocationScale.BUILDING,
        parent_location_id=quarter.id,
        importance=4,
    )
    street = models.LocationDefinition(
        world_id=world_id,
        name="Market Street",
        description="Half the stalls are shuttered; the other half are pretending not to be.",
        category=LocationCategory.TRANSIT,
        subtype="street",
        scale=LocationScale.SITE,
        parent_location_id=quarter.id,
        importance=3,
    )
    db.add_all([tavern, street])
    await db.flush()

    # A location rather than a zone: it can be entered, it can be flooded or sealed, and
    # a scene can happen in it. The fireplace below is a zone, because it is somewhere
    # to stand. See docs/world-state-locations.md.
    cellar = models.LocationDefinition(
        world_id=world_id,
        name="The Broken Crown cellar",
        description="Cold, dry, and further under the street than it has any right to be.",
        category=LocationCategory.INTERIOR,
        subtype="cellar",
        scale=LocationScale.ROOM,
        parent_location_id=tavern.id,
        importance=2,
    )
    db.add(cellar)
    await db.flush()

    db.add_all(
        [
            models.LocationZone(
                location_id=tavern.id, name="the bar", category="counter", importance=3
            ),
            models.LocationZone(
                location_id=tavern.id, name="the fireplace", category="seating", importance=2
            ),
            models.LocationZone(
                location_id=tavern.id, name="the back tables", category="seating", importance=2
            ),
        ]
    )

    db.add_all(
        [
            # Containment does not imply a route, so the door is written down. Without
            # this row the tavern would be inside the quarter and unreachable from it.
            models.LocationConnection(
                world_id=world_id,
                from_location_id=street.id,
                to_location_id=tavern.id,
                bidirectional=True,
                category=ConnectionCategory.PASSAGE,
                subtype="door",
                base_travel_minutes=0,
                importance=4,
            ),
            models.LocationConnection(
                world_id=world_id,
                from_location_id=tavern.id,
                to_location_id=cellar.id,
                bidirectional=True,
                category=ConnectionCategory.VERTICAL,
                subtype="stairs",
                base_travel_minutes=1,
                importance=2,
            ),
            # One-way on purpose, so the demo world contains something the model must
            # not quietly reverse: you can drop into the cellar from the street, and you
            # cannot climb back out that way.
            models.LocationConnection(
                world_id=world_id,
                from_location_id=street.id,
                to_location_id=cellar.id,
                bidirectional=False,
                category=ConnectionCategory.VERTICAL,
                subtype="coal_chute",
                base_travel_minutes=1,
                importance=1,
            ),
        ]
    )
    await db.flush()
    return [quarter.name, street.name, tavern.name, cellar.name]


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
                # An evening in autumn, so a new session starts somewhere with a mood
                # rather than at the default first morning of year one.
                initial_datetime=DEMO_WORLD_START.model_dump(mode="json"),
            )
            db.add(world)
            await db.flush()

            characters = {
                data["name"]: models.Character(world_id=world.id, **data) for data in CHARACTERS
            }
            db.add_all(characters.values())
            await db.flush()

            # Written after the characters exist, because a template fact about Elena
            # needs her id and there is deliberately no way to name a subject any other
            # way. Every session made from this world starts with copies of these and
            # diverges from there; nothing a session does writes back here.
            world.initial_facts = [
                fact.model_dump(mode="json")
                for fact in _initial_facts(characters["Elena"].id, characters["Kael"].id)
            ]
            await db.flush()

            places = await _seed_geography(db, world.id)

            print(f"Created demo world: {world.id}")
            print(f"Rules: {DEMO_WORLD_PRESET.value}")
            print(f"Characters: {', '.join(c['name'] for c in CHARACTERS)}")
            print(f"Initial facts: {len(world.initial_facts)}")
            print(f"Locations: {', '.join(places)}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(seed())
