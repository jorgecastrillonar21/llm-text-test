"""A world's rules constrain what can become true in it.

WorldRules says what kind of universe this is. Facts say what is currently so. When a
mutation would make the second contradict the first, the rules win -- that is what
"the rules are authoritative" has to mean if it means anything.

# Structural checks only

Every check here reads a canonical property and a typed value. None of them reads
prose. "Elena is secretly immortal because of magic" is not caught by understanding
the sentence; it is caught because establishing it requires either
`narrative.supernatural_nature` or a `supernatural` tag, and a world with the
supernatural switched off rejects both.

That is the whole design: make the consequential claims *nameable*, then guard the
names. Detecting the same claim smuggled through free text is a semantic problem, it
needs a language model to adjudicate, and a model that can be talked out of a rule is
not an enforcement mechanism. See docs/world-state-facts.md.

# No authority is exempt

These checks run for every mutation regardless of who asked, including `admin`. An
exemption would be the first crack: the point of a rule that resurrection is
impossible is that nothing in the system can quietly make a dead character alive
again. Changing what a world permits means changing its rules, which is a decision
about the world rather than a decision about one fact.
"""

from __future__ import annotations

from app.domain.errors import IncompatibleFactError
from app.domain.world_facts.facts import FactSubjectType, WorldFact
from app.domain.world_facts.mutations import RemoveFact, SetFact
from app.domain.world_facts.policy import SYSTEM_ALIVE, definition_for
from app.domain.world_rules.enums import DeathFinality
from app.domain.world_rules.rules import WorldRulesV1

SUPERNATURAL_TAG = "supernatural"
"""Marks a fact as making a supernatural claim. Checked against the world's rules, so
tagging one in a mundane world is a refusal rather than a label."""


def check_rules_compatibility(
    rules: WorldRulesV1,
    *,
    mutation: SetFact | RemoveFact,
    current: WorldFact | None,
) -> None:
    """Raise `IncompatibleFactError` if this world does not permit this change.

    `current` is the fact as stored, or None when nothing is established. The
    difference matters: a value moving from false to true is a transition the rules
    may forbid, while establishing a value for the first time usually is not.
    """
    if isinstance(mutation, RemoveFact):
        # Withdrawing a property makes no claim about the world, so no rule can be
        # broken by it. Removing `system.alive` is not a resurrection; it is the game
        # declining to say either way.
        return

    _check_supernatural(rules, mutation)
    _check_resurrection(rules, mutation, current)
    _check_npc_death(rules, mutation, current)


def _check_supernatural(rules: WorldRulesV1, mutation: SetFact) -> None:
    if rules.supernatural.enabled:
        return

    definition = definition_for(mutation.property)
    if definition is not None and definition.requires_supernatural:
        raise IncompatibleFactError(
            f"{mutation.property!r} describes something supernatural, and this world has "
            "supernatural.enabled = false. Nothing supernatural exists here."
        )
    if SUPERNATURAL_TAG in mutation.tags:
        raise IncompatibleFactError(
            f"The fact {mutation.property!r} is tagged {SUPERNATURAL_TAG!r}, and this world "
            "has supernatural.enabled = false. Nothing supernatural exists here."
        )


def _check_resurrection(rules: WorldRulesV1, mutation: SetFact, current: WorldFact | None) -> None:
    """Nobody comes back in a world where coming back is impossible.

    Only the transition is refused. Establishing that someone is alive when the world
    has never said otherwise is ordinary -- the game simply had not recorded it yet.
    """
    if mutation.property != SYSTEM_ALIVE or mutation.value is not True:
        return
    if current is None or current.value is not False:
        return
    if rules.mortality.death_finality is DeathFinality.RESURRECTION_POSSIBLE:
        return

    raise IncompatibleFactError(
        f"{mutation.subject.key} is established as dead, and this world's death_finality is "
        f"{rules.mortality.death_finality.value!r}. The dead do not return here."
    )


def _check_npc_death(rules: WorldRulesV1, mutation: SetFact, current: WorldFact | None) -> None:
    """A world where NPCs cannot die does not get to kill one through the fact store.

    Only character subjects are checked. The player is not an entity in this table --
    they are the session's player, not a row -- so `mortality.player_death` has
    nothing here to apply to yet.
    """
    if mutation.property != SYSTEM_ALIVE or mutation.value is not False:
        return
    if mutation.subject.type is not FactSubjectType.CHARACTER:
        return
    if current is not None and current.value is False:
        return  # Already dead; recording it again changes nothing.
    if rules.mortality.npc_death:
        return

    raise IncompatibleFactError(
        f"{mutation.subject.key} cannot be established as dead: this world has "
        "mortality.npc_death = false."
    )
