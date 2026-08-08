# AI contract

Everything exchanged with a story provider is typed and validated. The model proposes;
the application layer decides what is persisted.

## StoryContext — what the model sees

Built by `application/context_builder.py`. A provider receives this and nothing else —
no database session.

| Field | Contents | Bound |
|---|---|---|
| `world` | name, description, genre, setting, **language** | — |
| `player` | name, description | — |
| `session` | title, current location, summary, turn index | — |
| `relevant_characters` | full profiles incl. goals and secrets | 12 |
| `recent_messages` | transcript, oldest-first | 20 |
| `relevant_memories` | importance desc, then recency | 30 |
| `relationships` | current axis values per character | — |
| `player_action` | this turn's raw input | 2000 chars |

Retrieval is pure SQL ordering — deterministic and reproducible. The same context always
produces the same mock turn, which is what makes the E2E flow assertable.

**Secrets are included deliberately.** The director needs them so an NPC can act on,
deflect, or lie about a secret. The prompt forbids stating them outright. This is a
prompt-level guarantee, not an enforced one — a weak model may leak. Treat NPC secrets
as flavour, not as a security boundary.

## TurnGeneration — what the model returns

```text
TurnGeneration
├── narration            : string, required, non-empty
├── dialogue             : DialogueLine[]
├── suggested_actions    : string[]   (trimmed, capped at 4)
├── memory_candidates    : MemoryCandidate[]
├── relationship_changes : RelationshipChange[]
├── world_events         : WorldEvent[]
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

When prompt iteration starts affecting saved games, the next step is to record the
prompt version on `GameEvent` or a `turns` table so old sessions can be interpreted
against the prompt they were written under. A full prompt-management system (A/B
testing, remote config, per-world overrides) is deliberately out of scope.

## Future semantic retrieval

Today `context_builder._load_memories` orders by `(importance DESC, created_at DESC)`
with a fixed limit. That is the single function semantic retrieval replaces.

The plan, when recency+importance demonstrably fails:

1. Add an `embedding` column to `memories`, populated on write via Ollama's
   `/api/embeddings`. No new service.
2. Keep the current query as the candidate set, then re-rank by cosine similarity to the
   player's action.
3. Only if that is too slow, add an index (`sqlite-vec` or similar).

Deterministic tests are the reason this is not done yet: every backend test asserting
turn behaviour would need to tolerate a similarity-ranked context. That cost is worth
paying once there is evidence it improves play, not before.
