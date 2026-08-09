# WorldState: facts and state changes

A story needs a record of what is *so*. Not what a character believes, not what the
last three paragraphs of narration implied, not what the model would say if asked
again — what the game asserts is objectively true in this session right now.

Without one, "the north bridge collapsed" survives exactly as long as it stays inside
the prompt window. Thirty turns later a model with no reason to think otherwise walks
someone across it, and there is nothing in the system that can say it was wrong.

This is the second piece of `WorldState`, after [the clock](world-state-time.md). It
ships the fact store and nothing else: no inventory, no hit points, no skills, no
combat, no economy, no autonomous simulation. Those all need somewhere to keep what is
currently true, so that goes in first.

## Five things that are not each other

| | what it is | example |
|---|---|---|
| **WorldFact** | current objective truth | `character:elena system.alive = false` |
| **GameEvent** | something that happened, immutably logged | `DEATH — Elena fell at the ford` |
| **StateMutation** | a requested change to a fact | `SetFact(elena, system.alive, false)` |
| **Memory** | what a character recalls, fallibly | "I saw Elena go down" |
| **Belief** | what a character holds true, possibly wrongly | "Elena is still alive" |

Events accumulate and are never edited. Facts are overwritten and describe only *now*.
A mutation is the request; the fact is the result. Memories and beliefs are separate
systems that do not exist yet and deliberately do not live in this table — see
[Knowledge is not truth](#knowledge-is-not-truth).

## Objective truth, and nothing else

A `WorldFact` is what the game says is true. Not what the player knows, not what an NPC
knows, not what anyone has been told.

That cuts both ways, and the second direction matters more:

> **Do not force metaphysical truths the world has not established.**

If nobody has decided whether the gods are real, there is no fact recording that they
are *not*. An unestablished question is unestablished. It is not false, it is not null,
and writing either would be the store inventing a truth the story never chose.

## Absence, null, and false are three states

```text
no row                 nothing has been established about this
value = null           established: this has no answer
value = false          established: this is not so
```

These are never collapsed. `store.get_fact(...)` returning `None` means absent; a fact
whose `value` is `None` means an established null. A character with no
`system.alive` fact is not dead, and is not alive either — nothing has happened to them
yet, which is the ordinary state of most characters most of the time.

## Identity: subject + property

One fact is identified by *what it is about* and *which property of it*:

```text
(session_id, subject_type, subject_id, property)
```

Nothing else is part of identity. Not the value, not the kind, not who wrote it.

**Subjects** are `world` (which takes no id — there is one world per session),
`character`, `location`, `faction` and `other`. Every type but `world` requires an id.
A subject is never a display name: two characters named Elena are two subjects, and a
character who is renamed is the same one.

**Properties** are `namespace.snake_case`, and the grammar is enforced rather than
suggested:

```text
system.alive
system.location
world.political_status
narrative.birthplace
gameplay.palace_secret_discovered
```

`birthPlace`, `Narrative.Birthplace` and `where she was born` are all rejected outright
rather than normalised. A tolerant parser is exactly how one logical property quietly
becomes three, and the uniqueness constraint below can only prevent a contradiction
between two facts it can see are about the same thing.

A small hand-maintained alias table (`PROPERTY_ALIASES`) folds spellings we know are
the same field — `favourite_food` → `favorite_food`, `place_of_birth` → `birthplace`.
There is deliberately no attempt to decide that `narrative.where_she_was_born` means
`narrative.birthplace`: that is semantic equivalence, it needs a language model to
guess at, and a wrong guess silently overwrites an unrelated fact.

### One current value, enforced by the database

It must not be possible to persist both of these at once:

```text
character:aldren  system.alive = true
character:aldren  system.alive = false
```

That is enforced by unique indexes, not by application code remembering to look first.
There are **two** of them, split on whether `subject_id` is null:

```sql
UNIQUE (session_id, subject_type, subject_id, property) WHERE subject_id IS NOT NULL
UNIQUE (session_id, subject_type, property)             WHERE subject_id IS NULL
```

The split is not stylistic. SQL treats two NULLs as distinct, so a single index over
`subject_id` would not constrain world-scoped facts *at all* — every
`world.political_status` row would be considered unique and the invariant would hold
for characters and silently fail for the world. A sentinel UUID for "the world" was the
alternative and was rejected: it puts a fake id in every row to work around one
property of one index.

`kind` is deliberately **not** part of the key. Including it would let the same subject
and property exist once as `world_truth` and once as `gameplay_flag` with opposite
values, which is the exact contradiction the key exists to prevent.

## Namespaces and kinds

Namespaces describe *who owns* a property:

| namespace | meaning |
|---|---|
| `system.` | mechanical state owned by deterministic game systems |
| `world.` | diegetic truth about a place, object, or the world |
| `narrative.` | colour established through storytelling |
| `gameplay.` | internal progression flags |
| `derived.` | computed from other state, never stored |

`kind` is a separate axis and answers a different question — whether the fact is a
statement about the fiction (`world_truth`) or an engine flag (`gameplay_flag`). "The
king is dead" is world truth. "The palace secret has been discovered" is a gameplay
flag: true of the save, not a sentence anyone in the world would say.

## Importance

`1..5`, and it affects **presentation only**. It decides which facts reach the prompt
when there are more than fit, and whether they are presented as "must not be
contradicted" or as colour. It confers no authority and changes no truth: an
importance-1 fact is exactly as true as an importance-5 fact.

## Authority and policy

Two halves of one decision. A **policy** is a property of the property; an
**authority** is who is asking.

| policy | who may write it |
|---|---|
| `OPEN` | anyone, including the Story Director |
| `GUARDED` | game systems, seeding and admin — not the Story Director |
| `SYSTEM` | game systems, seeding and admin |
| `DERIVED` | **nobody** |

| authority | what it is |
|---|---|
| `engine` | deterministic core systems |
| `simulation` | offscreen world simulation |
| `player_resolution` | the outcome of a resolved player action |
| `story_director` | the language model |
| `seed` | world template materialisation |
| `admin` | development and repair tooling |

The load-bearing row is `story_director` × `OPEN`. **The Story Director has the lowest
authority over mechanical state of anything in the system**, and the model never writes
the database at all — see [Proposals](#the-model-proposes-the-game-decides).

Unregistered properties fall back to their namespace: `world.anything_at_all` is
`GUARDED`, `system.*` and `gameplay.*` are `SYSTEM`. **Inventing a name grants
nothing.** An unknown property never becomes `OPEN` by accident, which is what stops a
model from reaching mechanical state by choosing a word nobody registered.

Nobody writes `DERIVED`, including admin. `derived.is_night` is a function of the clock;
a stored copy could only ever disagree with it.

## Provenance

Every fact carries the authority that wrote it and, usually, the `GameEvent` that
caused it. Mechanical authorities (`engine`, `simulation`, `player_resolution`) **must**
name an event — a world that cannot say why something is true has provenance in name
only.

Three authorities are documented exceptions:

- `seed` and `admin` — a starting configuration is not the consequence of anything that
  happened in the story, and neither is a repair.
- `story_director` — a turn is already the event that produced the fact. Minting a
  `FACT_CREATED` row for "Elena dislikes olives" would bury the history that matters
  under the history that does not.

`source_event_id` is `ON DELETE SET NULL`: provenance can decay, truth cannot.

## When a value became true

`current_value_since` is the **fictional minute** the current value became true, not the
wall-clock moment the row was written. Both matter and they are different numbers, so
`created_at`/`updated_at` remain alongside it. There is deliberately no redundant text
copy of the date — the calendar is a projection of the clock, exactly as in
[world-state-time.md](world-state-time.md).

## Mutations and batches

Two operations, and only two:

```python
SetFact(subject=..., property=..., value=..., importance=3, kind=..., tags=())
RemoveFact(subject=..., property=...)
```

`RemoveFact` means **the property is no longer established** — it returns the fact to
absence. It does *not* mean false. It should be rare: most changes are a new value, and
withdrawing a fact is a statement that the world no longer has an answer to a question
it once had. Removing a property that was never there is refused, because a caller that
believes it is there and is wrong should find out.

There is **no `previous_value` field**, on either operation. A caller-supplied "what it
used to be" is a claim the store would have to either trust or re-check, and re-checking
makes it redundant. The store reads.

A `StateMutationBatch` carries an authority, one to fifty mutations, and an optional
`expected_revision`. Two mutations touching the same fact in one batch is refused:
which one wins would be an ordering accident.

## Atomicity

```text
resolution decides something happened
        ↓
build a GameEvent + a StateMutationBatch
        ↓
validate the whole batch          ← nothing has been written yet
        ↓
persist the event
apply every mutation
increment the state revision
        ↓
commit
```

Validation runs to completion **before the first write**. That ordering is why the "one
mutation failed" case is usually not a rollback at all — it is a refusal, and the
transaction never started doing anything to undo.

The transaction is still the guarantee, because a failure *after* validation — a
constraint, a lost connection — must not leave an event claiming something that did not
happen to the world. If any mutation fails, everything unwinds: the event does not
remain persisted as though it occurred.

`app.application.state_service` is the only door. Nothing else writes facts: not a
router, not the turn service directly, and certainly not a story provider.

## The state revision

`GameSession.state_revision` starts at 0 and increases by exactly one per committed
batch. It never decreases, and it is a **third independent counter**:

| | moves when |
|---|---|
| `turn_index` | the player takes a turn |
| `elapsed_minutes` | the game advances the clock |
| `state_revision` | a batch of state changes commits |

A turn of pure conversation moves only the first. Seeding a new session moves only the
third.

`expected_revision` is an optional optimistic check: a batch that names a revision the
session has moved past is refused with a 409 rather than applied on top of something it
was not decided against. It is deliberately *not* a concurrency framework — this is a
single-player application that commits one request at a time, and the field exists so
that the day that stops being true, the hook is already where callers can find it.

## Initial facts: the world template

A world may declare `initial_facts` — a list of `SetFact` documents describing what is
already true before anyone plays it. When a session is created, they are materialised
into that session's own rows as one `seed` batch, in the same transaction that creates
the session.

**The template is not live state.** Each session gets its own copies and diverges
immediately: killing the king in one save leaves the template, and every other save,
untouched. There is no code path that writes back to a world's `initial_facts` during
play, and adding one would be a design change, not a feature.

Template facts are ordinary `SetFact` documents rather than a separate seed type,
because a template fact *is* a mutation waiting for a session to apply it. Giving it its
own model would mean two shapes to validate and two ways for one of them to drift.

### Known limitation

`POST /worlds` runs before the world has any characters, so a template written through
the API can only address the world itself — a character-scoped starting fact needs an id
that does not exist yet. `app/scripts/seed_demo.py` shows the shape that works: create
the world, create the characters, then write `initial_facts` in the same transaction.
There is no template-editing endpoint, and adding one is a deliberate future decision
rather than an oversight.

## The model proposes, the game decides

The Story Director never writes a fact. It returns `fact_proposals`, and each one runs
this gauntlet:

```text
subject the game can resolve
    → canonical property name
    → the property's policy allows story_director
    → nothing is already established there
    → the world's rules permit it
    → accepted
```

Most proposals are refused, and a refusal is not an error: the turn continues, the
player sees nothing, and the rejection is logged. A malformed proposal is discarded
before validation rather than failing the whole `TurnGeneration` — a 502 over a detail
the model was not obliged to send at all would roll back a turn of real prose.

Three refusals worth naming:

- **Anything already established.** Once `narrative.birthplace` is Arven, a proposal of
  Valeria is refused. That is the structural contradiction check: same subject, same
  canonical property, different value. It is not a judgement about which is better —
  the store holds current truth and the story already committed to one.
- **The same value again.** Refused too, for a duller reason: it would move the state
  revision without moving the world.
- **Subjects nothing can check.** The director may only speak about the world and about
  characters, because those are the only ids this application can resolve today. A
  proposal about `location:<uuid>` names an entity nothing can confirm exists.

The model also has no *shape* in which to express a replacement WorldState: the contract
is a list of individual claims about single properties, which is what can be adjudicated
one at a time. And `kind` is decided by the reviewer, always `world_truth`, so a model
cannot set a gameplay flag by relabelling what it is doing.

## The world's rules outrank everyone

`WorldRules` decides what *can* be true; the fact store records what *is*. A mutation
is checked against the world's rules regardless of who is asking — **no authority is
exempt, including admin**:

- something supernatural in a world with no supernatural → refused
- `system.alive` false → true where death is final → refused
- an NPC dying in a world where NPCs cannot die → refused

Nobody is authorised for these, because the universe does not work that way.

## Contradictions

The only contradiction detection here is **structural**: one subject, one canonical
property, one current value, enforced by the unique indexes. Two facts that disagree in
prose — `world.condition = "at peace"` alongside `world.political_status = "civil war"`
— are not detected and are deliberately out of scope. Deciding that two sentences
conflict needs semantic analysis, it would need a language model to do it, and a wrong
answer silently deletes a truth somebody established. Structured identity is what this
table is for.

## HTTP surface

```text
GET  /api/v1/sessions/{id}/world-state/facts     read current state
GET  /api/v1/worlds/{id}/initial-facts           read a world's template
POST /api/v1/dev/sessions/{id}/world-state/changes   development only
```

There is **no gameplay CRUD over facts**. The read endpoint is the only non-development
one, and that is the point: a client that could POST a fact could post `system.alive`,
and the authority model would be decoration. The development endpoint is mounted only
when `APP_ENV` is in a short allowlist, goes through `apply_state_change` like every
other caller, and does not lift the authority model — `story_director` sent there still
reaches `OPEN` and nothing else.

The read response carries `state_revision` alongside the facts, because that is the
number a caller sends back as `expected_revision`, and a revision read in a different
request than the facts it describes would defeat the point of having it.

## Deliberately not built

Named here so the absence is a decision rather than a gap:

- **A `WorldState` aggregate.** No object that loads every fact for a session and hands
  it around. Facts are queried; a god object is what this design exists to avoid.
- **`CharacterState`, `LocationState`, `FactionState`.** Domain aggregates over these
  rows, when there are systems that need them.
- **`KnowledgeState` and `BeliefState`.** See below.
- **`SceneState`.** Ephemeral per-scene state — who is in the room, what is on the
  table, the current lighting — does not belong here. It has a different lifetime
  (a scene, not a save), a different owner, and a different failure mode. Writing it
  into a durable fact table would fill a session's permanent truth with furniture.
- **Inventory, hit points, skills, combat, magic, economy.** Each is its own system
  with its own rules; the `system.` namespace has names reserved so they can arrive
  without a migration of meaning.
- **Autonomous offscreen simulation.** The `simulation` authority exists; nothing calls
  it yet.
- **Event sourcing.** Facts are current values, not a fold over a log. `GameEvent` is an
  audit trail, not the source of truth.
- **Embeddings and a general knowledge graph.**

### Knowledge is not truth

`WorldFact` has no `known_by`, no `believed_by`, no `confidence`, and will not grow
them. Who knows what is a different system with different mechanics: knowledge spreads,
decays, is lied about and is wrong, and none of that is true of objective reality.
Folding the two together makes both worse — you either end up with a truth table that
cannot express a rumour, or a rumour table that a game system reads as truth.

A character acting on something false is normal and desirable. That belongs to
`BeliefState` when it arrives, and this table stays the thing it can be checked against.

## Code map

```text
app/domain/world_facts/       values, properties, policy, authority,
                              facts, mutations, compatibility
app/application/state_service.py    the only door to writing facts
app/application/fact_proposals.py   reviewing what the model claims
app/application/persistence.py      WorldStatePort, NewFact
app/infrastructure/db/models.py     the world_facts table and its indexes
```

Tests: `tests/test_world_facts.py` (domain), `tests/test_world_state.py` (service and
adapter against a real database), `tests/test_fact_proposals.py` (the review),
`tests/test_world_state_api.py` (HTTP).
