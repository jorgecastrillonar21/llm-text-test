"""What the Story Director may and may not establish.

The model proposes; this is the gauntlet. A rejected proposal is never an error -- the
turn continues and the player sees nothing -- so every test here asserts two things:
that the proposal did not take effect, and that nothing else broke because of it.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.contracts import MAX_FACT_PROPOSALS, FactProposal, TurnGeneration
from app.application.fact_proposals import ProposalOutcome, review_fact_proposals
from app.application.state_service import apply_state_change
from app.domain.state_mutations import StateMutationBatch
from app.domain.world_facts import (
    WORLD_SUBJECT,
    FactAuthority,
    FactKind,
    FactSubject,
    FactSubjectType,
    SetFact,
)
from app.domain.world_rules import default_world_rules
from app.infrastructure.db import models
from app.infrastructure.db.turn_gateway import SqlAlchemyTurnGateway
from tests.support import cause_from_resolution


async def _bootstrap(
    db: AsyncSession, make_world, make_character
) -> tuple[SqlAlchemyTurnGateway, models.World, models.Character, models.GameSession]:
    world = make_world()
    db.add(world)
    await db.flush()
    character = make_character(world.id)
    db.add(character)
    session = models.GameSession(world_id=world.id, title="Run", player_name="Rin")
    db.add(session)
    await db.flush()
    return SqlAlchemyTurnGateway(db), world, character, session


async def _review(store, session, world, proposals, character_ids):
    snapshot = await store.get_world(world.id)
    assert snapshot is not None
    return await review_fact_proposals(
        store,
        session_id=session.id,
        world=snapshot,
        proposals=proposals,
        known_character_ids=character_ids,
    )


# -- the contract -------------------------------------------------------------


def test_a_turn_with_no_proposals_is_normal() -> None:
    """Optional, unlike `suggested_actions`: the right number for most turns is zero,
    and a required field is one a constrained model will invent something to fill."""
    generation = TurnGeneration.model_validate(
        {"narration": "The door opens.", "suggested_actions": ["Go in"]}
    )
    assert generation.fact_proposals == []


def test_one_malformed_proposal_does_not_cost_the_turn_its_prose() -> None:
    """A 502 over a detail the model was not obliged to send at all would roll back a
    perfectly good turn."""
    generation = TurnGeneration.model_validate(
        {
            "narration": "Elena grimaces.",
            "suggested_actions": ["Ask again"],
            "fact_proposals": [
                {"subject_type": "not_a_subject", "property": "narrative.birthplace", "value": "X"},
                {"subject_type": "world", "property": "narrative.birthplace", "value": "Arven"},
            ],
        }
    )
    assert generation.narration == "Elena grimaces."
    assert [p.property for p in generation.fact_proposals] == ["narrative.birthplace"]


def test_a_runaway_model_is_truncated_rather_than_read() -> None:
    generation = TurnGeneration.model_validate(
        {
            "narration": "n",
            "suggested_actions": [],
            "fact_proposals": [
                {"subject_type": "world", "property": f"narrative.thing_{i}", "value": i}
                for i in range(20)
            ],
        }
    )
    assert len(generation.fact_proposals) == MAX_FACT_PROPOSALS


# -- review -------------------------------------------------------------------


async def test_an_open_narrative_detail_is_accepted(
    db_session: AsyncSession, make_world, make_character
) -> None:
    store, world, character, session = await _bootstrap(db_session, make_world, make_character)

    review = await _review(
        store,
        session,
        world,
        [
            FactProposal(
                subject_type=FactSubjectType.CHARACTER,
                subject_id=character.id,
                property="narrative.dislikes_food",
                value=["olives"],
                importance=1,
                reason="She said so.",
            )
        ],
        {character.id},
    )

    assert [p.outcome for p in review.reviewed] == [ProposalOutcome.ACCEPTED]
    assert len(review.accepted) == 1
    assert review.accepted[0].property == "narrative.dislikes_food"
    # Narration establishes diegetic truth, never a progression flag -- the kind is
    # decided by the reviewer, not proposed.
    assert review.accepted[0].kind is FactKind.WORLD_TRUTH


async def test_a_system_property_is_refused(
    db_session: AsyncSession, make_world, make_character
) -> None:
    """Whether someone is alive is not narration's to decide, however the scene reads."""
    store, world, character, session = await _bootstrap(db_session, make_world, make_character)

    review = await _review(
        store,
        session,
        world,
        [
            FactProposal(
                subject_type=FactSubjectType.CHARACTER,
                subject_id=character.id,
                property="system.alive",
                value=False,
                importance=5,
            )
        ],
        {character.id},
    )

    assert review.accepted == []
    assert review.reviewed[0].outcome is ProposalOutcome.REJECTED


async def test_a_guarded_world_property_is_refused(
    db_session: AsyncSession, make_world, make_character
) -> None:
    store, world, _, session = await _bootstrap(db_session, make_world, make_character)

    review = await _review(
        store,
        session,
        world,
        [
            FactProposal(
                subject_type=FactSubjectType.WORLD,
                property="world.political_status",
                value="fallen",
            )
        ],
        set(),
    )

    assert review.accepted == []


async def test_an_unregistered_name_does_not_grant_open_authority_by_inventing_it(
    db_session: AsyncSession, make_world, make_character
) -> None:
    """`world.anything` falls back to GUARDED, so a new name buys nothing."""
    store, world, _, session = await _bootstrap(db_session, make_world, make_character)

    review = await _review(
        store,
        session,
        world,
        [
            FactProposal(
                subject_type=FactSubjectType.WORLD, property="world.who_rules_here", value="nobody"
            )
        ],
        set(),
    )

    assert review.accepted == []


async def test_a_proposal_about_a_character_the_world_does_not_have_is_refused(
    db_session: AsyncSession, make_world, make_character
) -> None:
    store, world, _, session = await _bootstrap(db_session, make_world, make_character)

    review = await _review(
        store,
        session,
        world,
        [
            FactProposal(
                subject_type=FactSubjectType.CHARACTER,
                subject_id=uuid.uuid4(),
                property="narrative.birthplace",
                value="Arven",
            )
        ],
        set(),
    )

    assert review.accepted == []


async def test_a_proposal_about_a_location_is_refused_because_nothing_can_check_it(
    db_session: AsyncSession, make_world, make_character
) -> None:
    store, world, _, session = await _bootstrap(db_session, make_world, make_character)

    review = await _review(
        store,
        session,
        world,
        [
            FactProposal(
                subject_type=FactSubjectType.LOCATION,
                subject_id=uuid.uuid4(),
                property="narrative.childhood_nickname",
                value="the pit",
            )
        ],
        set(),
    )

    assert review.accepted == []


async def test_a_proposal_that_contradicts_established_truth_is_refused(
    db_session: AsyncSession, make_world, make_character
) -> None:
    """The structural contradiction check: same subject, same property, different
    value. Not a judgement about which is better -- the story already committed."""
    store, world, character, session = await _bootstrap(db_session, make_world, make_character)
    subject = FactSubject(type=FactSubjectType.CHARACTER, id=character.id)
    await apply_state_change(
        store,
        session_id=session.id,
        batch=StateMutationBatch(
            authority=FactAuthority.SEED,
            mutations=[SetFact(subject=subject, property="narrative.birthplace", value="Arven")],
        ),
        cause=cause_from_resolution(),
    )

    review = await _review(
        store,
        session,
        world,
        [
            FactProposal(
                subject_type=FactSubjectType.CHARACTER,
                subject_id=character.id,
                property="narrative.birthplace",
                value="Valeria",
            )
        ],
        {character.id},
    )

    assert review.accepted == []
    assert "Arven" in review.reviewed[0].reason
    stored = await store.get_fact(session.id, subject, "narrative.birthplace")
    assert stored is not None
    assert stored.value == "Arven"


async def test_repeating_an_established_value_is_refused_too(
    db_session: AsyncSession, make_world, make_character
) -> None:
    """It would move the revision without moving the world, and a revision that
    changes when nothing changed is worse than useless."""
    store, world, character, session = await _bootstrap(db_session, make_world, make_character)
    subject = FactSubject(type=FactSubjectType.CHARACTER, id=character.id)
    await apply_state_change(
        store,
        session_id=session.id,
        batch=StateMutationBatch(
            authority=FactAuthority.SEED,
            mutations=[SetFact(subject=subject, property="narrative.birthplace", value="Arven")],
        ),
        cause=cause_from_resolution(),
    )

    review = await _review(
        store,
        session,
        world,
        [
            FactProposal(
                subject_type=FactSubjectType.CHARACTER,
                subject_id=character.id,
                property="narrative.birthplace",
                value="Arven",
            )
        ],
        {character.id},
    )

    assert review.accepted == []


async def test_two_proposals_for_one_property_cost_the_second_not_the_turn(
    db_session: AsyncSession, make_world, make_character
) -> None:
    store, world, character, session = await _bootstrap(db_session, make_world, make_character)

    review = await _review(
        store,
        session,
        world,
        [
            FactProposal(
                subject_type=FactSubjectType.CHARACTER,
                subject_id=character.id,
                property="narrative.birthplace",
                value="Arven",
            ),
            FactProposal(
                subject_type=FactSubjectType.CHARACTER,
                subject_id=character.id,
                property="narrative.place_of_birth",  # the same property under an alias
                value="Valeria",
            ),
        ],
        {character.id},
    )

    assert [fact.value for fact in review.accepted] == ["Arven"]
    assert review.reviewed[1].outcome is ProposalOutcome.REJECTED


async def test_a_proposal_the_worlds_rules_forbid_is_refused(
    db_session: AsyncSession, make_world, make_character
) -> None:
    base = default_world_rules()
    # Every sub-flag too: the rules document validates its own coherence, and a world
    # with no supernatural that still has supernatural creatures is refused outright.
    mundane = base.model_copy(
        update={
            "supernatural": base.supernatural.model_copy(
                update={
                    "enabled": False,
                    "innate_powers_exist": False,
                    "learnable_powers_exist": False,
                    "supernatural_items_exist": False,
                    "supernatural_creatures_exist": False,
                }
            )
        }
    )
    base_world = make_world(rules_json=mundane.model_dump(mode="json"))
    db_session.add(base_world)
    await db_session.flush()
    character = make_character(base_world.id)
    db_session.add(character)
    session = models.GameSession(world_id=base_world.id, title="Run", player_name="Rin")
    db_session.add(session)
    await db_session.flush()
    store = SqlAlchemyTurnGateway(db_session)

    review = await _review(
        store,
        session,
        base_world,
        [
            FactProposal(
                subject_type=FactSubjectType.CHARACTER,
                subject_id=character.id,
                property="narrative.supernatural_nature",
                value="revenant",
            )
        ],
        {character.id},
    )

    assert review.accepted == []


async def test_the_world_itself_is_a_valid_subject_for_an_open_property(
    db_session: AsyncSession, make_world, make_character
) -> None:
    store, world, _, session = await _bootstrap(db_session, make_world, make_character)

    review = await _review(
        store,
        session,
        world,
        [
            FactProposal(
                subject_type=FactSubjectType.WORLD,
                property="narrative.childhood_nickname",
                value="the Old Quarter",
            )
        ],
        set(),
    )

    assert len(review.accepted) == 1
    assert review.accepted[0].subject == WORLD_SUBJECT
