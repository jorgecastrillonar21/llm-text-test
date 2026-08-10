"""The two resolvers this game actually has, and the table that finds them.

    Command + ResolutionContext  ->  ResolutionOutcome

Pure functions, both of them. Nothing here opens a transaction, writes a row, mints an
id or moves the clock: a resolver returns a description of what should happen and
`resolution_service` decides whether it may and makes it so, atomically or not at all.

# A dict, not a framework

`_RESOLVERS` maps `command.kind` to the thing that resolves it. That is the whole
registry. No entry points, no import scanning, no reflection -- with two resolvers,
any of those would be more machinery than the thing it dispatches to. A third resolver
is one dict entry and one class.

# Two, and not a plausible-looking third

There is no `AttackResolver` and no `TraverseConnectionResolver`, because there are no
combat mechanics and no traversal mechanics to put inside them. A resolver that
returned an invented-but-reasonable outcome would be worse than an absent one: it would
look like the system works, and the numbers it produced would be nobody's design.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from app.application.situation_service import ProgressionContext
from app.application.situation_service import resolver_for as situation_resolver_for
from app.domain.errors import ValidationError
from app.domain.resolution import (
    AdvanceTimeCommand,
    Command,
    EventCandidate,
    EventCategory,
    ProgressSituationCommand,
    ResolutionContext,
    ResolutionDisposition,
    ResolutionOutcome,
    ScheduledEventRequest,
    no_effect,
    rejected,
)
from app.domain.situation_progression import SituationProgressionResult
from app.domain.state_mutations import StateMutation
from app.domain.vocabulary import MetadataValue
from app.domain.world_situations import (
    ResolveSituation,
    Situation,
    SituationProgressionRequest,
    UpdateSituation,
    is_terminal,
)
from app.domain.world_time import TimeAdvanceRequest, is_permitted

SITUATION_PROGRESSED_EVENT = "situation_progressed"
"""The event subtype for a progression with nothing more specific to say.

Registered `EventPersistence.NONE`: a pass that nudged an intensity by four did not
produce history, and forcing it to invent an event name is how a session's history
fills with the engine narrating its own bookkeeping. A resolver that produces
`east_gate_breached` says something about the world, and that one is kept.
"""

SITUATION_PROGRESS_DUE_EVENT = "situation.progress"
"""ScheduledEvent type for "look at this process again". Payload carries the situation
id. Nothing dispatches it yet -- `advance_time` marks it processed and no simulation
engine exists to act on it. See docs/world-state-situations.md."""


class Resolver(Protocol):
    """Decides what one command does, from a context somebody else assembled.

    `name` and `version` go onto the `ResolutionRecord`, which is what makes an old
    verdict readable after the arithmetic behind it changes: a row saying
    `generic_progression/1` is a row whose numbers came from a resolver this build may
    no longer contain.
    """

    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    def resolve(self, command: Command, context: ResolutionContext) -> ResolutionOutcome: ...


class TimeAdvanceResolver:
    """Turns "move the clock" into a request the time service will honour.

    Deliberately thin. It does not advance anything -- `ResolutionOutcome.time_advance`
    is a *request*, and `app.application.time_service` is still the only thing that may
    touch `elapsed_minutes`, because the interval may contain scheduled events and a
    resolver adding minutes directly would skip them.

    What it does decide is whether the world lets this caller ask at all. A paused world
    refusing a simulation tick is a game rule saying no, which is a `rejected`
    resolution with a reason code -- not a technical failure, and not a silent zero.
    """

    name = "advance_time"
    version = "1"

    def resolve(self, command: Command, context: ResolutionContext) -> ResolutionOutcome:
        if not isinstance(command, AdvanceTimeCommand):
            raise ValidationError(f"{self.name} cannot resolve a {command.kind!r} command.")

        if not is_permitted(context.rules.simulation.time_progression, command.reason):
            return rejected(
                "time_progression_forbidden",
                detail=(
                    f"This world's time progression is "
                    f"{context.rules.simulation.time_progression.value!r}, which does not "
                    f"let {command.reason.value!r} move the clock."
                ),
            )

        if command.minutes == 0:
            # Legal, and the ordinary way to say "resolve anything already due without
            # moving". Still a real advance request: the time service processes what is
            # due at the current minute, which is the entire point of asking for zero.
            pass

        return ResolutionOutcome(
            disposition=ResolutionDisposition.APPLIED,
            time_advance=TimeAdvanceRequest(
                requested_minutes=command.minutes,
                reason=command.reason,
                detail=command.detail,
                interruptible=command.interruptible,
            ),
            narrative_context={
                "requested_minutes": command.minutes,
                "reason": command.reason.value,
                "from_minute": context.now,
            },
        )


class SituationProgressionResolver:
    """Runs one ongoing process forward from where it was last looked at to now.

    The interval is derived here, from the situation's own `last_progressed_at` and the
    session clock, and it is not a parameter anywhere. A caller that could choose it
    could evaluate a fire over six hours twice and burn the city down in an afternoon
    that never happened.

    The arithmetic itself belongs to the per-category situation resolvers in
    `situation_service`; this converts their `SituationProgressionResult` into the
    outcome vocabulary the resolution pipeline speaks. Two layers rather than one
    because the situation resolvers predate this boundary and are tested through it.
    """

    name = "situation_progression"
    version = "1"

    def resolve(self, command: Command, context: ResolutionContext) -> ResolutionOutcome:
        if not isinstance(command, ProgressSituationCommand):
            raise ValidationError(f"{self.name} cannot resolve a {command.kind!r} command.")

        situation = context.require_situation(command.situation_id)

        if not situation.is_live:
            # A game rule, not a bug: a concluded process stays concluded. The caller is
            # told why rather than being handed a silent no-op it cannot distinguish
            # from "nothing happened this interval".
            return rejected(
                "situation_concluded",
                detail=f"{situation.title!r} is {situation.status.value}.",
            )

        elapsed = context.now - situation.last_progressed_at
        if elapsed <= 0:
            # Already up to date. Legitimate, and nothing happened.
            return no_effect("already_progressed")

        request = SituationProgressionRequest(
            situation_id=situation.id,
            from_time=situation.last_progressed_at,
            to_time=context.now,
            trigger=command.trigger,
        )
        result = situation_resolver_for(situation).resolve(
            ProgressionContext(
                situation=situation,
                request=request,
                rules=context.rules,
                participants=context.participants_of(situation.id),
            )
        )

        if result.is_noop():
            # A dormant conspiracy evaluated over six hours did nothing. Recording an
            # event and moving the revision to say so would make "has anything changed?"
            # unanswerable.
            return no_effect("no_change")

        return ResolutionOutcome(
            disposition=ResolutionDisposition.APPLIED,
            events=_events_from(situation, result),
            state_mutations=_mutations_from(situation, result),
            scheduled_events=_schedule_from(result),
            narrative_context={
                "situation_id": str(situation.id),
                "situation_title": situation.title,
                "elapsed_minutes": elapsed,
                "intensity_delta": result.deltas.intensity_delta,
                "threat_delta": result.deltas.threat_delta,
                "momentum_delta": result.deltas.momentum_delta,
                **(
                    {"status_change": result.status_change.value}
                    if result.status_change is not None
                    else {}
                ),
            },
        )


def _events_from(
    situation: Situation, result: SituationProgressionResult
) -> tuple[EventCandidate, ...]:
    """The resolver's own events when it has any, and a generic one otherwise.

    `east_gate_breached` is a better history entry than "the siege progressed", so a
    resolver that produced one gets it. The generic fallback exists so a progression
    always has something to say, and its policy then drops it -- which is the honest
    outcome for a pass that only moved a number.
    """
    if result.generated_events:
        return tuple(
            EventCandidate(
                category=EventCategory.SITUATION,
                subtype=event.type,
                summary=event.description,
                primary_location_id=situation.primary_location_id,
            )
            for event in result.generated_events
        )
    return (
        EventCandidate(
            category=EventCategory.SITUATION,
            subtype=SITUATION_PROGRESSED_EVENT,
            summary=f"{situation.title}: {result.notes or 'progressed'}"[:500],
            primary_location_id=situation.primary_location_id,
            payload={"situation_id": str(situation.id)},
        ),
    )


def _mutations_from(
    situation: Situation, result: SituationProgressionResult
) -> tuple[StateMutation, ...]:
    """The result as one ordered list of mutations.

    Order matters in exactly one way: the situation's own change comes first, so a
    reader sees what this progression was *about* before what it knocked over. Nothing
    depends on it mechanically -- the batch is validated and applied as a whole.
    """
    mutations: list[StateMutation] = [_own_change(situation, result)]
    mutations.extend(result.state_mutations)
    mutations.extend(
        # Children are linked to their cause here rather than by the situation resolver,
        # which has no reason to know its own id and every reason not to be trusted
        # with it.
        start.model_copy(update={"parent_situation_id": situation.id})
        if start.parent_situation_id is None
        else start
        for start in result.new_situations
    )
    return tuple(mutations)


def _own_change(situation: Situation, result: SituationProgressionResult) -> StateMutation:
    """The mutation that moves the situation this pass was about.

    A terminal status change becomes `ResolveSituation`, which records the ending; every
    other outcome becomes `UpdateSituation`. The deltas do not ride along with the
    resolve -- resolving preserves the numbers as they stood, which is the honest record
    of how a process was when it ended.
    """
    if result.status_change is not None and is_terminal(result.status_change):
        return ResolveSituation(
            situation_id=situation.id,
            resolution_status=result.status_change,
            reason=result.resolution_reason,
        )

    return UpdateSituation(
        situation_id=situation.id,
        intensity_delta=result.deltas.intensity_delta,
        threat_delta=result.deltas.threat_delta,
        momentum_delta=result.deltas.momentum_delta,
        resulting_status=result.status_change,
        reason=result.notes[:300],
        # `last_progressed_at` is not on the mutation: the state service stamps it from
        # the session's fictional clock, so a resolver cannot claim a process was
        # evaluated at a time it was not.
    )


def _schedule_from(result: SituationProgressionResult) -> tuple[ScheduledEventRequest, ...]:
    """The situation resolver's next-evaluation request, if it made one.

    Absolute session time, straight through -- the resolver already converted whatever
    cadence it had in mind into a position on the clock.
    """
    if result.next_progression_at is None:
        return ()
    payload: dict[str, MetadataValue] = {"situation_id": str(result.situation_id)}
    return (
        ScheduledEventRequest(
            due_at=result.next_progression_at,
            type=SITUATION_PROGRESS_DUE_EVENT,
            payload=payload,
            # Never interrupts. A world quietly re-evaluating a fire is not a reason to
            # cut a player's action short; the events that should interrupt are the ones
            # a resolver emits, not the housekeeping that produces them.
            interrupt_player_action=False,
        ),
    )


_RESOLVERS: dict[str, Resolver] = {
    "advance_time": TimeAdvanceResolver(),
    "progress_situation": SituationProgressionResolver(),
}


def register_resolver(kind: str, resolver: Resolver) -> None:
    """Attach a resolver to a command kind, replacing any existing one.

    Exists for tests and for the systems that will follow. There is no discovery step:
    a new command type is a new entry here, written by whoever added the command.
    """
    _RESOLVERS[kind] = resolver


def resolver_for(kind: str) -> Resolver:
    """The resolver for a command kind, or a failure naming it.

    No fallback. A command with no resolver is a wiring mistake, and a generic
    "do nothing" default would turn it into a session that silently ignores actions.
    """
    resolver = _RESOLVERS.get(kind)
    if resolver is None:
        known = ", ".join(sorted(_RESOLVERS)) or "none"
        raise ValidationError(
            f"No resolver is registered for command kind {kind!r}. Registered: {known}."
        )
    return resolver


def known_resolvers() -> dict[str, Resolver]:
    """A copy, for tooling and tests."""
    return dict(_RESOLVERS)


def situation_ids_needing_progress(situations: list[Situation], *, now: int) -> list[uuid.UUID]:
    """Live situations whose last evaluation is behind the clock.

    A helper for callers that want to progress everything a session has running, so the
    "which ones?" question has one answer rather than one per caller. Deliberately not a
    scheduler: nothing calls this on a timer, and fictional time only moves when the
    application moves it.
    """
    return [s.id for s in situations if s.is_live and s.last_progressed_at < now]
