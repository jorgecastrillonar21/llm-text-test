# LLM performance baseline

Local generation is the slowest thing this application does, by three orders of
magnitude. This document is how it is measured, what the measurements meant on the
machine Epic 1 was built on, and what a later epic has to do to show it did not make
things worse.

Nothing here is a target. The numbers below describe one laptop; yours will differ, and
the point of recording them is comparison over time, not a threshold to pass.

## Tokens, not characters

Every budget in this system is counted in **tokens**. A token is roughly 3–4 characters
of English prose and rather fewer of JSON punctuation, but the ratio is a property of
the tokenizer, not a constant — so a character limit is a guess about a limit, and the
provider enforces the real one. `STORY_MAX_OUTPUT_TOKENS` is tokens. `OLLAMA_NUM_CTX` is
tokens. `prompt_eval_count` and `eval_count` come back from Ollama in tokens.

The one character limit that remains is on player input (2000 characters), which is a
sanity bound on a text box rather than a model budget.

## The two budgets, which are not the same thing

| | Setting | Ollama option | What it bounds |
|---|---|---|---|
| **Context window** | `OLLAMA_NUM_CTX` | `num_ctx` | How much the model may *read*: system prompt + context + the generated tokens, all together |
| **Output budget** | `STORY_MAX_OUTPUT_TOKENS` | `num_predict` | How much the model may *write* before it is cut off |

They fail in opposite directions, which is why neither is allowed to default.

**Context window.** Ollama's own default is 4096 regardless of what the model supports,
and llama.cpp discards from the *start* of an over-long prompt — the system prompt, the
world rules, the character definitions — without an error, a warning, or any field in the
response. A run that silently drops its rules still produces confident prose. This is why
`OLLAMA_NUM_CTX=8192` is set explicitly in `.env.example` and why `prompt_context_utilization`
is reported on every generation: it is the early warning that a context is approaching the
window it is being measured against. Raising `num_ctx` costs VRAM, and on a machine where
that pushes layers off the GPU it costs throughput too — see [hardware](#hardware-decides-almost-everything).

**Output budget.** `num_predict` is a hard stop mid-token, not an instruction to be
brief. When a generation ends because it ran out of budget, Ollama reports
`done_reason: "length"` and the application records `output_budget_reached: true`.

For `story_turn` this is worse than a truncated paragraph. A turn is generated as a
**schema-constrained JSON document**, so a document cut off at the budget is invalid JSON
and the turn fails outright — the player gets an error, not a shorter answer. The adapter
detects this case and says so instead of reporting a generic parse failure:

```text
Ollama returned content that is not valid JSON: <parse error>. The output stopped at
the 1024-token budget (done_reason=length), so the JSON is truncated rather than wrong.
Raise STORY_MAX_OUTPUT_TOKENS.
```

carried on an error whose `error_code` is `budget_exhausted` rather than `invalid_json`,
so the two causes are distinguishable in a log.

That is the reason `STORY_MAX_OUTPUT_TOKENS` defaults to **1024** rather than the 256 a
prose-only endpoint could use. Measured output for a real turn is 156–308 tokens (below),
so 1024 is roughly 3–6× headroom over what turns actually cost — deliberate, because the
failure mode on the low side is a failed turn and on the high side is nothing at all
(`num_predict` is a ceiling; unused budget costs no time).

`NARRATION_MAX_OUTPUT_TOKENS` is 320, because `outcome_narration` returns one paragraph of
plain prose and genuinely can be cut short without breaking anything.

## Keeping the model resident

`OLLAMA_KEEP_ALIVE` is how long Ollama keeps the model in memory after a request. Empty
sends nothing and lets Ollama use its own default (5 minutes at the time of writing). `30m`
suits an active playthrough. `-1` keeps it resident until something evicts it, which is a
standing multi-gigabyte memory commitment and not a free speedup.

You do not have to guess whether you are paying for a reload: **`load_ms` is on every
record.** On a cold model it is seconds; on a resident one it is tens of milliseconds. The
measured gap on this machine is `12011.3 ms` versus `58.9 ms` — a factor of 200.

To see what is resident right now, outside the application:

```bash
ollama ps
```

That is a thing *you* run in a terminal. The application never shells out; `load_ms` is
how it knows, and `GET /api/v1/ai/status` reports the configured `keep_alive` under
`extra` so you can confirm what the process is actually sending.

## What Ollama reports, and what is derived from it

The adapter captures these from every `/api/chat` response and nothing else:

| Ollama field | Unit | Becomes |
|---|---|---|
| `model` | — | `model` |
| `done`, `done_reason` | — | `done_reason` (`stop`/`length`/`load`/`error`/`unknown`) |
| `prompt_eval_count` | tokens | `prompt_tokens` |
| `eval_count` | tokens | `generated_tokens` |
| `total_duration` | ns | `total_ms` |
| `load_duration` | ns | `load_ms` |
| `prompt_eval_duration` | ns | `prompt_eval_ms` |
| `eval_duration` | ns | `generation_ms` |

Durations arrive in **nanoseconds** and are converted once, at the boundary, by
`ns_to_ms`. Anything that is not a non-negative integer becomes `None` rather than a
number: this is JSON decoded from another process, and a malformed duration must not turn
into a measurement somebody trusts.

Derived, in `LlmGenerationMetrics`:

- `prompt_tokens_per_second` = `prompt_tokens / (prompt_eval_ms / 1000)` — how fast the
  model *read*.
- `generation_tokens_per_second` = `generated_tokens / (generation_ms / 1000)` — how fast
  it *wrote*. This is the number people mean by "tokens per second".
- `prompt_context_utilization` = `prompt_tokens / configured_context_window`. Not clamped:
  a value above 1.0 is worth seeing, not worth hiding.
- `output_budget_reached` — true on `done_reason: length`, and also when the token count
  reached the configured budget even if the provider called it a normal stop.

**A missing measurement is `None`, never `0`.** Zero duration is a real thing Ollama
reports (a cached prompt evaluates in no time), and dividing by it yields "no rate", not
"zero tokens per second". Every rate is `None` when its inputs are missing or zero, and
the log line prints `-` where a number is absent.

### Purpose

Every record carries a typed `GenerationPurpose`, so "why was the model called" is never
inferred from a free-text label:

| Purpose | Call site | Budget |
|---|---|---|
| `story_turn` | `POST /sessions/{id}/turns` — the whole turn, as JSON | `STORY_MAX_OUTPUT_TOKENS` |
| `outcome_narration` | `POST /sessions/{id}/resolutions/{id}/narration` — one paragraph | `NARRATION_MAX_OUTPUT_TOKENS` |

Those are the only two generations that exist today. The enum is the place a third gets
added, together with its own budget in `GenerationPolicy`.

## What is on the critical path

Only the AI work needed to answer *this* request is allowed to run inside the request.
Today that is exactly one `story_turn` call per turn, and `llm_call_count` on
`TurnPerformanceMetrics` is what proves it rather than an assumption — if a later epic adds
a second synchronous call, that field moves from 1 to 2 and the baseline shows it.

Turn latency is split at application boundaries, not sprinkled through domain methods:

- `total_turn_ms` — the whole use case
- `story_generation_ms` — the provider call
- `non_llm_application_ms` — the remainder: retrieval, resolution, persistence

so that "the turn took 100 seconds" can always be resolved into "99.9 of it was the model"
or "50/50", without a debugger.

## Context is bounded, and must stay bounded

Prompt size must not grow with the size of the campaign. The rule, in one line:

> **Adding another game foundation must not automatically append all of its persisted
> state to `StoryContext`.**

Every retrieval in `context_builder.py` is capped, and the caps are the contract:

| Context slice | Cap |
|---|---|
| recent messages | 20 |
| memories | 30 |
| characters | 12 |
| relationships | the same 12 characters |
| facts | 40 |
| landmark + recent events | 8 + 12 |
| situations | 6 |
| geography | 8 adjacent, 8 children, 6 zones, 4 ancestors |

`StoryContext` carries **no** `CurrentWorldSnapshot`, no full location list, no full fact
table, no scheduled events and no resolution audit trail. A new foundation gets a bounded,
purpose-shaped projection or it gets nothing; the debug snapshot endpoint growing does not
mean the prompt grows.

The measurement that keeps this honest is prompt token count at turn 1 versus a later
turn, which the baseline script prints on every run.

## Metrics are not game state

`LlmGenerationMetrics` is a technical record about a process. It is not a `GameEvent`, not
a fact, not a memory, and not part of `WorldState`:

- it never moves `state_revision`;
- it never enters `StoryContext` — **previous performance data is never sent back to the
  model**;
- it lives in a bounded in-process ring buffer (`LLM_METRICS_BUFFER_SIZE`, default 200) plus
  one structured log line per generation, and in no database table;
- prompts and generated text are **not** stored in it. Token counts, yes; the words, no.

And it is secondary to the work: a recorder that raises must never turn a successful
generation into a failed turn. Recording is wrapped so that a broken metrics backend
produces a logged complaint and a completed turn.

## Streaming and time-to-first-token

The Ollama adapter supports streaming (`OLLAMA_STREAMING_ENABLED`, default off) and exposes
it as `stream_turn(...) -> AsyncIterator[StoryChunk]` alongside the unchanged
`generate_turn(...)`. Both build the prompt through the same path; there is no duplicated
context construction.

It is off by default because the only thing it currently buys is measurement. Turn output
is schema-constrained JSON, which cannot be usefully displayed half-decoded, and the REST
contract the PWA speaks is a single response. When it is on, `time_to_first_token_ms` is
measured from request start to the first non-empty content chunk.

When it is off, **`time_to_first_token_ms` is `None`.** It is not estimated from
`total_ms`, `load_ms` or anything else. TTFT for the default transport is unavailable
until streaming is enabled, and a fabricated one would be worse than the gap.

## Hardware decides almost everything

The single largest factor is whether the model fits in VRAM. `ollama ps` reports both:

```text
NAME          SIZE      PROCESSOR
mistral:7b    5.6 GB    57%/43% CPU/GPU
```

A 7B model at Q4_K_M needs ~5.6 GB with an 8192-token context. On a 4 GB card, 43% of it
lives on the GPU and the rest runs on the CPU, and generation throughput falls to a few
tokens per second. The same model on a card that fits it whole is typically an order of
magnitude faster. Nothing in the application changes this, and — deliberately — nothing in
the application inspects the GPU or rewrites model configuration to compensate. That is a
deployment decision made by a person with `.env`.

Do not design game mechanics around one machine's throughput.

## Epic 1 baseline (measured)

Collected 2026-08-12 with the procedure in [Running it](#running-it).

**Machine.** Windows 11, 12th Gen Intel Core i5-12500H, NVIDIA GeForce RTX 3050 Laptop GPU
with 4 GB VRAM, driver 595.95. Ollama 0.32.7.

**Configuration.** `STORY_PROVIDER=ollama`, `OLLAMA_MODEL=mistral:7b` (7.2B, Q4_K_M),
`OLLAMA_NUM_CTX=8192`, `STORY_MAX_OUTPUT_TOKENS=1024`, `OLLAMA_KEEP_ALIVE=30m`,
`OLLAMA_STREAMING_ENABLED=false`. Model residency at run time: 5.61 GB total, 2.39 GB in
VRAM — **43% GPU / 57% CPU**.

**Three turns, warm-started session, one `story_turn` generation each.**

| # | prompt tok | out tok | total ms | load ms | prompt eval ms | gen ms | prompt tok/s | gen tok/s | ctx used | done |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 5409 | 275 | 99712.7 | 12011.3 | 15577.1 | 72046.6 | 347.2 | 3.8 | 66% | stop |
| 2 | 5506 | 156 | 65253.1 | 62.7 | 22391.1 | 42708.0 | 245.9 | 3.6 | 67% | stop |
| 3 | 5588 | 308 | 114313.6 | 58.9 | 21924.1 | 92253.8 | 254.9 | 3.3 | 68% | stop |

**Where the time went.**

| turn | client wall ms | total_turn_ms | story_generation_ms | non_llm_application_ms | llm_call_count |
|---|---|---|---|---|---|
| 1 | 100144.6 | 100139.2 | 100082.6 | 56.6 | 1 |
| 2 | 66026.3 | 66019.5 | 65951.9 | 67.6 | 1 |
| 3 | 115161.3 | 115152.8 | 115082.7 | 70.0 | 1 |

The headline: **the application layer accounts for 56–70 ms of a 66–115 second turn.**
Everything else is the model. Across the 16-turn run below the same figure was 31–93 ms
against turns of 92–218 seconds, so this holds as the load grows. Optimising SQL here would
be optimising 0.06% of the wait, which is exactly what "measure first" was meant to prevent.

**Output budget.** Never reached: 156–308 tokens generated against a 1024-token budget,
every generation ending `done_reason: stop`. `budget_reached: 0` across the run.

**Cold versus warm.** First generation after the model was evicted: `load_ms = 12011.3`.
Subsequent generations with the model resident: `62.7` and `58.9 ms`. A cold turn therefore
costs ~12 seconds before a token is produced.

**Keep-alive.** With `OLLAMA_KEEP_ALIVE=30m`, `ollama ps` reported `expires_at` 29.5 minutes
after the last request, confirming the value reaches the provider.

### Prompt growth over sixteen turns

Three turns cannot show a plateau, and the plateau is the whole claim. A separate 16-turn
run, warm throughout, one `story_turn` per turn:

| turn | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 | 16 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| prompt tokens | 5414 | 5518 | 5570 | 5724 | 5815 | 5981 | 6085 | 6176 | 6247 | 6249 | 6341 | 6399 | 6440 | 6482 | 6397 | 6430 |
| ctx used | 66% | 67% | 68% | 70% | 71% | 73% | 74% | 75% | 76% | 76% | 77% | 78% | 79% | 79% | 78% | 78% |

**Turns 1–9 grow about 104 tokens per turn. Turns 10–16 grow about 30, and stop being
monotonic** — turn 15 is 85 tokens *smaller* than turn 14. The knee is at turn 10, which is
where `RECENT_MESSAGE_LIMIT = 20` begins to bind: two messages per turn, so from turn 10
onward the transcript slice evicts as much as it gains and its contribution is constant.

The remaining ~30 tokens per turn are the other slices still filling toward caps that bind
later — memories toward 30, events toward 8 + 12. Those saturate too; the run simply is not
long enough to watch them do it, and pretending otherwise would be the same mistake as
claiming a plateau from three turns. What the caps guarantee is a *ceiling*: with every
slice full, the context cannot exceed roughly 6.7k tokens, and turn 16 measured 6430.

That distinction is the point. Total persisted session state grew monotonically across all
16 turns; prompt size grew 18.8% and then flattened. Prompt size is bounded by the caps in
`context_builder.py`, not by how much the campaign has accumulated — which is exactly the
property a later foundation can break, and exactly what re-running this detects.

### Sustained load is not steady state

The same run measured turn latency rising far faster than the prompt did:

| | turn 1 | turn 16 | median |
|---|---|---|---|
| client wall clock | 92.0 s | 159.1 s | 136.9 s |
| prompt eval | 34143 ms | 53228 ms | — |
| prompt tok/s | 158.6 | 120.8 | — |

Prompt size grew 18.8%; wall clock grew ~73%, peaking at **217.8 s** on turn 11. Prompt
throughput did not improve with a warm cache — it *fell*, from 158 tok/s to 55 tok/s at the
worst point, and the earlier three-turn run on the same machine and model measured 245–347
tok/s for the same work.

So the machine gets slower the longer it is asked to generate, most plausibly thermal
throttling on a laptop under a 36-minute sustained load with 57% of the model on the CPU.
No application change is implicated: `non_llm_application_ms` stayed at 31–93 ms throughout.

The consequence for anyone using this document: **a single run is not reproducible against
another single run on this class of hardware.** Compare prompt token counts, which are
deterministic given the same context, and treat wall-clock comparisons as valid only within
one run.

**Failure path.** The first attempt of this baseline ran with the default
`OLLAMA_TIMEOUT_SECONDS=120` and timed out. It is worth recording because it is what a
failed generation looks like in the instrumentation — the fields that were not measured are
absent, not zero:

```text
llm.generation purpose=story_turn provider=ollama model=mistral:7b status=error
  provider_metrics=unavailable error=timeout prompt_tokens=- generated_tokens=- total_ms=-
  load_ms=- prompt_eval_ms=- generation_ms=- prompt_tps=- generation_tps=-
  client_ms=120535.328
llm.turn turn=1 total_ms=120581.2 story_ms=120545.7 app_ms=35.5 llm_calls=1
```

with `error_count: 1` and `measured_sample_count: 0` in the summary. **On this hardware,
`OLLAMA_TIMEOUT_SECONDS=120` is not enough for a cold turn.** 900 was used for the run
above. That is a real finding about a 7B model on a 4 GB card, not a defect in the timeout.

### What this baseline says about playability

A 66–115 second turn is not a playable experience, and the longer run makes it worse rather
than better: a median of 137 seconds and a worst case of 218. No amount of application-layer
work will change it while 57% of the model is running on a CPU — the application's share is
0.04% of the wait. The options are a machine that fits the model, a smaller model, or a
shorter context — all of them deployment choices, all of them now measurable. That is what
this epic was for.

## Running it

The harness lives at `apps/api/app/scripts/llm_baseline.py`. It drives the real HTTP API,
because the number that matters is the one a player's request pays.

It creates a world, two characters and a session, so point it at a throwaway database —
not the one holding a campaign:

```bash
DATABASE_URL="sqlite+aiosqlite:///$TMPDIR/baseline.db" .venv/Scripts/python.exe -m alembic upgrade head
```

Then run a second API process against that database on a spare port, so a dev server on
8000 is left alone:

```bash
DATABASE_URL="sqlite+aiosqlite:///$TMPDIR/baseline.db" OLLAMA_TIMEOUT_SECONDS=900 .venv/Scripts/python.exe -m uvicorn app.main:app --port 8011
```

And collect:

```bash
.venv/Scripts/python.exe -m app.scripts.llm_baseline --api-url http://127.0.0.1:8011 --turns 3 --json baseline.json
```

It refuses to run against the mock provider unless you pass `--allow-mock`, since mock
timings are not a baseline. It asserts no latency at all — hardware decides these numbers,
and a threshold hard-coded in the harness would be a fact about one laptop pretending to
be a requirement.

Three turns is enough to read the per-generation fields. **The growth check needs
`--turns 16`**, because the transcript cap does not bind until turn 10 and a shorter run
cannot tell a plateau from a straight line. Budget an hour on hardware like the above.

**For a cold-start number**, unload the model first and let the first turn pay the load:

```bash
ollama stop mistral:7b
```

## How a later epic compares against this

Every foundation after Epic 1 adds state, and state is what prompts are built from. The
regression contract is deliberately about *shape*, not seconds:

1. **Re-run the baseline** on the same machine, same model, same `num_ctx`, warm.
2. **Compare `prompt_tokens` at turn 1**, not wall-clock time. This is the number that
   reveals whether a new foundation quietly attached itself to the context. A change of a
   few hundred tokens for a genuinely new bounded projection is a design decision; a change
   that scales with how much is stored is a defect.
3. **Compare growth across turns**, with enough turns to see the knee — at least 12, since
   the transcript cap does not bind until turn 10. Epic 1 measured ~104 tokens/turn before
   it and ~30 after. If the slope after turn 10 is not visibly lower than before it, some
   retrieval lost its cap.
4. **Compare `llm_call_count` per turn.** It is 1. A second synchronous call needs a stated
   reason, because it roughly doubles the wait.
5. **Compare `non_llm_application_ms`.** It was tens of milliseconds. If it reaches
   seconds, the application itself has started to cost something and *then* profiling the
   database is worth doing.
6. **Check `budget_reached_count` and `error_count`.** Both were 0. A turn newly hitting
   its output budget means the model is being asked for a bigger document than
   `STORY_MAX_OUTPUT_TOKENS` allows.

Latency comparisons across machines are meaningless; token counts and call counts are not.
Compare those.

## Reading the instrumentation

One structured line per generation. INFO normally; **WARNING** when the call is slower
than `LLM_SLOW_CALL_THRESHOLD_MS`, when it ended on its output budget, or when the prompt
filled 90% of the context window; **ERROR** when the generation failed. On this hardware
every real call is a warning, which is the honest reading of it:

```text
WARNING app.llm.performance: llm.generation purpose=story_turn provider=ollama
  model=mistral:7b status=ok session=d7401a94-... prompt_tokens=5409 generated_tokens=275
  budget=1024 budget_reached=false done=stop context_window=8192
  context_utilization=0.6603 total_ms=99712.678 load_ms=12011.307
  prompt_eval_ms=15577.109 generation_ms=72046.574 prompt_tps=347.24 generation_tps=3.82
  client_ms=100067.3 request=5d8990df-...
```

(wrapped here; it is one line). The full record also goes to the log record's `extra` as
`llm_generation`, for anything machine-shaped.

and one per turn:

```text
llm.turn session=... turn=1 total_ms=100139.2 story_ms=100082.6 app_ms=56.6 llm_calls=1
```

The same records are readable while the process is up:

- `GET /api/v1/dev/llm/performance?limit=N` — recent generations across the process
- `GET /api/v1/dev/sessions/{session_id}/llm-performance` — one session's generations,
  its per-turn splits, and a summary

Both are development-only (`APP_ENV=development|test`), bounded, in-memory, and lost on
restart. Neither returns a prompt or a generated word — token counts and durations only.

## Deliberately not done

No metrics database or migration. No Prometheus, OpenTelemetry, or hosted analytics. No
GPU inspection or automatic model selection. No query rewriting — the measurements say the
application is 0.06% of a turn. No semantic retrieval, no memory consolidation, no
narrative context assembly: those are later epics, and this one exists so they can be
judged against something.
