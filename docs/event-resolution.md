# Event / Resolution V1

Everything before this described the world. This describes the **door through which the
world changes** — and the record it leaves behind.

Four systems now say what is true (`world_facts`), where things are (`location_states`
and `character_positions`), what is under way (`situations`) and what time it is
(`elapsed_minutes`). None of them
said *how* any of it is allowed to change. That was the hole: the Story Director
proposed, reviewers accepted, and a fact appeared. There was no verdict, no audit trail,
no way to retry a turn safely, and no answer to "why is this so?" beyond "a model said
it once."

---

## The words, and why none is a synonym for another

```
player's sentence   what someone typed
Intent              what they were trying to accomplish        (not built)
Command             a validated request to attempt it
Resolution          evaluating that request against the world
Outcome             what the resolver calculated
GameEvent           something that objectively happened
StateMutation       an authoritative change to current state
ScheduledEvent      something that has not happened yet
Narration           prose describing outcomes already committed
```

Worked through one action:

```
player's sentence   "I force the east gate."
Command             ProgressSituationCommand / AdvanceTimeCommand / ...
Resolution          record #4f2a, disposition applied, revision 6 -> 7
Outcome             1 event, 3 mutations, 0 scheduled
GameEvent           east_gate_breached, importance 4, minute 720, sequence 31
StateMutation       location_states[east_gate].condition = destroyed
Narration           "The timber gives with a sound like a tree falling."
```

The distinction that costs the most to lose is the last one. **Narration comes after the
mechanics and describes them.** A system that reads prose to find out what happened has
made a language model the arbiter of who lives.

The second most expensive: `GameEvent` is not a log line. It is a small set of things
the story will still care about in fifty turns. See
[Significance](#significance-what-history-keeps).

---

## The pipeline

```
Trigger
    |
Command                       validated, typed, naming real ids
    |
load ResolutionContext        at revision N, bounded by the command
    |
Resolver                      pure: context in, outcome out
    |
ResolutionOutcome             a value; nothing has been written
    |
validate                      revision, rules, the whole projected change
    |
BEGIN TRANSACTION
    ResolutionRecord          the verdict, exactly once
    significant GameEvents    what history keeps, pointing at it
    StateMutationBatch        every authoritative change, together
    time advance              routed to the time service, never applied here
    scheduled events          what to look at later
    state_revision            moves once, or not at all
COMMIT
    |
narration                     afterwards, describing what already happened
```

`app/application/resolution_service.py`. **No partial world reality may remain.** A
failure anywhere before `COMMIT` leaves a session indistinguishable from one where the
command was never submitted. A failure after it is a narration problem, not a gameplay
one.

The write order inside the transaction is not arbitrary: the record is written first
because events carry its foreign key and it has to state how many events it produced.
That also means a mutation failing at the end takes the record and its events down with
it, which is what `test_one_failing_mutation_takes_the_record_and_its_events_with_it`
actually exercises.

### Being a trusted caller is not being an unchecked one

A resolution reaches `stage_state_change` directly. It does not pass through the Story
Director's proposal reviewer, and neither do ADMIN, ENGINE or the dev router — so
everything a proposal is checked for and the world depends on is checked again at the
mutation door, for every caller:

- **Every fact subject must name something that exists.** A `location` subject is read
  session-scoped and must resolve to a place this session can see; a `character` subject
  must be a character of this world; `faction` and `other` have no owning domain yet and
  are refused outright rather than accepted as an id nothing can check. See
  [A subject must name something that exists](world-state-facts.md#a-subject-must-name-something-that-exists).
- **A situation's parent must have existed before the batch began.** One batch cannot
  start a situation and nest a child inside it, because the child would have to name an
  id that does not exist until the batch is half-applied. See
  [A batch cannot nest a situation inside one it just started](world-state-situations.md#a-batch-cannot-nest-a-situation-inside-one-it-just-started).

The reviewer keeps its own copy of the location check and is not redundant: it can reject
one claim and let the turn continue, which is worth more than a refusal that ends the
turn. The boundary check is the one that cannot be bypassed.

### Resolution source

Every resolution knows what triggered it, and that is separate from what was attempted:

```yaml
ResolutionRequest:
  command: Command                  # what is being attempted
  source_type: ResolutionSourceType # who is attempting it
  source_id: UUID | null            # which row, when there is one
  parent_resolution_id: UUID | null
  idempotency_key: str
  expected_revision: int | null
```

```
player_action  scheduled_event  situation_progression  npc_action
faction_action  world_simulation  system  admin
```

Provenance lives on the request and never on the `Command`, because a resolver reads the
command — and must not be able to read, let alone influence, the authority its outcome
will be written under. `source_type` maps to a `FactAuthority` in one table in
`resolution_service.py`. `story_director` is deliberately absent from that mapping: the
language model has no `ResolutionSourceType` because it does not resolve anything.

`player_action` maps to `player_resolution`, not to something broader. **Player privilege,
where a world wants it, belongs in that world's rules** — an engine that quietly gave the
player more reach than the simulation would be privilege by accident.

---

## ResolutionRecord

One row per verdict, in `resolutions`.

```yaml
Resolution:
  id, session_id

  source_type, source_id
  parent_resolution_id            # for a future compound action's children
  idempotency_key                 # unique per session

  disposition: applied | rejected | no_effect
  reason_code: string | null      # required when rejected

  resolver_name, resolver_version # which formula, and which version of it

  state_revision_before
  state_revision_after            # equal, or exactly one higher

  occurred_at: int                # fictional minute
  turn_index: int | null

  event_count, mutation_count
  created_at                      # wall clock, no simulation meaning
```

**Narration is not here.** The prose lives in `messages`, pointing back with
`messages.resolution_id`. One table owns the mechanics and another owns the words, so a
regenerated paragraph cannot drift from the audit trail — and a mechanical audit does
not carry a copy of every narrator sentence.

There is no `detail` blob, no free-form `notes`, no `payload`. Only fields something
reads today.

### Three dispositions, and none of them is `success`

```
applied     the attempt happened and changed something
rejected    the world's rules refused it; nothing was attempted
no_effect   legitimate, and nothing changed
```

`failure` is deliberately absent, and this is the distinction the vocabulary exists for:

| | what happened | disposition |
|---|---|---|
| Lockpick snapped in the lock | the attempt occurred and went badly | `applied` |
| The world has no locks | the rules refused the attempt | `rejected` |
| The door was already open | legitimate, nothing to change | `no_effect` |

**A rejected action is not a failed attempt.** The first never happened; the second
happened and hurt. Collapsing them into `success: false` gives both the same prose, the
same history and the same consequences, and the player cannot tell "you missed" from
"that is not a thing you can do here."

`no_effect` is not an error either. Most situation progressions on a quiet clock are
`no_effect`, and so is most of what a conversational turn does.

### Disposition is not the revision

`changed_state` reads `state_revision_after != state_revision_before`, never the
disposition. An `applied` resolution that only wrote history did not change authoritative
state, and the revision is the thing that tells an optimistic-concurrency check whether
its view went stale — it must answer that question honestly regardless of what the verdict
was called.

---

## Resolver / application separation

```
Resolver         pure. context in, ResolutionOutcome out. No I/O, no database.
Application      loads the context, validates the outcome, commits it.
```

A resolver **calculates**. It does not commit, does not open a transaction, does not
write an event, does not touch the clock. `ResolutionOutcome` is a value: a resolver that
returns one has changed nothing, and every one of these tests that needs an impossible
outcome simply constructs one.

Registration is a dictionary keyed by command kind, in `app/application/resolvers.py`:

```python
register_resolver("advance_time", TimeAdvanceResolver())
register_resolver("progress_situation", SituationProgressionResolver())
```

Two resolvers, one dictionary, no plugin framework, no reflection, no entry points, no
dynamic import. When there are twenty resolvers and a real reason, that is the moment to
build the mechanism — not before. **Nothing branches on a preset name**, and nothing may:
a resolver reads world *rules*, and a rule is data.

### What a resolver may see

`ResolutionContext` is loaded per command, bounded by a `ContextRequest` the command
declares. The time resolver gets the clock and the world rules. The progression resolver
gets the clock, the rules and *one* situation. Neither can see the transcript, the
memories, the relationships, or the other nine situations.

**The whole world is never loaded.** A resolver that receives everything grows a
dependency on everything, and the first performance problem then has no smaller unit to
optimise.

---

## State revision

`game_sessions.state_revision`, one integer, moving forward only.

- One committed resolution that changes authoritative state increments it **exactly once**,
  whatever it touched. Three mutations across facts, space and situations is one change to
  the world's version.
- A resolution with no mutations does not move it — including one that wrote history.
- `rejected` and `no_effect` do not move it.
- A rolled-back resolution does not move it, because the increment was inside the
  transaction that vanished.

A caller may pass `expected_revision`. A mismatch raises `StaleStateError` **before the
resolver runs** — nothing is loaded, nothing is computed, nothing is written. Silently
applying a decision reached against state that has since changed is the failure mode this
exists to make impossible.

The three session counters remain independent and none can be computed from another:

```
turn_index        how many exchanges have been played
elapsed_minutes   what time it is in the story
state_revision    how many times authoritative state has changed
```

---

## Idempotency

**Mandatory, not an optimisation.** `(session_id, idempotency_key)` is unique in the
database — a constraint, not a Python check, so two concurrent retries of one submission
race and the loser gets an `IntegrityError` rather than both writing a resolution.

A retry that finds an existing record:

- does **not** run the resolver again
- does **not** call a language model again
- will **not** consume RNG again, when there is RNG
- does **not** write a second `GameEvent`
- does **not** apply the mutations a second time
- does **not** advance the clock a second time
- returns the original result, marked `replayed`

Keys are namespaced by the thing that mints them, so a client cannot choose a string that
collides with one the engine generates:

```
turn:{client_action_id}             a player turn
turn-index:{n}                      a turn submitted without a client id
dev:progress:{situation_id}:{now}   the developer progression endpoint
```

Those three are the keys anything currently mints. Every other caller of `resolve()`
supplies its own, and the uniqueness constraint is what enforces the rule rather than any
convention about prefixes.

The key is the **caller's** stable name for one attempt, and it must survive a transport
retry unchanged. The server cannot tell — and must not guess — that two submissions were
meant to be one. The frontend mints a `client_action_id` where the player acts and reuses
it for every retry of that submission; the retry button re-sends the id it already had,
and a genuinely new action gets a new one. See `apps/web/src/pages/SessionPage.tsx`.

A turn submitted with no `client_action_id` is still recorded, under a key derived from
the turn index. It is simply not replayable: a retry arrives with no id, matches nothing,
and plays a second turn.

---

## GameEvent

```yaml
GameEvent:
  id, session_id
  resolution_id: UUID | null        # which verdict wrote this
  turn_index: int

  category: EventCategory           # closed enum: action, world, character, ...
  subtype: string                   # open: bridge_collapsed, oath_sworn
  summary: string                   # one line, for people. Never parsed.

  occurred_at: int                  # fictional minute
  sequence: int                     # monotonic per session, unique
  importance: 1..5

  primary_location_id: UUID | null
  caused_by_event_id: UUID | null   # causality across resolutions
  payload: {}                       # small, flat, scalar
  created_at                        # wall clock, no simulation meaning
```

`category` is a closed enum because retrieval filters on it. `subtype` is an **open
string** because the alternative is an enum of several hundred event names that is wrong
the first time anyone adds a system. Unregistered subtypes are kept and get the
conservative default policy.

The model is frozen. There is no update path and no delete path, in the domain, in the
ports (`EventWriterPort` has `add_event` and nothing else), in the application, or over
HTTP. **A correction is a new event**, usually pointing at the old one with
`caused_by_event_id`. A bridge that was rebuilt does not un-collapse: the valley was cut
off for a while, and the history says so however the story turned out.
`test_no_application_code_updates_or_deletes_a_persisted_event` walks the source tree to
keep it that way.

### Significance: what history keeps

`GameEvent` is **not a logging mechanism**. `logger.info` is the logging mechanism, and
it writes to a file nobody replays.

Each subtype has an `EventPolicy` deciding whether it is persisted at all and what
importance band it may occupy:

```
none        not written. It happened; history does not need it.
history     written, and read back by importance and recency.
landmark    written at the top of the scale. The story's spine.
```

| subtype | persistence | default | band |
|---|---|---|---|
| `door_opened` | `none` | — | — |
| `time_advanced` | `none` | — | — |
| `situation_progressed` | `none` | — | — |
| `world_state_seeded` | `history` | 1 | 1–1 |
| `secret_discovered` | `history` | 3 | 1–5 |
| `character_died` | `history` | 4 | 3–5 |
| `major_character_died` | `landmark` | 5 | 5–5 |
| *(unregistered)* | `history` | 2 | 1–3 |

Importance proposed by anything — including the Story Director — is **clamped into the
band, never taken on trust**. A proposer that rates everything 5 gets everything clamped
back down; `major_character_died` filed at 1 is stored at 5; `door_opened` at 5 is not
stored at all; an unregistered subtype cannot promote itself past 3 into landmark
territory. A model asked to rate what it has just written rates all of it highly, which
is exactly why the scale is not its to set.

Historical importance is never rewritten. A death that mattered enormously and then stopped
mattering keeps the importance it was written with — retroactively re-scoring history would
make "what has happened in this story?" depend on when you asked.

Deduplication is **per-subtype and opt-in** via `dedupe_window_minutes`, and nothing opts
in yet. A universal deduplicator would remove genuinely distinct events: two people can
die in the same minute, and a system that decides otherwise has silently deleted one of
them.

### Ordering

Two keys: `(occurred_at, sequence)`.

`occurred_at` is a fictional minute, and ties on it are the **normal** case — everything
one turn records usually shares one. `sequence` is a monotonic per-session counter, unique
in the database, which is why ordering is total.

`created_at` is a wall clock and decides nothing. A row written later is not a thing that
happened later: a save played over an afternoon can record minute 900 before minute 100 if
a timeskip was resolved before a flashback was written down. **Seconds are not invented
merely for ordering** — the clock has minutes, and inventing a false precision to sort by
would put a lie in a column that later systems would read as real.

### Causality

```
caused_by_event_id   across resolutions: this happened because that did
resolution_id        within one: these events came from this verdict
parent_resolution_id one resolution belongs to another
```

Three links, three different questions. Nothing produces a `parent_resolution_id` yet; it
is stored and read back so the first compound action does not have to add a column to a
table full of history.

`world_facts.source_event_id` and `situations.source_event_id` already point into history,
and both are `ON DELETE SET NULL`: provenance can decay, truth cannot.

---

## Narration

**After the resolution, describing what has already been committed.** Never before, never
instead.

```
POST /api/v1/sessions/{id}/resolutions/{resolution_id}/narration
     { "regenerate": false }
```

A POST rather than a GET because it can call a language model and write a message. Safe to
retry regardless: without `regenerate`, an outcome that already has narration returns it
unchanged **and no provider runs**.

The `OutcomeContext` a narrator receives carries the world, the player, the time, the
disposition, the reason code, the resolver's own `narrative_context` and whatever history
kept. It does not carry the rules, the geography, the relationships or the secrets — this
is a description job, not a turn.

### Failure after commit

If the provider is down when narration is attempted, the outcome is exactly as committed as
it was a moment earlier:

- authoritative state stays committed
- the `ResolutionRecord` stays committed
- the `GameEvent`s stay committed
- `state_revision` does not change, then or on retry
- retrying narration does not create a second resolution

What is missing is a paragraph. The failure surfaces as a 502 naming the provider, like
every other provider failure — never swallowed, never replaced with a placeholder sentence
that would become canon on the next turn.

### Regenerating narration is not rerunning resolution

**Regenerating narration may be allowed. Rerunning mechanical resolution must not happen
implicitly.**

`regenerate=true` calls the provider again and *replaces* the stored paragraph — the same
message row, not a second one. Two descriptions of one moment in the transcript would both
be read as canon on the next turn.

Nothing in the endpoint can re-resolve anything: the request body is one boolean and it is
about prose. Re-running mechanics because prose disappointed someone would let a player
reroll an outcome by pressing a button labelled "try again".

---

## ScheduledEvent is not a GameEvent

```
GameEvent       something that happened.       Past. Immutable.
ScheduledEvent  something expected to happen.  Future. Has a status.
```

A scheduled event is a **request to evaluate something later**. It is not history and must
never be written into it.

Time advancement owns the chronology, and only the chronology. `stage_time_advance` walks
the events the interval reached, marks each `due` through the same transition rules
everything else uses, returns their ids, and stops at the first one flagged as
interrupting.

**Reaching an event is not resolving it.** `due` means the clock arrived and nobody has
answered yet; `processed` means the work the event owned was actually carried out. Nothing
routes a due event into `resolve()`, so a due scheduled event produces no ResolutionRecord
today — and it stays `due`, visible to `load_due_work`, until something executes it and
calls `complete_scheduled_event` in the same transaction as whatever the work changed. An
interrupting event nobody answers stops the clock at its minute every time it is asked to
advance. See [DUE is not PROCESSED](world-state-time.md#due-is-not-processed).

The seam is deliberately left in place and unused: `ResolutionSourceType` already carries
`scheduled_event`, and a resolver keyed on the scheduled event's own id would drop into the
existing pipeline without changing it. Wiring that up belongs to whatever system first
needs the world to act on its own — see the non-goals below. **`elapsed_minutes += X` is
not a generic StateMutation** and there is no mutation type that can express it: Time V1
owns the clock, a resolver *requests* an advance, and the resolution service routes that
request to the time service rather than applying it.

There is **no event bus.** No queue, no broker, no subscribers, no recursive dispatch. A
resolution may schedule work; scheduled work is picked up by an explicit call. Kafka,
RabbitMQ and an in-process pub/sub are all the same mistake at different scales here — the
thing that goes wrong is an event cascade nobody can trace or bound.

---

## Technical failure is not a gameplay outcome

A database error, a bad command, a resolver crash, an unreachable provider: those
**propagate**. Nothing writes a `rejected` row to describe them.

A `rejected` resolution is a permanent claim that the world's rules said no. Manufacturing
one out of a timeout puts a lie in the audit trail that the next resolution reads back as
fact, and tells a player "the world refused" when the truth is "our code broke". After a
technical failure there is no record, no event, no mutation and no revision increment — so
the same submission arriving again is a first attempt, not a replay of a crash.

---

## What reaches the Story Director

Two bounded bands, in `context_builder.py`:

```text
# What has already happened  (authoritative: settled, not open to revision)
This story so far:
- [character] King Aldric is dead. (3 days ago)
Lately:
- [world] The east gate gave way. (an hour ago)
```

**Landmarks** (importance ≥ 4, at most 8) are the story's spine — a siege that began forty
turns ago still shapes every scene. **Recent** (importance ≥ 2, at most 12, landmarks
excluded) is what a character would actually mention. Anything below that is written,
readable over HTTP, and simply not prompt material.

Neither band grows with how long the save has been played. **Not all GameEvents are loaded
into StoryContext**, and no amount of play makes the prompt longer.

`resolutions` is **not** in the context at all. Idempotency keys, resolver versions and
revision numbers are engine bookkeeping; the director is told what happened, not how the
engine decided it. A trivial resolution that nudged a siege's intensity contributes nothing
to the prompt, which is the entire reason the two trails are separate tables.

---

## HTTP surface

```
POST /api/v1/sessions/{id}/turns
       { "action": "...", "client_action_id": "..." }
GET  /api/v1/sessions/{id}/resolutions?source_type=&limit=
GET  /api/v1/sessions/{id}/events?category=&min_importance=&limit=
POST /api/v1/sessions/{id}/resolutions/{resolution_id}/narration
```

Both reads are read-only and bounded by `limit` rather than pageable: these are inspection
views, not exports, and an endpoint that could stream every event of a long session is the
one a client would use to build a prompt out of all of them.

**There is no write counterpart to either.** No endpoint creates a resolution, and none
creates, edits or deletes an event. A client that could POST a resolution could assert the
world changed without anything having decided that it did; a client that could PATCH an
event could rewrite history. Both are asserted by a test that inspects the OpenAPI
document, so a write endpoint appearing here is a failing build.

---

## Persistence

```
resolutions
  UUID pk, session_id FK -> game_sessions ON DELETE CASCADE
  UNIQUE (session_id, idempotency_key)          uq_resolutions_session_idempotency
  INDEX  (session_id, source_type)              ix_resolutions_session_source
  INDEX  (session_id, occurred_at, created_at)  ix_resolutions_session_time
  INDEX  (parent_resolution_id)

game_events
  UNIQUE (session_id, event_sequence)           uq_game_events_session_sequence
  INDEX  (session_id, occurred_at, event_sequence)  ix_game_events_session_time
  INDEX  (session_id, category, subtype)        ix_game_events_session_category
  INDEX  (session_id, importance, occurred_at)  ix_game_events_session_importance
  INDEX  (resolution_id)
  caused_by_event_id -> game_events ON DELETE SET NULL

messages
  resolution_id -> resolutions ON DELETE SET NULL
```

Alembic owns the schema; **nothing relies on `create_all` at startup**, so a stale dev
database surfaces as a warning rather than being silently papered over. The migration
`65bea0eded6c` creates `resolutions` and reshapes `game_events` — `type` became
`subtype`, free text became a `category` of `other`, and existing rows kept their order
and their identity. `world_facts.source_event_id` and `situations.source_event_id` both
point into the rebuilt table and both survive the rebuild, which is what
`test_reshaping_game_events_keeps_the_rows_that_point_at_them` proves.

---

## This is not event sourcing

Current state lives in current tables: `world_facts`, `location_states`, `situations`,
`game_sessions.elapsed_minutes`. `game_events` is significant history **alongside** them,
not a log the state is rebuilt from.

Nothing replays the stream. Nothing may come to require replaying it. The day something
does, every event ever written becomes load-bearing forever, event schema migration
becomes a correctness problem rather than a housekeeping one, and the significance policy
above becomes impossible — because an event you chose not to persist is state you have
destroyed.

There are no snapshots, no projections, no rewind and no time travel. `ResolutionRecord`
is the mechanical audit trail, which is what those features were going to be asked for.

---

## Seams left open, deliberately

**RNG.** No dice, no seeded generator, no skill checks. When there is one, a resolution
gains a seed and an audit policy — `none` for most sessions, `important` for landmark
verdicts, `full_debug` for development. The place it hooks in is already there: a resolver
is pure, so its randomness has to arrive as an argument, and the replay path already
guarantees a retry does not draw twice.

**Compound actions.** `parent_resolution_id` is stored and read back. One player sentence
producing several resolutions is a real design question — the whole set has to be atomic, or
explicitly not — and it is not being answered here.

**Reactions.** No immediate/deferred reaction pipeline. When there is one, "the guard
notices" is a new Resolution with `caused_by_event_id` pointing at what it reacted to, and
the depth bound is the first thing that has to be decided.

**Intent interpretation.** Free text is not parsed into a `Command`. A turn's record is
attributed to `resolver_name="turn"`, and it deliberately does not claim the prose resolved
anything mechanically — the director proposes, the reviewers decide, and the record
describes that review.

---

## Deliberately not implemented

A full Intent Interpreter. A complete Command hierarchy. Skills, skill checks, a game RNG,
combat, `CharacterState`, inventory, a power system. NPC autonomy. Faction simulation. A
complete world simulation. A full reaction engine. Event sourcing, snapshots, rewind.
Long-term summary generation. Semantic event retrieval.

This task built one resolution boundary, one audit trail and one history model. Everything
above is what plugs into them.

---

## Related

* [Simulation time](world-state-time.md) — the clock every temporal field here uses
* [World facts](world-state-facts.md) — what a `StateMutation` is allowed to change
* [Locations](world-state-locations.md) — spatial state, and its own mutations
* [Situations](world-state-situations.md) — the processes progression resolves
* [AI contract](ai-contract.md) — the proposal boundary, and `world_events`
* [Architecture](architecture.md) — ports, transactions and the layering rule
