"""WorldFacts: what is objectively true in a session, right now.

The second piece of the future `WorldState`, after `world_time`. Where the clock
answers "when is it?", this answers "what is so?" -- structurally, in a form the game
can query, rather than as prose a language model has to be trusted to remember.

    values          what a fact may be worth, and why it must stay small
    properties      canonical names, namespaces, and the grammar they obey
    policy          which properties are open, guarded, mechanical or derived
    authority       who is asking, and what that entitles them to
    facts           the WorldFact itself, and what it is about
    mutations       SetFact, RemoveFact, and the batch that makes them atomic
    compatibility   the world's rules constraining what may become true

The Story Director may propose narrative colour and nothing else. It cannot write
mechanical state, and its prose is not a write path -- accepted proposals become
mutations that the application layer validates and applies. See
docs/world-state-facts.md.
"""

from __future__ import annotations

from app.domain.world_facts.authority import (
    FactAuthority,
    is_permitted,
    require_permitted,
    requires_source_event,
)
from app.domain.world_facts.compatibility import SUPERNATURAL_TAG, check_rules_compatibility
from app.domain.world_facts.facts import (
    MAX_TAGS,
    WORLD_SUBJECT,
    FactKind,
    FactSubject,
    FactSubjectType,
    Importance,
    WorldFact,
)
from app.domain.world_facts.mutations import FactMutation, RemoveFact, SetFact
from app.domain.world_facts.policy import (
    KNOWN_PROPERTIES,
    SYSTEM_ALIVE,
    FactPolicy,
    PropertyDefinition,
    definition_for,
    location_dedicated_owner,
    resolve_policy,
)
from app.domain.world_facts.properties import (
    PROPERTY_ALIASES,
    PropertyNamespace,
    namespace_of,
    parse_property,
)
from app.domain.world_facts.values import FactScalar, FactValue, check_fact_value

__all__ = [
    "KNOWN_PROPERTIES",
    "MAX_TAGS",
    "PROPERTY_ALIASES",
    "SUPERNATURAL_TAG",
    "SYSTEM_ALIVE",
    "WORLD_SUBJECT",
    "FactAuthority",
    "FactKind",
    "FactMutation",
    "FactPolicy",
    "FactScalar",
    "FactSubject",
    "FactSubjectType",
    "FactValue",
    "Importance",
    "PropertyDefinition",
    "PropertyNamespace",
    "RemoveFact",
    "SetFact",
    "WorldFact",
    "check_fact_value",
    "check_rules_compatibility",
    "definition_for",
    "is_permitted",
    "location_dedicated_owner",
    "namespace_of",
    "parse_property",
    "require_permitted",
    "requires_source_event",
    "resolve_policy",
]
