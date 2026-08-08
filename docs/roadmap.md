# Roadmap

High-level and deliberately non-committal on dates. Phase 1 is done; everything below it
is a sketch, not a specification.

## Phase 1 — Bootstrap ✅

Current iteration. Monorepo, persistence with migrations, the AI contract, mock and
Ollama story providers, the ComfyUI adapter boundary, one complete turn loop, a usable
mobile-first PWA in English and Spanish, and tests that actually run.

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
- Time of day and calendar.
- NPC schedules — characters exist when the player is not looking.
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
