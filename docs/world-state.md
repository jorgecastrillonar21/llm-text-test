# WorldState

The canonical document for one session's mutable reality: what it is, what owns which
part of it, and what may change it.

Five epics built the parts — the clock, the facts, the geography, the situations, and
the resolution boundary that changes them. This one does not add a sixth. It names the
root they all hang off, states the rules that only make sense across all of them at
once, and writes down the decisions that would otherwise have to be re-derived from six
modules and a migration chain.

The single claim it exists to hold:

> **WorldState is one conceptual root, not one serialized document.**

A `GameSession` has exactly one authoritative reality, and every part of that reality
belongs to it. But the parts live in their own tables, with their own indexes,
lifecycles and invariants, and a root that carried them would be a JSON blob holding an
entire world — unqueryable, unindexable, and rewritten in full every time a lamp went
out.

## The shape of it

```text
                        WORLD
                          │
             ┌────────────┴────────────┐
             │                         │
        WorldRules                WorldTemplate
     how this universe          the starting configuration:
     is allowed to work         geography, characters, facts,
             │                 situations, the opening date
             │                         │
             │                         ▼
             │                    GameSession ─────── one playthrough
             │                         │
             │                    WorldStateV1 ────── version · revision · time
             │                         │
             │    ┌─────────────┬──────┴──────┬──────────────┐
             │    │             │             │              │
             │  Facts       Spatial      Situations      Scheduled
             │              State                          Events
             │    │             │             │              │
             │    └─────────────┴──────┬──────┴──────────────┘
             │                         │
             └────────constrains──▶ Resolutions ──── the only door in
                                       │
                              Events + Mutations
                                       │
                                       ▼
                               Updated WorldState
                                       │
                                       ▼
                                 StoryContext ────── a bounded selection,
                                       │             never the whole world
                                       ▼
                                Story Director
```

Everything above the `Resolutions` line is what the world *is*. Everything below it is
how it is allowed to change, and what a language model is permitted to see of the
result. The two arrows out of `Updated WorldState` are the ones worth staring at: state
flows *into* the prompt as a selection, and never flows back out of it as an assignment.

## WorldState is not WorldRules

| | `WorldRules` | `WorldState` |
|---|---|---|
| answers | what is *possible* in this universe | what is *currently true* in this save |
| scope | the world, shared by every session | one session |
| changes | never, after creation | constantly, through resolutions |
| storage | one JSON document on `worlds` | a root plus six tables |
| example | "magic exists and is rare" | "the east gate is destroyed" |

Rules constrain state; they are not state. `worlds.rules_json` is deliberately one
serialized document — it is static configuration with no independent lifecycle and no
queries of its own, so relational decomposition would buy nothing. That is the exact
argument that runs the *other* way for WorldState, and the difference is mutability.
See [world-rules.md](world-rules.md).

## World template versus session reality

A world is a reusable starting position. A session is a playthrough of it, and ten
sessions of one world are ten different realities.

| | template (world-scoped) | reality (session-scoped) |
|---|---|---|
| facts | `worlds.initial_facts` | `world_facts` |
| situations | `worlds.initial_situations` | `situations` |
| geography | `location_definitions`, `location_connections`, `location_zones` | `location_states`, `location_connection_states` |
| clock | `worlds.initial_datetime` | `game_sessions.elapsed_minutes` |
| rules | `worlds.rules_json` | — (rules do not change) |

**Playing never writes back to the template.** Killing the king in one session leaves
the world's `initial_facts` untouched, and every other save of that world still starts
with a living king. There is no code path from a mutation to a `worlds` row, and none
from a mutation to a `location_definitions` row that a session did not create for
itself; `test_playing_a_session_never_writes_back_to_the_world_it_started_from` pins it.

The one crossing is deliberate and one-directional: gameplay may *invent* a small place
inside one save. That definition is written with `origin_session_id` set, which makes it
deterministic canon for that session and invisible to every other one. See
[world-state-locations.md](world-state-locations.md).

## The persisted root

Three columns on `game_sessions`, plus the session's own id:

```python
class WorldStateV1(BaseModel):
    version: Literal[1]      # which shape this state is stored in
    session_id: uuid.UUID    # whose reality this is
    revision: int            # the logical version of that reality
    time: TimeState          # where the session sits on its own clock
```

Four fields, and **none of them has a cardinality**. Nothing here grows with how long a
session has been played, how many places it knows or how many things are true in it.
That is the constraint the whole design exists to hold, and it is tested structurally
rather than trusted: `test_the_root_carries_nothing_with_a_cardinality` asserts the
field set and rejects any annotation containing a `list` or a `dict`.

It lives on `game_sessions` rather than in a `world_states` table with one row per
session, because that table would be this one with extra joins — same identity, same
lifetime, same cascade. `test_no_column_anywhere_stores_a_session_world_as_one_blob`
asserts there is no JSON column on the session row at all.

`time` is a `world_time.TimeState`, imported rather than redefined. A second
`elapsed_minutes` on this model would be a second answer to what time it is, which is
precisely the failure the decomposition exists to prevent.

## Why the collections stay separate

Each part of a session's reality is a table because each one independently needs
something a document cannot give it:

| | what it needs |
|---|---|
| **its own indexes** | `world_facts` is keyed by `(session, subject, property)`; situations by status and by category; events by `(occurred_at, event_sequence)`. One document has one key. |
| **its own lifecycle** | A fact is overwritten in place. A situation moves through a status machine. An event is appended and never touched again. A scheduled event is resolved or cancelled. |
| **its own foreign keys** | A fact points at the event that established it; a situation at a location and a parent situation; a location state at a definition. The database enforces those. Inside a blob it enforces nothing. |
| **bounded queries** | "the twelve most important live situations" has to be a `LIMIT`, not a load-everything-and-filter. A prompt built from a document read in full is a prompt whose cost grows with the save. |
| **its own invariants** | One current value per subject and property is a unique index. Monotonic event sequence is a unique constraint. A document can hold two contradictory facts and no layer below the application would notice. |
| **partial mutation** | Changing one door's state writes one row. In a document it rewrites the world, and two concurrent writers lose each other's work by construction. |
| **partial retrieval** | Reading where the scene is should not cost the whole history of the save. |

The counter-argument for a document — "one read, one write, atomic by construction" —
is answered by the transaction, not by the schema. Every batch of mutations commits
together or not at all, and that is a property of the unit of work rather than of how
many rows it touched.

## The decomposition

Seven tables carry one session's current reality and its record of itself. Each is keyed
by `session_id` with a non-null foreign key and `ON DELETE CASCADE`, which is what makes
"a session's world" a real thing rather than a manner of speaking.

| table | what it holds | detail doc |
|---|---|---|
| `world_facts` | declarative truth: one current value per subject and property | [facts](world-state-facts.md) |
| `location_states` | what is currently true of a place, in this save | [locations](world-state-locations.md) |
| `location_connection_states` | whether a way through is currently passable | [locations](world-state-locations.md) |
| `character_positions` | where each actor is — one row per actor, no exceptions | [below](#where-the-player-is) |
| `situations` | ongoing processes with a lifecycle and a direction | [situations](world-state-situations.md) |
| `scheduled_events` | commitments the world has made about its future | [time](world-state-time.md) |
| `game_events` | significant history — what happened, not what is | [event/resolution](event-resolution.md) |

Alongside them, and deliberately *not* current state: `resolutions` is the mechanical
audit trail, `messages` is the narrative record, and `situation_participants` is detail
belonging to a situation rather than to the session.

### Five categories that must never be collapsed

The most expensive mistake available here is a model that merges two of these because
they look similar in a schema diagram. They answer different questions, and a system
that conflates any pair starts lying about one of them.

```text
TEMPORAL            what time is it              elapsed_minutes
DECLARATIVE         what is true                 world_facts
SPATIAL             where things are and are     location_states,
                    they reachable               location_connection_states,
                                                 character_positions
ONGOING PROCESS     what the world is doing      situations
FUTURE COMMITMENT   what it has promised to do   scheduled_events
```

The siege is not the breach, the breach is not the ruined gate, and the ruined gate is
not the next evaluation of the siege. Four separate things, four separate rows.

And on the history side, three more:

```text
SIGNIFICANT HISTORY   what happened, worth remembering    game_events
MECHANICAL AUDIT      how the engine decided it           resolutions
NARRATIVE HISTORY     what was said and written           messages
```

A `GameEvent` is not current state — "the gate gave way" is history, and "the gate is
destroyed" is a `LocationState`. A `Message` is not truth: a character may lie, and the
transcript records that they said it, not that it is so. A `ResolutionRecord` is not
narrative memory: nothing in the prompt has any use for a resolver version. A
`ScheduledEvent` is not a fact: "the roof collapses in ninety minutes" is a commitment,
and it is false right up until it isn't.

And one more that is none of the above:

```text
PROCESS TELEMETRY     what a generation cost     LlmGenerationMetrics (no table)
```

`LlmGenerationMetrics` is superficially the nearest thing to a `GameEvent` — append-only,
timestamped, session-scoped — and it has no authority whatsoever. It records that the
machine took 99 seconds and read 5409 tokens; nothing about the world changed because of
it. It **never moves the revision**, is never written as a `GameEvent` or a memory, is
never persisted at all (a bounded in-process buffer and a log line), and never enters
`StoryContext`. See [llm-performance-baseline.md](llm-performance-baseline.md#metrics-are-not-game-state).

## Three counters, none derived from another

```text
elapsed_minutes    fictional minutes since this session began
turn_index         exchanges between the player and the story
state_revision     committed changes to this session's reality
```

Each moves for its own reason, and **no one of them may ever be computed from another**:

- A turn of pure dialogue moves `turn_index` and nothing else.
- Sleeping eight hours moves `elapsed_minutes` by 480 and `turn_index` by one.
- A resolution that changes six things moves `state_revision` by one and may move the
  clock not at all.
- Time advancing past a scheduled event can move the revision without a turn happening.

`test_fictional_time_turns_and_the_revision_never_derive_from_each_other` drives each of
them independently and asserts the other two stayed put. The frontend shows all three
for the same reason: a player who sees only two would infer the third.

### What moves the revision

**Once per logical state-changing resolution.** Not once per mutation, not once per
field, not once per table touched. One batch that damages a tavern, sets a fact and
starts a situation moves the revision from 0 to 1 —
`test_one_batch_across_three_domains_moves_the_revision_once` asserts exactly that,
including that all three mutations applied.

There is exactly one mechanism: `bump_state_revision`, on the port, incrementing the one
column. `test_only_the_session_row_carries_a_revision` walks the ORM metadata for any
column with "revision" in its name and asserts the set is exactly
`{("game_sessions", "state_revision")}` — the two columns on `resolutions` record the
before and after of *that* counter, and are not a second one.

`stage_state_change(..., moves_revision=False)` has exactly two callers, and both are
cases where the batch is not by itself one logical change: a turn, which bumps once at
the end if it changed anything at all — covering the places and situations that do not
come through this path, and leaving the revision alone when a turn was pure conversation;
and session initialization, below. Nothing else may pass it. A caller
that turned it off because a second bump was inconvenient would be inventing the second
revision mechanism this design exists to avoid.

### The initial revision

> **A session that has been initialised and not yet played sits at `revision = 0`.**

Zero is "the world exactly as its template declared it". Materializing a template into a
new session is not a change to that session's world; it *is* that world, arriving.

The alternative — bumping once per seeded batch — would make the starting revision a
function of how much content the world's author wrote, so two sessions that had equally
never been played would disagree about how many times their reality had changed.
`test_the_revision_convention_does_not_depend_on_how_much_a_world_declared` starts a
session from an empty world and one from a fully-furnished world and asserts both are at
0.

Optimistic concurrency uses `expected_revision` on the batch, compared against the
session row inside the transaction; a mismatch is a `StaleStateError` and a 409. There
is no row lock and no version column beyond this one.

## CurrentWorldSnapshot

`CurrentWorldSnapshot` is a **read projection**. It is composed on demand from the
tables that own each part, handed to a caller, and thrown away. Nothing in it is stored,
nothing in it is authoritative, and there is no path by which writing to one of these
objects could reach the database.

That is why `world_state_service` takes `WorldStateReaderPort` — a port with no write
method on it at all. A service that could read the whole world *and* change it would
invite the one shortcut this decomposition exists to prevent: read everything, fix it up
in memory, hand the result back as the new truth. Changes go through a resolution and a
typed mutation, always.

A snapshot is built from the caller's open transaction, so the revision it reports and
the contents beside it come from one consistent view of the database rather than from
eight independent reads.

### Scopes

Size is the whole problem, so a snapshot has to be asked for at a size.

| scope | adds | cost |
|---|---|---|
| `minimal` | the root, the counts, facts at importance ≥ 4 (max 10), live situations (max 8) | cheap; no graph load |
| `relevant` | where the scene is, and the ways out of it | loads the spatial graph |
| `regional` | the containers above it, what is inside it, one edge away | same graph, more of it |
| `full_debug` | every fact, place, connection, situation, the whole schedule, recent history | a database dump |

Each is a superset of the one below. `test_each_scope_adds_to_the_one_below_and_never_takes_away`
builds all four from one session and asserts that each new block appears exactly at the
scope that introduces it. The default is `minimal`, because the expensive answer must
never be the accidental one.

`counts` is identical at every scope — the same test asserts the four snapshots produce
one distinct `counts` value between them. That is what lets a reader tell "not in this
scope" from "there are none", and it is why an expensive scope buys detail rather than
truth. `truncated` is set when any read hit its ceiling: a clean-looking snapshot that
was cut short is a lie unless it says so.

**`full_debug` is not available to a gameplay client.** `GET /sessions/{id}/world-state`
rejects it with a 422 and names the scopes it will serve; the full dump lives on the
development router, which is mounted only when `APP_ENV` is `development` or `test`.
`test_the_gameplay_endpoint_refuses_to_dump_a_whole_world` pins both halves.

## Who may change what

Mutation ownership stays with the domain that owns the data. `state_service` is a
dispatcher, not an authority: it validates the whole batch, then routes each typed
mutation to the domain that knows what it means.

```text
SetFact, ClearFact          → world_facts
UpdateLocationState         → location_states
UpdateConnectionState       → location_connection_states
StartSituation, UpdateSituation, ConcludeSituation → situations
```

Two rules make this hold:

**Typed mutations only.** There is no arbitrary JSON patch, no "apply this dict to the
world" verb, and no raw root mutation API. Every change is a named operation with a
validated payload, which is what lets policy run before anything is written.

**Authority is checked at construction.** A `StateMutationBatch` validates its authority
against the operations it carries *when the batch is built*, not when it is applied — a
`story_director` batch carrying a spatial operation raises a validation error before it
can be handed to a service. The property namespaces enforce the same idea for facts:
`narrative.*` is open, `world.*` is guarded, `system.*` and `gameplay.*` are for game
systems, and `derived.*` may be written by nobody at all.

### The resolution boundary

Every authoritative change goes through one pipeline:

```text
Command → ResolutionContext at a known revision → pure Resolver → Outcome
                                                                    │
                    one transaction ────────────────────────────────┘
                    resolution record → events → mutations → revision → clock
```

Order inside that transaction is load-bearing: the record first because events carry its
foreign key, events before mutations because a fact names the event that established it,
and the clock last because a mutation is a consequence of the world as the resolver saw
it rather than of the minute it lands on.

A refused change leaves the world exactly as it was —
`test_a_refused_change_leaves_the_world_exactly_as_it_was` sends a batch whose second
mutation is illegal and asserts the first one did not land and the revision did not
move. Because the whole batch is validated before the first write, that case is usually
not a rollback at all: it is a refusal, and the transaction never started doing anything
to undo. Full semantics in [event-resolution.md](event-resolution.md).

### Transaction boundaries

Application orchestration owns the transaction, and no mutation handler commits. The
audit that established this walked every `.commit()` in `app/application` and confirmed
each one sits at a use-case boundary:

```text
state_service.apply_state_change      one batch is the whole unit of work
turn_service.execute_turn             one turn, including the player's own message
resolution_service.resolve            the verdict and everything it caused
time_service                          advance_time, schedule_event, cancel_scheduled_event
narration_service.narrate_resolution  prose for a verdict already committed
```

`stage_state_change` exists precisely so a caller whose unit of work is larger can apply
mutations without committing them — a turn, which also writes messages, memories and
relationships and commits once at the end.

The routers commit too, and that is the same rule seen from the other side: `create_session`
and the location-creation endpoints are use cases that happen to live in the HTTP adapter
because they are one write each. What no layer does is commit inside `get_db`'s teardown —
FastAPI closes `yield` dependencies after the response is sent, so a client that
immediately re-reads could miss its own write. See
[architecture.md](architecture.md#why-commits-are-explicit).

## Session initialization

```text
POST /sessions
  ├─ create the GameSession row, flush
  ├─ materialize_initial_facts        template facts → world_facts
  ├─ materialize_initial_spatial_state state rows for the world's places
  ├─ materialize_initial_situations   template situations → situations
  ├─ materialize_initial_position     the player's canonical CharacterPosition
  └─ commit                            ← one transaction, all of it
```

Geography before situations, because a seeded situation may be centred on a place and
the location has to be visible before the siege of it can be written. The position comes
last, because it points at a location by id and the geography has to exist first.

The position is always written, even when it says `unlocated` — see
[where the player is](#where-the-player-is). A session with no position row at all is
precisely the ambiguity that table exists to remove.

**All of it or none of it.** A session that exists without the truths its world declared,
or without a state row for the places in it, is a world the player is playing a different
version of, and there is no retry that fixes it afterwards.
`test_initialisation_that_fails_leaves_no_half_built_world` fails a seeding step and
asserts no session row survives.

The four steps are four services rather than one because they are four different kinds
of thing: a batch of mutations, materialising defaults that no event caused, starting
processes the world was already running before anyone played it, and placing the actor
the save is about. None of them moves the revision.

## StoryContext is not WorldState

The most important boundary in this document, and the one with the least code enforcing
it.

> **Having more state has never meant sending more of it.**

`StoryContext` is a bounded, deterministic *selection*, assembled by `context_builder`
and nothing else. It is not a view of WorldState, it is not derived from a snapshot, and
nothing that builds a prompt calls `world_state_service` at all.

| block | bound |
|---|---|
| facts | 40, importance-ordered, split into critical (4–5) and relevant |
| recent messages | 20 |
| memories | 30 |
| characters | 12 |
| geography | the scene, its exits, its containers — never the graph |
| situations | scene-relevant, importance-ordered |
| history | 8 landmarks + 12 recent |
| resolutions | **zero, at any size** |

Every one of those is a constant in the application layer, and none of them is a
function of session size. `test_a_bigger_world_does_not_make_a_bigger_prompt`
establishes 120 facts across three batches and asserts the context still carries at most
`FACT_LIMIT` of them, and strictly fewer than the world contains.

The mechanical audit trail never reaches the prompt, and the enforcement is structural:
`StoryContext` has no field for it and no field whose type mentions one, which
`test_the_story_director_is_never_handed_the_mechanical_audit_trail` asserts against the
model's own annotations. There is nowhere to put a resolution, which is the only reliable
way to keep them out. Idempotency keys, resolver versions and revision numbers are engine
bookkeeping; the director is told what happened, not how the engine decided it.

A `full_debug` snapshot must never be sent to a language model. It exists for an
operator with a browser open.

## Versioning

`world_state_version` is stored on every session, defaults to `1`, and is checked on
every read. A session stamped with a version this build does not know is **refused,
loudly** — `UnsupportedWorldStateVersionError`, mapped to a 500 — rather than
best-efforted into a shape nobody wrote.

There is deliberately no migration framework here. One integer and one refusal is the
whole mechanism until a second version actually exists.
`SUPPORTED_WORLD_STATE_VERSIONS` is a tuple rather than a range, because a version is
supported when somebody wrote the code to read it.

The one exception is the consistency validator, which *reports* an unreadable version as
an issue instead of raising. Refusing to describe the thing somebody is trying to
diagnose would defeat the purpose of a diagnostic.

## Consistency validation

`check_state_consistency` runs eight referential checks across the whole decomposition:
the root, fact ownership, fact subjects, location states, connection states, situation
locations, situation participants, and scheduled events.

It is a **diagnostic, not a gate**. Nothing in the turn loop calls it and nothing waits
on its answer — state changes are validated on the way in by the domain that owns them.
This is for the case where something got in anyway: a bad migration, a hand-edited row,
a bug in a mutation handler.

It exists because foreign keys catch most of what can go wrong across six tables, and
not all of it: a `LocationState` for a place this session cannot see, a fact about a
character id nobody ever wrote, a situation centred on a location from another world.
Those are the shapes it reports.

The report says which checks ran and whether any read was truncated, so a caller can
tell "clean" from "not looked at". It deliberately does not attempt cross-domain
*semantic* invariants — whether a siege makes sense in a world at peace is not
referential integrity.

`GET /api/v1/dev/sessions/{id}/world-state/check`, development only.

## No event sourcing

**Current state is read from the tables that hold it.** The application does not, and
must not, rebuild runtime state by replaying `game_events`.

`game_events` is significant history: a curated, importance-filtered record of what
happened, with per-subtype policy deciding what is kept at all. It is not a complete
change log and was never designed to be one, so a replay would reconstruct a world that
never existed.

This is tested from both directions. `test_reading_the_world_does_not_touch_the_event_trail`
wraps the adapter in a proxy that raises if anything reads the event trail, then builds
snapshots at every gameplay scope. `test_deleting_every_event_changes_nothing_that_is_currently_true`
deletes every row from `game_events` and asserts the snapshot is byte-for-byte identical
and the revision unchanged. The one casualty is provenance: the facts that pointed at the
event which established them now point at nothing, because "why is this true" is a
question about history and history is what was removed.

There are no snapshots, no rewind engine, and no event bus. No Kafka, no Redis, no
distributed anything. This is a single-player game running in one process against one
SQLite file.

## API surface

```text
GET  /api/v1/sessions/{id}/world-state?scope=      minimal | relevant | regional
GET  /api/v1/sessions/{id}/world-state/facts       paged, filterable
GET  /api/v1/sessions/{id}/locations/...           session-visible geography
GET  /api/v1/sessions/{id}/situations              live processes
GET  /api/v1/sessions/{id}/events                  significant history
GET  /api/v1/sessions/{id}/resolutions             the mechanical audit trail

GET  /api/v1/dev/sessions/{id}/world-state         full_debug
GET  /api/v1/dev/sessions/{id}/world-state/check   the consistency report
POST /api/v1/dev/sessions/{id}/world-state/changes one batch, through the real service
```

**There is no `PUT` and no `PATCH` on world state, at any path**, and there must not be
one. A world that can be uploaded is a world with no invariants: it would bypass the
authority model, the typed mutations, the resolution record and the revision in a single
request. The development mutation endpoint is not an exception — it goes straight through
`apply_state_change`, so a story-director-authority batch is refused there exactly as it
would be in a turn. The endpoint being development-only does not lift the authority
model, because the authority model is the feature.

## Frontend

Three inert values under the session header: where the scene is, the fictional clock,
and the state revision. Plus a development-only link that opens the `full_debug` snapshot
as raw JSON in a new tab.

`WorldStateReadout` contains no `input`, `button`, `select` or `textarea`, and
`test('offers no way to edit the world it is showing')` asserts that by querying for
them. An interface that could edit what is true would be a way around every rule the
mutation path enforces.

Deliberately not built: a map, a strategy dashboard, a state editor, a character sheet,
a timeline editor.

## Where the player is

`character_positions` is the canonical answer, and the only one. One row per
`(session, actor kind, actor)`, enforced by a unique constraint, because two rows would
be two answers to "where is the player?".

A position is one of four shapes, discriminated on `kind`:

```text
at_location   at a place, optionally in a zone of it
in_transit    between two places, on a declared connection
offstage      not in the scene, deliberately
unlocated     nobody has said
```

`offstage` and `unlocated` are both fieldless and both kept, because they are different
claims. An actor the story set aside is not secretly somewhere; an actor nobody has
placed is an honest gap. Collapsing them would make "we decided" and "we never said"
the same row.

Everything is referenced by id, never by name, and every id is validated against the
geography the session can actually see before a position is stored: the location, the
zone (which must belong to that location), both ends of a transit, and the connection —
which must actually run from the origin to the destination, so a one-way edge cannot be
walked backwards by writing a position. A `character` position must name a character of
this world; the player's `actor_id` **is** the session id, because this build has no
player character row to point at, and `ActorKind` is what discriminates. The column
carries no foreign key for exactly that reason — it addresses two tables — so
`position_service` makes the check the database cannot.

**`LocationState` keeps no occupant list.** "Who is here" is a query against positions
(`position_service.actors_at`), not a field on the room. Two places recording the same
fact would disagree the first time one was written without the other, and the room is
the copy that would go stale.

`in_transit` records a *commitment* — who left where for where, along which connection,
when, and when they are expected — and computes nothing from it. No speed, no path, no
partial progress, no arrival logic. Reaching the expected minute is not arriving, the
same distinction [`ScheduledEvent`](world-state-time.md#due-is-not-processed) draws
between due and processed. Arriving is a caller writing `at_location`; the caller that
will do it is TravelEngine, which does not exist.

**`game_sessions.current_location` is legacy presentation.** It is read exactly once, at
session creation, by `position_service.materialize_initial_position`, which resolves the
string against this session's visible geography — exact, case-insensitive, trimmed, and
**refusing ambiguity**: two places called "Market Street" seed `unlocated` rather than
whichever row came back first. That is the last name-to-id resolution in the system's
life. From then on the position is the authority and the string cannot override it:
`StoryContext.session.current_location`, the prompt's geography block and
`CurrentWorldSnapshot.current_location` all derive from the position's location by id.
Migration `7c41a9f2b6d3` applied the same one-off rule to every existing save, so no
session anywhere is without a row.

Nothing else lives here. No discovery or knowledge flags, no tactical coordinates, no
character sheet. This is the smallest complete spatial-presence authority, drawn so that
Character Foundation composes it rather than growing a second one beside it.

## Duplicated authority

The audit for this consolidation looked for facts with two homes. It found exactly one,
and the correction above closed it: `game_sessions.current_location` was free text
resolved by name match, and where an actor is now has one canonical, id-addressed home.
The string is kept as a legacy seed and as presentation, documented as deprecated, and
nothing in the running system reads it after session creation.

Everything else came back clean. The clock has one authority (`time_service`), facts have
one writer (`state_service`), the revision has one mechanism (`bump_state_revision`), and
nothing derived is stored: the calendar date, the part of the day, and situation
intensity projections are all computed on read.

## Persistence and indexes

The index audit for this epic added nothing to the tables that already existed. Every
access pattern WorldState introduced was already served:

| query | index |
|---|---|
| the root | primary key on `game_sessions` |
| one actor's position | the unique constraint `(session_id, actor_kind, actor_id)` |
| who is at a place | `ix_character_positions_session_location` |
| facts for a session | `ix_world_facts_session_subject` on `(session_id, subject_type, subject_id)`, plus the two partial unique indexes on subject and property |
| spatial state | the unique constraints `(session_id, location_id)` and `(session_id, connection_id)`, which are indexes |
| situations | `ix_situations_session_status` on `(session_id, status)` |
| pending schedule | `ix_scheduled_events_pending` on `(session_id, status, due_at)` |
| recent history | `ix_game_events_session_time` on `(session_id, occurred_at, event_sequence)` |

A snapshot is a handful of bounded reads against indexes that already existed. Adding a
composite index "for the snapshot" would have duplicated one of these with a different
column order and cost every write for a read that is already served. Facts are ordered
by importance in the application after a `session_id`-bounded read rather than by an
`(session_id, importance)` index, because the bound is small and a second index on a
table that is written on nearly every resolution is not free.

Migration `2d9f47c1a8be` adds `world_state_version` with a default of 1 for existing
sessions and a `>= 1` check constraint. It does **not** renumber any existing revision:
the 0-versus-1 convention was chosen to match what the code already did, precisely so
that no migration had to exist solely to change a number.

Migration `7c41a9f2b6d3` adds `character_positions` and backfills one player row per
existing session — `at_location` where `current_location` names exactly one visible
place, `unlocated` otherwise. It is purely additive: no column is dropped, no existing
row is rewritten, and `game_sessions.current_location` is left exactly as it was.

## Future extensibility

New state domains attach the same way the existing five did, and the shape of the work
is now fixed:

1. A domain package with the types and their rules — no I/O, no ORM.
2. New mutation operations on `StateMutation`, with an authority policy.
3. A table keyed by `session_id`, with its own indexes and its own lifecycle.
4. A port method, an adapter method, and a materialisation step if the template
   declares a starting value.
5. A bounded, deterministic retrieval function for `StoryContext` — never "send it all".
6. A referential check in `state_consistency`.

`CharacterState` is the next one. It does **not** take position with it: position already
has a canonical home, and Character Foundation composes `character_positions` rather than
establishing a second authority beside it. Nothing about that requires the root to
change: a character's state is a table keyed by session and character, and the root will
still be four fields.

## Explicitly not built

Character Foundation, `CharacterState`, attributes, personality, relationships as state,
`KnowledgeState`, `BeliefState`, skills, progression, a power system, combat, inventory,
factions, reputation, a game RNG, autonomous world simulation, economy, weather, quests,
semantic or vector retrieval, event sourcing, snapshots, and rewind.

Each has a place to attach rather than an implementation, and the difference is
deliberate.

## Where the detail lives

- [world-state-time.md](world-state-time.md) — the clock, the calendar projection, scheduled events
- [world-state-facts.md](world-state-facts.md) — objective truth, property namespaces, authority
- [world-state-locations.md](world-state-locations.md) — places, containment, connections, spatial state
- [world-state-situations.md](world-state-situations.md) — ongoing processes, lifecycle, progression
- [event-resolution.md](event-resolution.md) — commands, resolvers, dispositions, history
- [world-rules.md](world-rules.md) — how a universe is configured
- [ai-contract.md](ai-contract.md) — what the Story Director sees and may propose
- [architecture.md](architecture.md) — layering, ports, transactions
