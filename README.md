# Playable Story Engine

A local-first, AI-driven interactive anime/RPG engine. Persistent worlds, persistent
NPCs with their own motivations, memory that survives across sessions, and free-form
player input — running entirely on your own machine.

Inference happens on your PC (Ollama for text, ComfyUI for images). Your phone is just
a client. **No paid AI API is ever required**, and the whole application runs with a
deterministic mock provider if you have no AI runtime installed at all.

## Architecture

```text
Android phone / desktop browser
              |
              v
     React + Vite PWA          (apps/web)
              |  /api/*  (Vite dev proxy)
              v
         FastAPI API           (apps/api)
              |
       +------+------+
       |             |
       v             v
     Ollama        ComfyUI     (optional, local)
       |             |
       v             v
   Local LLM     Local image AI
              |
              v
           SQLite              (data/ooc.db)
```

A pragmatic modular monolith. External AI systems sit behind ports
(`StoryGeneratorPort`, `ImageGeneratorPort`); the domain and application layers never
import Ollama or ComfyUI. See [docs/architecture.md](docs/architecture.md).

## Requirements

| | |
|---|---|
| Node | 24 LTS (≥ 20.19 works; Vite 8 requires it) |
| pnpm | 9+ |
| Python | ≥ 3.13 |
| uv | any recent (`pip install uv`) |
| Ollama | **optional** — only for real text generation |
| ComfyUI | **optional** — not needed for the core loop |

## Quick start

No AI runtime needed. This is the shortest path to a playable turn:

```bash
git clone <this-repo> && cd ooc-clone
```

Windows:

```bash
powershell -ExecutionPolicy Bypass -File scripts/bootstrap.ps1
```

macOS / Linux:

```bash
bash scripts/bootstrap.sh
```

Either script checks your toolchain versions, creates `.env` from `.env.example` if you
do not have one, then runs `pnpm bootstrap` — install frontend deps, sync the Python
env, apply the database migration, seed the demo world. All of it is re-runnable.

(The npm script is not called `setup` because `pnpm setup` is one of pnpm's own
commands — it would rewrite your `PATH` instead of running this.)

Then:

```bash
pnpm dev
```

Open <http://localhost:5173>, pick **The Fractured Crown**, start a game, and type
anything. The defaults are `STORY_PROVIDER=mock` and `IMAGE_PROVIDER=disabled`, so this
works with nothing else installed.

The mock provider is a rule-based stand-in, not a language model. Its prose is
deliberately plain — it exists so the system is runnable and testable end to end.

## Story language

A world's language is chosen when the world is created and is **fixed for its
lifetime**. Narration, dialogue, suggested actions, and memories are all written in it.
This is intentional: a save file whose transcript is half English and half Spanish would
feed inconsistent context back into the model.

The **interface** language is separate and switchable in Settings (English / Español),
defaulting to your browser's language.

## Switching to Ollama

1. Install Ollama and make sure it is serving (default `http://127.0.0.1:11434`).
2. Pull any instruct model that supports structured output. Smaller is fine to start:

   ```bash
   ollama pull llama3.1:8b
   ```

3. In `.env`:

   ```dotenv
   STORY_PROVIDER=ollama
   OLLAMA_MODEL=llama3.1:8b
   OLLAMA_NUM_CTX=8192
   ```

   `OLLAMA_NUM_CTX` matters more than it looks. Ollama defaults to a 4096-token
   context regardless of what the model supports, and llama.cpp discards the *head*
   of an oversized prompt — the story rules, the world, the characters — without
   raising anything. A full context at the current retrieval limits measures ~6.7k
   tokens, so at the default roughly two thirds is thrown away and every world starts
   reading the same. Do not lower this below 8192 without also lowering the limits in
   [context_builder.py](apps/api/app/application/context_builder.py).

4. Restart the API. Check **Settings → AI status**, or:

   ```bash
   curl http://127.0.0.1:8000/api/v1/ai/status
   ```

No model is downloaded during setup, and none is mandatory. If Ollama is unreachable or
the model is missing, the API says so explicitly — it will **not** silently fall back to
the mock provider, because that would hide a configuration error behind plausible prose.
To go back, set `STORY_PROVIDER=mock`.

## ComfyUI (optional)

Images are not part of the core loop yet. The adapter boundary exists and can verify
connectivity and submit an API-format workflow, but retrieving finished images is Phase
4. See [ai/comfyui/README.md](ai/comfyui/README.md).

```dotenv
IMAGE_PROVIDER=comfyui
COMFYUI_BASE_URL=http://127.0.0.1:8188
COMFYUI_WORKFLOW_PATH=ai/comfyui/workflows/your-workflow.api.json
```

## Playing from your phone

```bash
pnpm dev:lan
```

Then open `http://<your-pc-lan-ip>:5173` on the phone, on the same network. The phone
talks only to the frontend origin; Vite proxies `/api` to the backend.

**Never port-forward Ollama or ComfyUI to the internet**, and do not expose them
directly to the phone — the application backend is the boundary. See
[docs/mobile-access.md](docs/mobile-access.md).

## Common commands

| Command | Does |
|---|---|
| `pnpm bootstrap` | install + sync + migrate + seed |
| `pnpm dev` | API on :8000 and web on :5173 |
| `pnpm dev:lan` | same, bound to your LAN |
| `pnpm db:migrate` | apply migrations |
| `pnpm db:seed` | create the demo world (idempotent) |
| `pnpm test` | backend + frontend tests |
| `pnpm test:e2e` | Playwright smoke flow (needs the API running) |
| `pnpm lint` | ruff + eslint |
| `pnpm typecheck` | mypy + tsc (`:api` / `:web` for one half) |
| `pnpm build` | frontend production build |
| `pnpm api:generate` | regenerate TS types from OpenAPI (API must be running) |

## CI

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs three jobs on Node 24 and
Python 3.13: **backend** (`uv sync --locked`, `ruff check`, `ruff format --check`,
`mypy app`, `pytest`), **frontend** (`pnpm install --frozen-lockfile`, lint, typecheck,
test, build), and **e2e** (Playwright against a real API in mock mode). No job needs
Ollama or ComfyUI — a missing AI runtime is a hard error by design, so CI would fail
rather than quietly skip.

It triggers on pushes to `master` and on pull requests. The push filter previously said
`main`, a branch this repository does not have, so no push ever ran CI.

## Documentation

- [Architecture](docs/architecture.md) — modules, ports, request and turn lifecycles
- [AI contract](docs/ai-contract.md) — `StoryContext`, `TurnGeneration`, semantics
- [World rules](docs/world-rules.md) — how a universe is configured, and the presets
- [Simulation time](docs/world-state-time.md) — the fictional clock, calendar, scheduling
- [World facts](docs/world-state-facts.md) — objective truth, authority, state changes
- [Development](docs/development.md) — workflows, migrations, testing, contract sync
- [Mobile access](docs/mobile-access.md) — LAN, PWA, HTTPS, security
- [Roadmap](docs/roadmap.md) — what comes next
