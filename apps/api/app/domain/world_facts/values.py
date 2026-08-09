"""What a fact is allowed to be worth.

A fact value is JSON-compatible and deliberately shallow: a scalar, a short list of
scalars, or a small flat object. One level, no nesting.

That shape is a design constraint rather than a serialisation detail. The moment a
value becomes a deep object with many independent fields, the thing being described
has stopped being a fact and become an aggregate -- a CharacterState, an inventory, a
faction ledger -- and those get their own models with their own invariants. Letting
them hide inside a JSON column here would produce a state system with no schema, no
migrations and no validation, which is the failure mode this type exists to prevent.

# Absence, null and false

These are three different statements and the type system keeps them apart:

    no fact row              nothing has structurally established this property
    value is None            the property is established, and its value is nothing
    value is False           the property is established, and its value is false

`None` is therefore a real value here, not a missing one. `SetFact` requires `value`
explicitly for exactly this reason -- a caller who omits it gets an error rather than
an accidental "objectively nothing".
"""

from __future__ import annotations

from app.domain.errors import ValidationError

FactScalar = bool | int | float | str | None
"""The leaves. `None` is a value, not an absence -- see the module docstring."""

FactValue = FactScalar | list[FactScalar] | dict[str, FactScalar]
"""Everything a fact may hold. Non-recursive on purpose: a list of objects or an
object of objects will not type-check, which is what stops aggregates from creeping
into this table."""

MAX_STRING_LENGTH = 500
MAX_ITEMS = 20
MAX_KEYS = 12
MAX_KEY_LENGTH = 60


def check_fact_value(value: object) -> FactValue:
    """Narrow arbitrary input to a `FactValue`, or raise `ValidationError`.

    A parser rather than an assertion, and it takes `object` on purpose. The three
    places a value arrives from -- a JSON column, a client request, a language model --
    all hand over something whose shape is a claim rather than a guarantee, and this is
    where the claim gets checked. Everything downstream can then trust the annotation.

    The bounds are small deliberately. They are not there to protect the database --
    SQLite would happily store a megabyte of JSON -- but to make the moment a fact
    should have become a dedicated state model fail loudly instead of silently.
    """
    # Spelled out rather than delegated to `_is_scalar` so the type checker can
    # narrow `object` here; a helper returning bool tells it nothing.
    if value is None or isinstance(value, bool | int | float | str):
        if isinstance(value, str):
            _check_string(value, "value")
        return value

    if isinstance(value, list):
        if len(value) > MAX_ITEMS:
            raise ValidationError(
                f"A fact value list may hold at most {MAX_ITEMS} items; got {len(value)}."
            )
        for item in value:
            if not _is_scalar(item):
                raise ValidationError(
                    "A fact value list may only hold scalars. Nested lists and objects "
                    "belong to a dedicated state model, not to a fact."
                )
            if isinstance(item, str):
                _check_string(item, "value item")
        return value

    if isinstance(value, dict):
        if len(value) > MAX_KEYS:
            raise ValidationError(
                f"A fact value object may hold at most {MAX_KEYS} keys; got {len(value)}."
            )
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValidationError(f"A fact value object needs string keys; got {key!r}.")
            if len(key) > MAX_KEY_LENGTH:
                raise ValidationError(
                    f"A fact value key may be at most {MAX_KEY_LENGTH} characters; got {key!r}."
                )
            if not _is_scalar(item):
                raise ValidationError(
                    f"The fact value at {key!r} is a nested container. A fact holds one flat "
                    "level; anything deeper is an aggregate and needs its own model."
                )
            if isinstance(item, str):
                _check_string(item, f"value at {key!r}")
        return value

    raise ValidationError(
        f"{type(value).__name__} is not a JSON-compatible fact value. "
        "Use a boolean, number, string, null, a short list, or a small flat object."
    )


def _is_scalar(value: object) -> bool:
    # `bool` before `int` is unnecessary here because both are permitted, but the
    # order matters everywhere else in this module -- bool is a subclass of int.
    return value is None or isinstance(value, bool | int | float | str)


def _check_string(text: str, label: str) -> None:
    if len(text) > MAX_STRING_LENGTH:
        raise ValidationError(
            f"A fact {label} may be at most {MAX_STRING_LENGTH} characters; got {len(text)}. "
            "Long prose is narration, not structured truth."
        )
