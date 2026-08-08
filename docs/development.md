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
