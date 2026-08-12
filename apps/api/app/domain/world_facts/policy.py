"""Who is allowed to establish what.

Every property resolves to exactly one policy:

    OPEN      narrative colour. Anyone may establish it, including the Story Director.
    GUARDED   consequential. Proposals need review that does not exist yet.
    SYSTEM    mechanical. Only deterministic game systems may write it.
    DERIVED   computed from other state. Nobody may store it at all.

# Why unknown properties are GUARDED and not OPEN

A property nobody has registered is, by definition, one nobody has thought about.
Defaulting it to OPEN would mean the first time a language model invents a property
name, it also grants itself permission to write it -- and the most valuable properties
to invent are exactly the ones a game would otherwise control. Defaulting to GUARDED
makes the failure "this needs a decision" rather than "this quietly worked".

The cost is that a genuinely harmless new narrative property must be spelled inside
the `narrative` namespace to be freely writable. That is a small, teachable rule, and
it is stated in the Story Director prompt.

# Importance is not a permission

`importance` is 1..5 and it steers context selection, summarisation and debugging.
It appears nowhere in this module. A fact does not become more or less writable by
being labelled important, and nothing here may ever read it.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.domain.world_facts.properties import PropertyNamespace, namespace_of


class FactPolicy(StrEnum):
    OPEN = "open"
    GUARDED = "guarded"
    SYSTEM = "system"
    DERIVED = "derived"
    DEDICATED = "dedicated"
    """A dedicated model already owns this. Nobody writes it here.

    Distinct from DERIVED, which is computed and stored nowhere. A location's condition
    *is* stored -- in `location_states`, with a closed vocabulary and a constraint --
    and a fact claiming the same thing would be a second answer to a settled question.
    """


class PropertyDefinition(BaseModel):
    """A property the application knows by name."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy: FactPolicy
    summary: str
    requires_supernatural: bool = False
    """True when the property only makes sense in a world where the supernatural
    exists. Checked against WorldRules before the fact is stored; see
    `world_facts.compatibility`."""


_NAMESPACE_POLICY: dict[PropertyNamespace, FactPolicy] = {
    PropertyNamespace.SYSTEM: FactPolicy.SYSTEM,
    # Flags are progression state. "The player discovered the palace secret" is a
    # thing the game decides, not a thing narration may assert about itself.
    PropertyNamespace.GAMEPLAY: FactPolicy.SYSTEM,
    PropertyNamespace.DERIVED: FactPolicy.DERIVED,
    PropertyNamespace.NARRATIVE: FactPolicy.OPEN,
    # World truth is diegetic and consequential -- who rules, what burned down. It is
    # not colour, so it does not get the narrative namespace's freedom.
    PropertyNamespace.WORLD: FactPolicy.GUARDED,
}

SYSTEM_ALIVE = "system.alive"
"""Named because two modules need it: the registry below and the WorldRules
compatibility checks, which have opinions about who may come back from the dead."""

KNOWN_PROPERTIES: dict[str, PropertyDefinition] = {
    # -- mechanical state. Most of these belong to systems that do not exist yet; the
    #    names are registered now so nothing else can claim them in the meantime.
    SYSTEM_ALIVE: PropertyDefinition(
        policy=FactPolicy.SYSTEM, summary="Whether the subject is objectively alive."
    ),
    "system.hp": PropertyDefinition(
        policy=FactPolicy.SYSTEM, summary="Reserved for a future health system."
    ),
    "system.inventory": PropertyDefinition(
        policy=FactPolicy.SYSTEM, summary="Reserved for a future inventory system."
    ),
    "system.currency": PropertyDefinition(
        policy=FactPolicy.SYSTEM, summary="Reserved for a future economy system."
    ),
    "system.skill_level": PropertyDefinition(
        policy=FactPolicy.SYSTEM, summary="Reserved for a future progression system."
    ),
    # -- world truth
    "world.political_status": PropertyDefinition(
        policy=FactPolicy.GUARDED, summary="The governing situation of a place or the world."
    ),
    "world.condition": PropertyDefinition(
        policy=FactPolicy.GUARDED, summary="The physical state of a place or object."
    ),
    # -- narrative colour the Story Director may establish freely
    "narrative.birthplace": PropertyDefinition(
        policy=FactPolicy.OPEN, summary="Where a character was born."
    ),
    "narrative.favorite_food": PropertyDefinition(
        policy=FactPolicy.OPEN, summary="Food a character likes."
    ),
    "narrative.dislikes_food": PropertyDefinition(
        policy=FactPolicy.OPEN, summary="Food a character dislikes."
    ),
    "narrative.childhood_nickname": PropertyDefinition(
        policy=FactPolicy.OPEN, summary="What a character was called growing up."
    ),
    "narrative.supernatural_nature": PropertyDefinition(
        policy=FactPolicy.OPEN,
        summary="What a character is, in worlds where that can be more than human.",
        requires_supernatural=True,
    ),
    # -- owned by a dedicated model. Registered so the obvious spellings are a named
    #    refusal that says where the real answer lives, rather than landing in SYSTEM
    #    and being writable by an engine that should be updating the other table.
    "system.location": PropertyDefinition(
        policy=FactPolicy.DEDICATED,
        summary="CharacterPosition owns this. See app.domain.character_position.",
    ),
    "system.location_condition": PropertyDefinition(
        policy=FactPolicy.DEDICATED, summary="LocationState.condition owns this."
    ),
    "system.location_accessibility": PropertyDefinition(
        policy=FactPolicy.DEDICATED, summary="LocationState.accessibility owns this."
    ),
    "system.location_security": PropertyDefinition(
        policy=FactPolicy.DEDICATED, summary="LocationState.security_level owns this."
    ),
    "system.location_owner": PropertyDefinition(
        policy=FactPolicy.DEDICATED, summary="LocationState.owner_entity_id owns this."
    ),
    "system.location_controller": PropertyDefinition(
        policy=FactPolicy.DEDICATED, summary="LocationState.controller_entity_id owns this."
    ),
    "system.connection_accessibility": PropertyDefinition(
        policy=FactPolicy.DEDICATED, summary="LocationConnectionState.accessibility owns this."
    ),
    # -- derived. Registered so that storing one is a named refusal rather than an
    #    unknown property that would otherwise land in GUARDED.
    "derived.is_night": PropertyDefinition(
        policy=FactPolicy.DERIVED, summary="Computed from the session clock. See world_time."
    ),
    "derived.is_injured": PropertyDefinition(
        policy=FactPolicy.DERIVED, summary="Computed from a future health system."
    ),
    "derived.is_wanted_here": PropertyDefinition(
        policy=FactPolicy.DERIVED, summary="Computed from a future faction system."
    ),
}


def resolve_policy(canonical_property: str) -> FactPolicy:
    """The policy governing a property.

    Registry first, namespace second. Indexed rather than `.get`-with-a-default so
    that adding a namespace without deciding its policy is a loud KeyError here
    instead of a silent GUARDED everywhere.
    """
    definition = KNOWN_PROPERTIES.get(canonical_property)
    if definition is not None:
        return definition.policy
    return _NAMESPACE_POLICY[namespace_of(canonical_property)]


def definition_for(canonical_property: str) -> PropertyDefinition | None:
    """The registered definition, or None for an unregistered extension property."""
    return KNOWN_PROPERTIES.get(canonical_property)


_LOCATION_DEDICATED: dict[str, str] = {
    # Registered by name in KNOWN_PROPERTIES as GUARDED, because for the world itself
    # or for an object it is an ordinary diegetic truth. Only for a *location* subject
    # does it collide with a dedicated model, so the refusal needs the subject too.
    "world.condition": "LocationState.condition owns a location's physical state.",
}


def location_dedicated_owner(canonical_property: str) -> str | None:
    """The dedicated model owning this property when the subject is a location.

    Separate from the `DEDICATED` policy because the policy is a property of the
    property alone, and this one depends on what the fact is about. `world.condition`
    of a *sword* is a fact; of the Broken Crown it is `location_states.condition`
    wearing a fact's clothes.
    """
    return _LOCATION_DEDICATED.get(canonical_property)
