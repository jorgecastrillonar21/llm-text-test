"""The loading boundary: raw document in, validated WorldRules out.

Anything that comes from outside the process -- a database column, a request body,
a seed script -- goes through `parse_world_rules`. Constructing `WorldRulesV1`
directly is only for code that already knows what version it is building.

Version dispatch is an explicit table, not a framework. When V2 arrives it gets its
own model and its own entry here, plus a `1 -> 2` upgrade if old documents are worth
carrying forward. Building a general migration engine now would be inventing
requirements for a second version that does not exist.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from app.domain.errors import InvalidWorldRulesError, UnsupportedRulesVersionError
from app.domain.world_rules.rules import WorldRulesV1

WorldRules = WorldRulesV1
"""The current version, aliased.

Code that just wants "whatever a world's rules are" annotates with this and gets to
keep working when V2 lands; code that means version 1 specifically says `WorldRulesV1`.
"""


def _parse_v1(raw: Mapping[str, Any]) -> WorldRulesV1:
    return WorldRulesV1.model_validate(raw)


_PARSERS: dict[int, Callable[[Mapping[str, Any]], WorldRulesV1]] = {
    1: _parse_v1,
}

SUPPORTED_VERSIONS: tuple[int, ...] = tuple(sorted(_PARSERS))


def parse_world_rules(raw: object) -> WorldRules:
    """Validate a raw rules document.

    Raises `UnsupportedRulesVersionError` for a version this build cannot read and
    `InvalidWorldRulesError` for anything else wrong with it. Never falls back to
    defaults: a world silently running on rules its author did not write is worse
    than a world that refuses to load.
    """
    if not isinstance(raw, Mapping):
        raise InvalidWorldRulesError(
            f"WorldRules must be an object, got {type(raw).__name__}.",
        )

    if "version" not in raw:
        raise InvalidWorldRulesError("WorldRules is missing its 'version' field.")

    version = raw["version"]
    # bool is an int in Python and would otherwise index straight into _PARSERS[1].
    if isinstance(version, bool) or not isinstance(version, int):
        raise UnsupportedRulesVersionError(version, SUPPORTED_VERSIONS)

    parser = _PARSERS.get(version)
    if parser is None:
        raise UnsupportedRulesVersionError(version, SUPPORTED_VERSIONS)

    try:
        return parser(raw)
    except PydanticValidationError as exc:
        # The pydantic message names the offending fields; keep it rather than
        # replacing it with something vaguer.
        raise InvalidWorldRulesError(f"Invalid WorldRules v{version}: {exc}") from exc


def default_world_rules() -> WorldRules:
    """The rules a world gets when its creator did not choose any.

    Same values as the `balanced` preset, reached without importing presets so the
    domain default does not depend on the preset catalogue.
    """
    return WorldRulesV1()
