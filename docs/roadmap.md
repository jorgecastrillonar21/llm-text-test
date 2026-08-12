# Roadmap

High-level and deliberately non-committal on dates. Phase 1 is done; everything below it
is a sketch, not a specification.

## Phase 1 — Bootstrap ✅

Current iteration. Monorepo, persistence with migrations, the AI contract, mock and
Ollama story providers, the ComfyUI adapter boundary, one complete turn loop, a usable
mobile-first PWA in English and Spanish, and tests that actually run.

## Phase 1.5 — World rules ✅

Every world now carries a validated, versioned `WorldRulesV1` document describing how its
universe works, six presets to start from, and an authoritative rules block in the Story
Director's context. Configuration only: no dice, no combat, no simulation engine. See
[world-rules.md](world-rules.md), whose *deferred questions* section lists what this
deliberately did not decide.

The systems that read those rules are each their own epic. `WorldState` is done in four
parts (1.6–1.9), the resolution boundary that changes it in a fifth (1.10), the
consolidation that names the whole thing in a sixth (1.11) and the review corrections in a
seventh (1.12); `CharacterSheet`, `PowerSystem`, rules resolution and world simulation are
not started.

## Phase 1.6 — WorldState: simulation time ✅

The first piece of `WorldState`, and the foundation every later simulation system needs:
an authoritative per-session clock in fictional minutes, a calendar projection, an
explicit advancement path that only application code may use, and a generic scheduled
event model. Turn count and fictional time are independent, and a turn does not move
the clock. See [world-state-time.md](world-state-time.md).

Deliberately absent: locations, NPC schedules, weather, travel, combat duration, the
seeded RNG, and any form of real-time synchronisation.

## Phase 1.7 — WorldState: facts and state changes ✅

The second piece of `WorldState`: an authoritative record of what is objectively true in
a session, with one current value per subject and property enforced by the database. A
policy/authority model decides who may change what — the Story Director reaches
narrative colour and nothing else, and never writes the store itself. Changes go through
one service as atomic batches paired with a `GameEvent`, and move a per-session state
revision. Worlds may declare starting facts that each session copies and then diverges
from. See [world-state-facts.md](world-state-facts.md).

Deliberately absent: inventory, hit points, skills, combat, magic, economy, autonomous
offscreen simulation, `KnowledgeState`, `BeliefState`, `SceneState`, semantic
contradiction detection, and any domain-specific state aggregate.

## Phase 1.8 — WorldState: locations and spatial state ✅

The third piece of `WorldState`: persistent places, what contains what, what connects to
what, and what is currently true of each in one save. Containment, connectivity and
proximity are kept apart on purpose — being inside somewhere is not being able to walk
there, and sharing a location is not being near someone. Definitions are shared across
saves and state is not; gameplay may invent small places that become deterministic canon
for that session and are invisible to every other. See
[world-state-locations.md](world-state-locations.md).

Deliberately absent: `CharacterState`, travel, scenes, tactical space, perception,
interaction range, weather, and any interactive map. Canonical position was absent here
too, and Phase 1.12 corrected that.

## Phase 1.9 — WorldState: situations ✅

The fourth piece of `WorldState`: what the world is currently *doing*. A siege, a fire, a
festival, an investigation, a reconstruction — a persistent process with a lifecycle,
participants, a place and a direction. `Situation` is kept distinct from `WorldFact`,
`GameEvent` and `ScheduledEvent`: the siege is not the breach, the breach is not the
ruined gate, and the ruined gate is not the next evaluation.

Three independent measures rather than one severity, because a festival at intensity 90
is not a threat and a model with one number makes every positive process look like a
problem. Progression is interval-driven with no universal tick — fires in minutes, sieges
in hours, construction in weeks — behind a generic resolver boundary that specialised
resolvers will register into. The Story Director may propose that a process began; it
supplies no numbers and cannot touch one that exists. See
[world-state-situations.md](world-state-situations.md).

Deliberately absent: the world simulation engine itself, fire/warfare/epidemic/economic
simulation, `FactionState`, NPC autonomy, a game RNG, a situation relation graph,
`KnowledgeState`, and any background processing.

## Phase 1.10 — Event / Resolution V1 ✅

The four `WorldState` phases described what the world *is*. This is the door through which
it is allowed to change, and the record it leaves behind. Every authoritative change now
goes through one pipeline — Command, ResolutionContext at a known revision, a pure
Resolver, an Outcome, then a single transaction that writes the verdict, the significant
events, the mutations and the revision together, or writes nothing at all.

Dispositions are `applied` / `rejected` / `no_effect`, never `success` / `failure`: a
lockpick snapping is an attempt that happened and went badly, and the world having no locks
is a refusal, and the two must not produce the same prose. Idempotency is a database
constraint rather than a Python check, so a retried turn resolves once — no second model
call, no duplicate events, no clock advanced twice. `GameEvent` became significant history
rather than a log: per-subtype policy decides what is kept and clamps proposed importance,
because a model asked to rate what it just wrote rates all of it highly. History is
append-only, ordered by fictional minute plus a per-session sequence, and narration comes
*after* the mechanics and merely describes them. See
[event-resolution.md](event-resolution.md).

Deliberately absent: a full Intent Interpreter, a complete Command hierarchy, skills and
skill checks, a game RNG, combat, `CharacterState`, inventory, a power system, NPC
autonomy, faction simulation, a full reaction engine, event sourcing, snapshots and rewind.

## Phase 1.11 — Consolidate WorldState V1 ✅

Six phases built the mutable reality of a session in pieces. This one gives it a name and
a single conceptual root without merging any of it: `WorldStateV1` is four fields —
version, session, revision, time — and every collection stays in the domain that owns it.
The root is not a serialized document, and it was never going to be; a world where the
lantern goes out should not rewrite the siege.

What is new is the composition. `CurrentWorldSnapshot` reads across facts, geography,
situations, schedule and history at one revision and returns a projection, at one of four
scopes — `minimal`, `relevant`, `regional`, `full_debug` — because the default answer must
never be the expensive one and the model never receives the debug view. A consistency
validator reports cross-domain disagreements no single domain can see. Reads are
read-only all the way down: the snapshot port has no write method and no commit.

`state_revision` is now the only revision mechanism, `elapsed_minutes` remains the only
clock, and `turn_index` counts turns; none is derived from another. Persistence changed by
one column and one migration, and the index audit added nothing — the access patterns were
already covered. See [world-state.md](world-state.md), the canonical document for all six
phases.

Deliberately absent: everything in Phase 5 and 6, plus `CharacterState`, `KnowledgeState`,
event sourcing, snapshots and rewind, and any state editor in the UI.

## Phase 1.12 — World Foundation corrections ✅

A review of the six phases above found four places where the foundation said something it
did not do. No redesign — four corrections, each the smallest one that makes the claim
true:

- **Canonical position.** Where an actor is was a free-text string on the session,
  matched by name. It is now `character_positions`: one row per actor, four typed shapes
  (`at_location`, `in_transit`, `offstage`, `unlocated`), ids validated against geography
  the session can see. `InTransit` records the commitment and computes nothing — travel
  mechanics remain unbuilt. See [world-state.md](world-state.md#where-the-player-is).
- **`due` is not `processed`.** Advancing time marked events processed, recording as
  finished work no code had done. Time now marks what it reached `due` and stops; the
  system that owns the work calls `complete_scheduled_event` after doing it. See
  [world-state-time.md](world-state-time.md#due-is-not-processed).
- **Fact subjects must resolve.** A location fact could name an id that was nothing, or
  another world's geography, whenever a trusted caller reached the state service without
  passing the proposal reviewer. Checked at the mutation door now, for every caller. See
  [world-state-facts.md](world-state-facts.md#a-subject-must-name-something-that-exists).
- **Nested situations in one batch.** Documented as supported and never expressible. A
  parent must have existed before the batch began, and saying so beats inventing a
  mutation scripting language. See
  [world-state-situations.md](world-state-situations.md#a-batch-cannot-nest-a-situation-inside-one-it-just-started).

Deliberately absent, still: Character Foundation, `CharacterSheet`, the travel engine, the
World Simulation Scheduler, NPC autonomy, factions, knowledge and perception.

## Phase 1.13 — LLM runtime baseline and output budgeting ✅

The last of the World Foundation work, and the only phase that adds no gameplay. Local
generation was the slowest thing here by three orders of magnitude and nothing measured it,
so "the turn was slow" had no next question. Now every generation reports what it read,
what it wrote, how long each part took, whether it hit its budget and why it was called at
all — as a provider-neutral record the adapter builds and the game never reads.

Output is bounded in **tokens** by an explicit `GenerationPolicy` per typed purpose, so no
`num_predict` is hidden in infrastructure code. Turn latency is split into model time and
application time at the use-case boundary. Metrics are deliberately *not* game state: no
table, no migration, no revision bump, and never sent back to a model.

The measurement that matters is prompt size, because it is the thing later foundations can
break. Over 16 turns it went 5414 → 6430 tokens, and — the part that matters — the slope
fell from ~104 tokens/turn to ~30 at turn 10, where the transcript cap begins evicting. The
prompt tracks the caps rather than the campaign, and the rule going forward is that a new
foundation must earn its way into `StoryContext` with a bounded projection or stay out.

It also produced an uncomfortable number worth keeping: on a 4 GB laptop GPU that fits 43%
of a 7B model, a turn takes 66–218 seconds — median 137 over a long run — of which the
application layer accounts for 31–93 **milliseconds**. See
[llm-performance-baseline.md](llm-performance-baseline.md#epic-1-baseline-measured).

Deliberately absent: any optimization made without a measurement, a metrics database,
distributed telemetry, GPU inspection, automatic model selection, and UI streaming.

## Phase 2 — Narrative quality

The highest-value work. The system runs; the writing is what makes it worth playing.

- Rolling session summaries so `session.summary` stops being empty and old turns still
  matter.
- Token/context budgeting — the real prompt size is now measured (Phase 1.13); what
  remains is trimming by *value* rather than by the fixed per-slice counts.
- Prompt iteration against saved transcripts; record prompt version per turn.
- NPC-specific knowledge: what a character has actually witnessed, rather than handing
  every character the whole context.
- Better memory extraction — deduplicate against existing memories before writing.
- World-scoped memories, promoted from session-scoped facts.

## Phase 3 — Semantic memory

Only once recency+importance is demonstrably the bottleneck.

- Embeddings via Ollama's `/api/embeddings`, stored alongside memories.
- Semantic re-ranking of the existing candidate set.
- Memory consolidation: merge near-duplicates into stronger single memories.
- Decay and forgetting, so a long session does not accumulate unbounded trivia.

See [ai-contract.md](ai-contract.md#future-semantic-retrieval) for the seam.

## Phase 4 — Images

- A real ComfyUI workflow with prompt, seed, and character inputs.
- Job polling and output retrieval (`/history/{prompt_id}`), asynchronous.
- Asset storage under `data/`, served by the API, cached by id.
- Character reference consistency (IP-Adapter or reference latents).
- A gallery per session.

## Phase 5 — RPG systems

- Inventory and items.
- Stats and skills.
- Checks and dice, resolved by a resolver like everything else, with the seed and an RNG
  audit policy recorded on the `ResolutionRecord`. The seam is already there: a resolver is
  pure, so its randomness has to arrive as an argument, and the replay path already
  guarantees a retried action does not draw twice.
- Combat as a structured turn mode.
- Compound actions — one player sentence producing several resolutions.
  `parent_resolution_id` is stored and read back; what remains is deciding whether the
  whole set is atomic.
- Quests with state.

## Phase 6 — World simulation

- Locations as first-class entities, with a map.
- Per-world calendars: custom month names, month lengths, week structures, eras. The
  clock and the projection landed in Phase 1.6; only the authoring half is missing.
- NPC schedules — characters exist when the player is not looking. `ScheduledEvent` exists
  and time advancement marks what the clock reached `due`; what is missing is the
  dispatcher that executes due work and anything that schedules interesting work in the
  first place. See [DUE is not PROCESSED](world-state-time.md#due-is-not-processed).
- Action durations: a resolved action that actually costs fictional time, with variance
  drawn from the seeded game RNG rather than from model sampling.
- Factions and reputation.
- Autonomous events between turns, and a reaction pipeline: "the guard notices" as a new
  Resolution with `caused_by_event_id` pointing at what it reacted to. The depth bound is
  the first thing that has to be decided, and there will be no event bus.

## Phase 7 — Mobile productization

- Polished PWA: install prompts, offline shell, better loading states.
- Secure remote access (Tailscale HTTPS) so the PWA installs on the phone.
- Authentication — required before any non-LAN exposure.
- Capacitor or native packaging, if the PWA proves insufficient.
- Local on-device inference experiments.

## Explicitly not planned

Multiplayer, cloud hosting, billing, subscriptions, RAG frameworks, agent frameworks,
voice, LoRA training, Kubernetes, microservices, GraphQL. If any of these become
necessary, that is a new decision with its own justification — not a backlog item.
