# AI contract

Everything exchanged with a story provider is typed and validated. The model proposes;
the application layer decides what is persisted.

## Authority

Everything in a `StoryContext` arrives with a different claim on the truth, and the Story
Director's job depends on telling them apart. A siege is not a breach, a breach is not a
ruined gate, and a guard *saying* the gate is ruined is none of the three.

| What it is | What it claims |
|---|---|
| `world_rules` | **the boundary of the possible.** Not a fact about this session — a statement of what may ever be true in this universe. Nothing narrated may exceed it. |
| current state — facts, geography, situations, the clock | **authoritative now.** These models, not the transcript, are what is currently true. Where prose and state disagree, state wins. |
| `history` — `GameEvent` | **significant occurrences that already happened.** Settled, not open to revision, and deliberately not everything that happened. |
| `situations` | **processes still running.** A siege is a thing in progress with a direction — not the event that started it, and not the damage it left behind. |
| scheduled events | **future triggers, not completed facts.** A thing that is *due*. Nothing may be narrated as having happened merely because it was scheduled. |
| `recent_messages` | **speech, which may be wrong.** A character can be mistaken, ignorant, or lying. A transcript line is evidence that something was *said*, never that it is true. |

The director's own authority is narrower than the context might suggest:

- **It may not rewrite current state.** No field in `TurnGeneration` sets a fact, moves a
  thing, ends a situation or advances the clock. The absence is the enforcement — see
  [world-state.md](world-state.md#who-may-change-what).
- **It narrates outcomes that are already committed.** Narration runs *after* the
  resolution's transaction has closed. The prose describes a verdict; it never produces
  one, and regenerating a paragraph cannot change what happened. See
  [event-resolution.md](event-resolution.md).
- **Its proposals are proposals.** New facts, places and situations arrive as
  `fact_proposals`, `location_proposals` and `situation_proposals`, and every one is
  reviewed against policy, authority and the world's rules before anything is written. A
  rejected proposal is normal operation, not an error.
- **What a character knows is not modelled yet.** Every character currently shares one
  context, secrets included, and the prompt asks for a discretion it cannot enforce. A
  future `KnowledgeState` will make viewpoint knowledge structural. Until then, treat NPC
  secrecy as flavour rather than as a boundary.

## StoryContext — what the model sees

Built by `application/context_builder.py`. A provider receives this and nothing else —
no database session.

| Field | Contents | Bound |
|---|---|---|
| `world` | name, description, genre, setting, **language** | — |
| `world_rules` | the world's rules, projected and flattened | — |
| `player` | name, description | — |
| `session` | title, current location, summary, turn index | — |
| `time` | fictional date, hour, part of day, elapsed since start | — |
| `relevant_characters` | full profiles incl. goals and secrets | 12 |
| `recent_messages` | transcript, oldest-first | 20 |
| `relevant_memories` | importance desc, then recency | 30 |
| `relationships` | current axis values per character | — |
| `history` | what has already happened, in two bands | 8 + 12 |
| `player_action` | this turn's raw input | 2000 chars |

Retrieval is pure SQL ordering — deterministic and reproducible. The same context always
produces the same mock turn, which is what makes the E2E flow assertable.

**Secrets are included deliberately.** The director needs them so an NPC can act on,
deflect, or lie about a secret. The prompt forbids stating them outright. This is a
prompt-level guarantee, not an enforced one — a weak model may leak. Treat NPC secrets
as flavour, not as a security boundary.

### World rules in the context

`world_rules` is a `WorldRulesContext` — a flattened projection of the world's
`WorldRulesV1`, built by `application/rules_projection.py`. The provider never receives
the domain document: the full thing is ~3 KB of JSON carrying sections that only future
deterministic systems care about, and the prompt budget is a real constraint.

The projection is rendered as a `# World rules` block in plain sentences, not as JSON.
Sections dropped on the way: `society`, `resources`, power tier ceilings, progression
sub-blocks, and the `chance` details beyond model and rerolls.

The rules are **authoritative** — the prompt's first section says so, and its ten
principles cover the failure modes that matter: plot armor acts before an outcome and
never after, darkness is not danger, content settings describe rather than decide, and
the model's own randomness is never the game's randomness. Those principles live in the
system prompt because they never vary; only the values are re-sent per turn.

Like secrets, this is a prompt-level guarantee. A model that ignores its rules produces a
bad turn, not a corrupted save — nothing the model returns bypasses contract validation.
Full semantics: [world-rules.md](world-rules.md).

### Time in the context

`time` is a `TimeContext`, derived on every turn from the session's `elapsed_minutes`
and the world's start date. It renders as one line:

```text
Now: 2 June, 842, 16:42 (afternoon) — 20 days, 3 hours into this story
```

The raw minute counter is deliberately absent. A narrator has no use for "29022" and
would only be tempted to do arithmetic on it.

**The model cannot move the clock, and this one is not merely a prompt-level
guarantee.** `TurnGeneration` has no field that reaches `elapsed_minutes`, and adding
one would make token sampling the arbiter of how long a journey took — the same mistake
as letting `temperature` resolve a dice roll. A turn leaves the clock exactly where it
was; only `application/time_service.py` moves it. What *is* prompt-level is the
instruction not to narrate around the clock — not to announce that a night passed, and
not to state an hour other than the one given. A model that ignores it writes a scene
that disagrees with the header, not a corrupted save.
Full semantics: [world-state-time.md](world-state-time.md).

### Established truth in the context

`world_facts` is a `WorldFactsContext` — the session's current objective truth, split
into `critical` (importance 4-5) and `relevant`. It is rendered **before** the
characters and the transcript, because it is the block everything below has to agree
with and a model reads a prompt in order:

```text
# Established truth  (authoritative: the game says these are so)
Must not be contradicted:
- The Fractured Crown — world.political_status: contested
Also established:
- Elena — narrative.birthplace: the capital's lower district
```

Subjects are rendered as *labels*, never as uuids: the director reads facts, it does not
address them, and an id the model cannot use is an id it should not be shown. Values are
rendered too — `true` becomes "yes", a list becomes a comma-separated phrase.

Selection is importance-ordered and capped, because a session accumulates facts and a
prompt does not grow. That is retrieval policy, so it lives in `context_builder.py` with
every other retrieval decision.

### Geography in the context

`space` is a `SpatialContext` — where the scene is, what is inside it, what contains it,
and every way out — or `None` when the world has no geography or the session's location
matches nothing in it. It renders as:

```text
# Where this is happening  (authoritative: this is the geography)
Here: The Broken Crown (tavern, damaged)
Within: The Lantern Quarter
Areas here: the bar, the back tables, the fireplace
Inside this place: The Broken Crown cellar (cellar)
Ways out:
- Market Street, via the door, about 0 min  — CLOSED, cannot be used
- The Broken Crown cellar, via the stairs, about 1 min
```

No ids, for the same reason facts carry none. Default condition and accessibility are
omitted — "intact, open" on every place teaches a model to ignore the field. Blocked
exits are shown and marked rather than hidden: a model that cannot see the barred gate
writes the player straight through it.

**The model cannot move anyone, and this one is only partly prompt-level.** Travel has a
cost and a duration and belongs to a system that does not exist yet, so the prompt
forbids narrating an arrival. What *is* enforced is stronger: `StateMutationBatch`
refuses to be constructed with a spatial mutation under `story_director` authority, so
nothing the model returns can open a gate, repair a ruin or change who holds a fort.

Selection is deterministic and tier-capped; see
[world-state-locations.md](world-state-locations.md#spatial-context).

### situations

`situations` is a `SituationsContext` — the ongoing processes this scene should know
about — or `None` when nothing relevant is under way, which is most turns. It renders as:

```text
# What is going on  (authoritative: you may narrate these, never change them)
- The failing wards (ward failure, local) — active, intensity 60/100, danger 55/100, growing, running 6 hours
- The contested succession (succession crisis, regional) — dormant, intensity 30/100, danger 40/100, steady, running 6 hours
```

No ids, again. The numbers *are* sent, unlike a location's default condition: there is no
uninteresting value for intensity, and a director that knows the siege is at 78 writes a
different scene from one that knows it is at 20. Direction is a word rather than a signed
integer, and a deliberately neutral one — "growing" covers a fire spreading and a festival
filling the streets.

**The model cannot change any of them, and this is enforced rather than asked.**
`TurnGeneration` has no field that could address an existing situation, and
`StateMutationBatch` refuses to be constructed with a situation mutation under
`story_director` authority. "Word arrives that the siege has broken" is a sentence it can
write and nothing more.

Selection is deterministic, banded and capped at six; see
[world-state-situations.md](world-state-situations.md#storycontext). Situations a world
tags `secret` are withheld — a stopgap for the absent `KnowledgeState`, and documented
there as the convention it is.

### History in the context

`history` is a `HistoryContext` — what has already happened in this session, in two
bands. It renders as:

```text
# What has already happened  (authoritative: settled, not open to revision)
This story so far:
- [character] King Aldric is dead. (3 days ago)
Lately:
- [world] The east gate gave way. (an hour ago)
```

**Landmarks** are the heaviest events of the whole session — importance ≥ 4, at most 8 —
and they are what the story is *about*: a siege that began forty turns ago still shapes
every scene. **Recent** is importance ≥ 2, at most 12, with anything already listed as a
landmark excluded. Both are oldest-first, and both are usually empty in an early session.

Neither band grows with how long the save has been played. The whole history is never
loaded: a session accumulates events indefinitely and a prompt does not grow.

The `resolutions` table is **not** in the context at any size. Idempotency keys, resolver
versions and revision numbers are engine bookkeeping — the director is told what happened,
not how the engine decided it. Selection lives in `context_builder` with every other
retrieval decision; semantics in [event-resolution.md](event-resolution.md).

## TurnGeneration — what the model returns

```text
TurnGeneration
├── narration            : string, required, non-empty
├── dialogue             : DialogueLine[]
├── suggested_actions    : string[]   (trimmed, capped at 4)
├── memory_candidates    : MemoryCandidate[]
├── relationship_changes : RelationshipChange[]
├── world_events         : WorldEvent[]
├── fact_proposals       : FactProposal[]      (optional, capped at 5)
├── location_proposals   : LocationProposal[]  (optional, capped at 3)
├── situation_proposals  : SituationProposal[] (optional, capped at 2)
└── visual_cue           : VisualCue
```

Defined in `application/contracts.py`. Unknown fields are ignored rather than fatal —
models add chatter, and a stray key should not cost the player a turn.

### Structured output

The Ollama adapter passes `TurnGeneration.model_json_schema()` as the `format` parameter
to `/api/chat`, so decoding is schema-constrained. The response is then **validated
again** with Pydantic: constrained decoding narrows the output, it does not guarantee
it. A response that parses as JSON but violates the contract raises
`StoryGenerationError`, which rolls the turn back.

### Memory candidates

```text
character_id : UUID | null
kind         : episodic | fact | relationship | goal | world
summary      : string
importance   : 1..5
```

A memory is something still relevant in twenty turns: commitments, revelations,
injuries, deaths, bargains, discovered facts, changed goals. Greetings, small talk, and
restatements of the player's action are explicitly excluded by the prompt. Most turns
should produce zero or one. `importance` is enforced 1–5 by both Pydantic and a database
check constraint.

Memories are currently scoped to a session. Promoting durable world facts to
world-scoped memory is a Phase 2 concern.

A memory is not a fact. A memory is what someone recalls, fallibly; a fact is what the
game asserts is so. See `fact_proposals` below and
[world-state-facts.md](world-state-facts.md).

### World events

```text
category   : action | combat | movement | social | world | situation | spatial
             | character | political | economic | system | other
subtype    : snake_case, 1..80 chars
summary    : string, 1..500 chars
importance : 1..5 | null
```

Something the model believes just happened and is worth remembering — a *proposal*, like
`FactProposal`. `category` is a closed enum because retrieval filters on it, and it
defaults to `other` rather than to a guess: a model that did not pick one has not told us
anything. `subtype` is an open string, because an enum of several hundred event names is
wrong the first time anyone adds a system.

**Whether history keeps it, and at what importance, is decided by policy the model cannot
see.** `application/event_service.py` looks the subtype up, drops it entirely if its
policy says `none`, and clamps the proposed importance into the band that subtype allows.
`importance: null` means "no opinion", which is the honest answer most of the time.

That split is not politeness. A model asked to rate what it has just written rates all of
it highly, so a spilled drink and a coronation both arrive at 5 — and the events reaching
the *next* prompt are selected by importance, so a model that could set its own scale
would be choosing what it gets to remember.

`summary` is one line for people to read. It is **never parsed for mechanics**: a world
event does not change a fact, move a thing, or advance the clock. Those go through
`fact_proposals`, `location_proposals` and the resolution pipeline. See
[event-resolution.md](event-resolution.md).

### Fact proposals

```text
subject_type : world | character | location | faction | other
subject_id   : UUID | null      (required for anything but the world)
property     : namespace.snake_case
value        : bool | int | float | string | string[] | null
importance   : 1..5
reason       : string
```

A *proposal*, and nothing more. The model never writes a fact: each one is reviewed
against the property's policy, the model's authority, what is already established, and
the world's rules, and most are refused. `TurnResponse` reports `facts_established` and
`facts_rejected` so a turn's outcome is visible without reading the log.

This is where the model's authority is at its narrowest by design. **The Story Director
has the lowest authority over mechanical state of anything in the system**: it reaches
`OPEN` properties — diegetic colour like `narrative.birthplace` — and nothing else.
Whether someone is alive, where they are, what they carry, how hurt they are, and every
other `system.` or `gameplay.` property is decided by game systems and handed to the
model as an outcome to narrate.

Three properties of the design worth naming:

- **There is no shape in which the model can return a replacement WorldState.** The
  contract is a list of individual claims about single properties, which is what can be
  adjudicated one at a time.
- **A malformed proposal is never fatal.** It is dropped before validation and logged.
  A 502 over a detail the model was not obliged to send would roll back a turn of real
  prose to protect an optional extra.
- **The field is optional**, unlike `suggested_actions` — and for the opposite reason.
  Suggestions are wanted every turn, so the schema demands them. The right number of new
  facts for most turns is zero, and a required field is one a grammar-constrained model
  will fill, turning "record what the story established" into "invent something".

Full semantics: [world-state-facts.md](world-state-facts.md).

### Location proposals

```text
name, description
category, subtype, scale
parent_location_id : an existing place, from the context
```

Somewhere the story just established exists. There is **no `id` field** and there never
will be: the application mints ids, and the uuid a model would supply is one it read in a
prompt. `parent_location_id` is the exception, and it is an id the context gave it —
anything invented there fails to resolve and the proposal is refused.

Refusals are the normal case: anything larger than a site, anything already named,
anything whose parent this session cannot see. `TurnResponse` reports `locations_created`
and `locations_rejected`.

What survives becomes **deterministic canon for that session**. Generating "Starfall
Books, east side of Riverwood" may be stochastic once; afterwards it is in the graph,
arrives in every later prompt as established geography, and is never re-imagined. It is
also invisible to every other save of the same world.

Full semantics: [world-state-locations.md](world-state-locations.md).

### Situation proposals

```text
category, subtype, title, description
scope
primary_location_id : an existing place, from the context
```

A process the story just set in motion. Look at what is **not** there: no `intensity`, no
`threat`, no `momentum`, no `importance`, no `status`, and no way to name an existing
situation.

A location the story mentions is a noun. A situation is a process with three bounded
numbers, a lifecycle and a claim on future simulation — and a model that could set those
could declare a war at intensity 100 by writing an atmospheric sentence, or end a siege
because the scene felt like it should be over. So the model says *what kind of thing
began* and the application decides every number. A narrated process starts small
(intensity 20, threat 0, momentum 20, importance 2) because it has just begun; if
something deserves to start large, a game system starts it large.

The rejected alternative was to let the model propose numbers and clamp them. Clamping
`intensity: 100` to 40 still lets a sentence decide that a fire is severe.

Capped at two per turn, and the right answer is almost always zero — a scuffle in a
tavern is a scene, not a situation. `TurnResponse` reports `situations_started` and
`situations_rejected`.

Full semantics: [world-state-situations.md](world-state-situations.md#story-director-authority).

### Relationship deltas

```text
character_id, trust_delta, affection_delta, respect_delta, fear_delta, reason
```

Each delta is constrained to **−5..+5** at the contract level, so an out-of-range value
is a validation error rather than something quietly clamped. The application layer then
clamps again when applying, and axis values are bounded to **−100..100**
(`domain/relationships.py`).

Two layers on purpose: the contract bound catches a misbehaving model loudly, and the
application clamp guarantees the invariant regardless of how a value arrived. The model
never mutates a row — `turn_service` reads the current vector, applies clamped deltas,
and writes the result.

A change proposed for a character that does not exist in the world is logged and
dropped, never written. Same for dialogue attributed to an unknown `character_id`: the
line is kept but stored unattributed, so a hallucinated id cannot violate a foreign key.

### Visual cues

```text
generate : boolean
scene_prompt : string | null
character_ids : UUID[]
reason : string | null
```

`generate=true` marks a visually significant moment — a first meeting, a reveal, a new
landscape, a fight — not every conversational turn. `scene_prompt` should read as a
visual description, not narration. Currently the flag is recorded and returned to the
client; wiring it to actual generation is Phase 4.

### Suggested actions

3–4 short actions in the player's voice, meaningfully different from each other. They
are **suggestions only** — the composer always accepts arbitrary free text, and the
player can ignore them entirely.

## Narrating an outcome

A story provider has a second entry point, `narrate_outcome`, used **after** a resolution
has already been committed. It is the same provider and a completely different contract.

```text
OutcomeContext                      OutcomeNarration
├── world, player, time             └── narration : string, required, non-empty
├── disposition                         (and nothing else)
├── reason_code
├── resolver
├── events
└── detail
```

`OutcomeContext` is small on purpose. Everything a turn's context carries in order to help
a model *decide* something — the rules, the geography, the characters' secrets, the
relationship vectors — is absent, because the deciding is over. `disposition` is `applied`,
`rejected` or `no_effect`, never `success` or `failure`: prose that called a botched
attempt a failure would read as the world refusing it. `detail` is the resolver's own
structured account, and it is the authoritative one — the narrator describes it and does
not restate it in numbers.

`OutcomeNarration` has **one field**, and that is the whole point. A turn's contract is
wide because the model is proposing things the application will review; this one is narrow
because there is nothing left to propose. No events, no facts, no suggestions, no
disposition — a narrator that could emit those would be resolving the action a second time,
in prose, after the first resolution was already permanent.

If the provider fails here, the outcome stays exactly as committed and the failure surfaces
as the same 502 as any other. What is missing is a paragraph, not a turn. See
[event-resolution.md](event-resolution.md#narration).

## Model failure behaviour

`StoryGenerationError` carries `provider` and `retryable`, and maps to HTTP **502** with:

```json
{ "error": "story_generation_failed", "detail": "...", "provider": "ollama", "retryable": true }
```

| Cause | `retryable` | Message names |
|---|---|---|
| Ollama unreachable | yes | the base URL and `ollama serve` |
| Timeout | yes | `OLLAMA_TIMEOUT_SECONDS` |
| Model not pulled | no | `ollama pull <model>` |
| Non-JSON content | yes | that the model may be too small |
| Schema violation | yes | the first validation error |

There is **no silent fallback to the mock provider** when `STORY_PROVIDER=ollama`. The
explicit mock provider is the development mechanism; an automatic downgrade would hide
a broken configuration behind prose that looks fine. The failed turn is rolled back
whole, so retrying is always safe.

## Prompt versioning

Prompts live in `apps/api/app/prompts/*.md` with front matter:

```yaml
---
version: 1
name: story_director
---
```

The strategy for now is deliberately minimal: **edit the file, bump `version`, describe
the behavioural change in the commit message.** The version is loaded and available for
logging, so a turn can be correlated with the prompt that produced it.

When prompt iteration starts affecting saved games, the next step is to record the prompt
version alongside `resolutions.resolver_version`, so old sessions can be interpreted
against the prompt they were written under the same way they can already be interpreted
against the resolver that decided them. A full prompt-management system (A/B testing,
remote config, per-world overrides) is deliberately out of scope.

## Future semantic retrieval

Today `StoryContextReaderPort.load_memories` orders by `(importance DESC, created_at
DESC)` with a limit the application supplies. That is the single method semantic
retrieval replaces, and it has exactly one implementation.

The plan, when recency+importance demonstrably fails:

1. Add an `embedding` column to `memories`, populated on write via Ollama's
   `/api/embeddings`. No new service.
2. Keep the current query as the candidate set, then re-rank by cosine similarity to the
   player's action.
3. Only if that is too slow, add an index (`sqlite-vec` or similar).

Deterministic tests are the reason this is not done yet: every backend test asserting
turn behaviour would need to tolerate a similarity-ranked context. That cost is worth
paying once there is evidence it improves play, not before.
