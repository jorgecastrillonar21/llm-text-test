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

The systems below that read those rules — WorldState, CharacterSheet, PowerSystem, rules
resolution, world simulation — are each their own epic and none of them is started.

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

Deliberately absent: `CharacterState` and canonical position, travel, scenes, tactical
space, perception, interaction range, weather, and any interactive map.

## Phase 2 — Narrative quality

The highest-value work. The system runs; the writing is what makes it worth playing.

- Rolling session summaries so `session.summary` stops being empty and old turns still
  matter.
- Token/context budgeting — measure the real prompt size and trim by value rather than
  by fixed counts.
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
- Checks and dice, with outcomes fed back into the narration prompt.
- Combat as a structured turn mode.
- Quests with state.

## Phase 6 — World simulation

- Locations as first-class entities, with a map.
- Per-world calendars: custom month names, month lengths, week structures, eras. The
  clock and the projection landed in Phase 1.6; only the authoring half is missing.
- NPC schedules — characters exist when the player is not looking. The generic
  `ScheduledEvent` model exists; nothing produces one yet.
- Action durations: a resolved action that actually costs fictional time, with variance
  drawn from the seeded game RNG rather than from model sampling.
- Factions and reputation.
- Autonomous events between turns.

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
