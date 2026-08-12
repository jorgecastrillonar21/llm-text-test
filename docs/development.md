# Development

## Layout

```text
apps/api    FastAPI backend, uv-managed (own .venv)
apps/web    React + Vite PWA, pnpm workspace member
ai/comfyui  workflow files and notes
data/       runtime SQLite database (gitignored)
docs/       this documentation
scripts/    uv resolver + bootstrap helpers
```

Root scripts drive both stacks, so you rarely need to `cd`.

## First run

```bash
pnpm bootstrap
```

Equivalent to: `pnpm install`, `uv sync`, `alembic upgrade head`, seed demo world.

Or, with prerequisite checks and `.env` creation:

```bash
bash scripts/bootstrap.sh
```

```bash
powershell -ExecutionPolicy Bypass -File scripts/bootstrap.ps1
```

The npm script is deliberately **not** named `setup`: `pnpm setup` is a built-in pnpm
command that configures `PNPM_HOME` and rewrites your `PATH`, and it shadows any package
script of the same name. `pnpm test`, `pnpm start`, and `pnpm stop` are built-in
*aliases* that do run the matching script, so those are safe.

If `uv` is not on your PATH, `scripts/uv.mjs` falls back to `python -m uv`, so
`pip install uv` is enough.

## Running

```bash
pnpm dev
```

API on `:8000` (docs at <http://127.0.0.1:8000/docs>), web on `:5173`. The Vite proxy
forwards `/api` and `/health` to the backend, so the browser only ever sees one origin.

To run one side alone: `pnpm dev:api` / `pnpm dev:web`.

## Database

```bash
pnpm db:migrate
```

```bash
pnpm db:revision "add inventory"
```

Autogenerate compares `Base.metadata` against the live database — **always read the
generated migration before committing it**. SQLite cannot `ALTER` most things in place,
so `render_as_batch=True` is enabled; batch mode rewrites the table.

"Rewrites the table" means copy, `DROP TABLE`, rename — and a DROP fires every
`ON DELETE CASCADE` aimed at that table. `migrations/env.py` therefore runs migrations
with `PRAGMA foreign_keys=OFF` and checks `PRAGMA foreign_key_check` afterwards. Without
that, altering a column on `worlds` deletes every character, session, message and memory
in the database, and the migration reports success. If you write a migration that touches
a table other rows point at, extend `tests/test_migrations.py` to migrate a *populated*
database and assert the rows are still there — that is the only check that catches this.

Models use `UtcDateTime`, so generated migrations reference
`app.infrastructure.db.types`. The migration template imports it for you.

Reset a broken dev database (destroys saves):

```bash
rm -f data/ooc.db data/ooc.db-wal data/ooc.db-shm && pnpm db:migrate && pnpm db:seed
```

The seed is idempotent — it does nothing if **The Fractured Crown** already exists.

## Testing

```bash
pnpm test
```

```bash
pnpm test:api
```

```bash
pnpm test:web
```

Backend tests use isolated temporary SQLite databases per test (`tmp_path`), created
from `Base.metadata`. The Alembic migration itself is verified separately by
`tests/test_migrations.py`, so per-test migration runs do not dominate the suite.

`tests/conftest.py` exposes `FailingStoryGenerator` for provider-failure paths, and
`make_story_context` for exercising providers directly.

### Development-only endpoints

`/api/v1/dev/*` is mounted only when `APP_ENV` is `development` or `test` — an
allowlist, so a typo leaves developer tooling off rather than quietly switching it on.
It exists because nothing in the game moves time or changes state yet:

```text
POST   /api/v1/dev/sessions/{id}/advance-time
POST   /api/v1/dev/sessions/{id}/scheduled-events
GET    /api/v1/dev/sessions/{id}/scheduled-events/due
DELETE /api/v1/dev/scheduled-events/{id}
POST   /api/v1/dev/sessions/{id}/world-state/changes
GET    /api/v1/dev/sessions/{id}/world-state
GET    /api/v1/dev/sessions/{id}/world-state/check
POST   /api/v1/dev/sessions/{id}/situations/{situation_id}/progress
GET    /api/v1/dev/llm/performance
GET    /api/v1/dev/sessions/{id}/llm-performance
```

The due-events endpoint is the seam nothing consumes yet. Advancing time marks what the
clock reached `due` and stops there — it does not execute it — so this is how to see what
the world is owed and has not been given. Until a dispatcher exists, an interrupting
event that nobody answers keeps stopping the clock at its minute, which is the honest
answer rather than a bug. See
[DUE is not PROCESSED](world-state-time.md#due-is-not-processed).

The mutation endpoint carries spatial and situation mutations too -- `update_location_state`,
`update_connection_state`, `start_situation`, `update_situation` and `resolve_situation`
travel in the same batch as fact changes, so one event can raise a siege's intensity,
collapse a gate, block the crossing and start a food crisis together or not at all.
Authoring geography is a different act and lives on the ordinary API under `/worlds`; see
[world-state-locations.md](world-state-locations.md#http-surface).

The progression endpoint runs one situation from where it was last evaluated to where the
session clock now is. The interval is not yours to choose, so advance the clock first:

```bash
curl -X POST .../dev/sessions/$S/advance-time -d '{"requested_minutes":360,"reason":"debug"}'
curl -X POST .../dev/sessions/$S/situations/$I/progress -d '{}'
```

It is the only caller the progression boundary has until a SimulationEngine exists; see
[world-state-situations.md](world-state-situations.md#progression).

### Inspecting what happened

Every change the dev endpoints make goes through the resolution pipeline, so both of them
leave a trail you can read back on the ordinary API:

```bash
curl ".../sessions/$S/resolutions?limit=20"
```

```bash
curl ".../sessions/$S/events?min_importance=3&limit=20"
```

`resolutions` is the mechanical trail — the disposition, the resolver and its version, the
revision before and after. `events` is world history, and most resolutions produce none:
progressing a siege by six hours is a real state change and not a thing the story
remembers. Both are read-only, and there is deliberately no write counterpart to either;
see [event-resolution.md](event-resolution.md#http-surface).

Retrying an action is safe by construction. A turn is keyed by its `client_action_id` and a
due scheduled event by its own id, so re-sending one resolves it once and replays the
stored result — no second model call, no duplicate events, no clock advanced twice.

None of them is a shortcut. Each goes through the same application service a real caller
will use, so a paused world still refuses to advance and a state change is still checked
against the property's policy, the world's rules and the session's revision. `admin`
authority does not bypass the world's rules, and `story_director` sent to the mutation
endpoint still reaches `OPEN` properties and nothing else. See
[world-state-facts.md](world-state-facts.md).

### Why that turn was slow

```bash
curl ".../dev/sessions/$S/llm-performance"
```

returns the recent generations for one session, the per-turn latency split, and a summary.
The question it answers first is which half of the wait was the model:

```text
llm.turn session=... turn=1 total_ms=100139.2 story_ms=100082.6 app_ms=56.6 llm_calls=1
```

`GET /api/v1/dev/llm/performance` is the same thing across the whole process. Both are
in-memory, bounded by `LLM_METRICS_BUFFER_SIZE`, lost on restart, and return **no prompts
and no generated text** — token counts and durations only.

The same records are logged, one line per generation, under `app.llm.performance`. They
come out at WARNING when a call is slower than `LLM_SLOW_CALL_THRESHOLD_MS`, when it ended
on its output budget, or when the prompt filled 90% of `OLLAMA_NUM_CTX`.

To collect a full baseline rather than read one turn — cold-versus-warm load cost, prompt
growth across turns, tokens per second — use the harness, against a throwaway database and
a spare port so a running dev server is left alone:

```bash
.venv/Scripts/python.exe -m app.scripts.llm_baseline --api-url http://127.0.0.1:8011 --turns 3
```

[llm-performance-baseline.md](llm-performance-baseline.md#running-it) has the full
procedure and the numbers Epic 1 measured.

### E2E

Playwright drives the real backend in mock mode. **Start the API first**, then:

```bash
pnpm test:e2e
```

Playwright starts Vite itself. It targets `http://localhost:5173` rather than
`127.0.0.1` on purpose — Vite binds the hostname, which resolves to `::1` on Windows,
and a literal IPv4 URL never connects.

No test requires Ollama or ComfyUI.

## Lint, types, build

```bash
pnpm lint
```

```bash
pnpm typecheck
```

```bash
pnpm build
```

Backend is Ruff (`E,F,I,UP,B,SIM,C4,RUF`, line length 100). Frontend is ESLint with
`typescript-eslint`; `@typescript-eslint/no-explicit-any` is an **error**.

`pnpm typecheck` runs **both** type checkers: `mypy app` for the backend and `tsc` for
the frontend. Either half alone:

```bash
pnpm typecheck:api
```

```bash
pnpm typecheck:web
```

`mypy` runs with `disallow_untyped_defs = true` and is a CI gate in the backend job.
It was configured from the start but never executed, which meant the setting was
decorative until the boundary refactor wired it up.

TypeScript is pinned to 5.x because `typescript-eslint` does not yet support TS 7
(`peerDependencies: typescript >=4.8.4 <6.1.0`). Revisit when that lands.

## Keeping frontend and backend contracts in sync

`apps/web/src/api/types.ts` is a hand-maintained mirror of the backend DTOs, and it is
what the app imports. To check it against the real schema, run the API and:

```bash
pnpm api:generate
```

That writes `src/api/generated.ts` from the live OpenAPI document (gitignored, since it
is reproducible). Diff it against `types.ts` when you change a DTO.

Why not import the generated types directly? They are deeply nested
(`paths['/api/v1/worlds']['get']['responses'][200]…`) and would put that shape in every
component. The narrow hand-written types keep call sites readable, and drift shows up in
one file. If the DTO surface grows much larger, switch to generated types with local
aliases.

## Adding a story provider

1. Implement `StoryGeneratorPort` in `app/infrastructure/story/`: `generate_turn` and
   `status`.
2. Add a value to `StoryProvider` in `config.py`.
3. Add the case to `build_story_generator`.
4. Raise `StoryGenerationError` with an actionable message — never swallow a failure or
   fall back to another provider.

## Conventions

- Domain and application layers import no HTTP client, no FastAPI, no ORM models.
- Providers receive a `StoryContext`, never a database session.
- Write endpoints commit explicitly; `get_db` never commits (see
  [architecture.md](architecture.md#why-commits-are-explicit)).
- DTOs at the API boundary; ORM objects stay inside.
- TODOs must name the phase they belong to and appear in
  [roadmap.md](roadmap.md).
