"""Collects the LLM performance baseline against a running API.

    uv run python -m app.scripts.llm_baseline --api-url http://127.0.0.1:8000

Over HTTP, on purpose. The numbers this reports are the ones a player's request
actually pays: the whole path through FastAPI, the context builder, the provider and
the transaction, measured from outside, plus the server's own split of that latency
read back from `/api/v1/dev/*/llm-performance`. Driving the application in-process
would measure a path nobody runs.

It creates its own world and session and plays several turns in it, so it needs a
database it is allowed to write to. Point it at a throwaway one -- run a second API
process with `DATABASE_URL` set elsewhere -- rather than at a campaign you care about;
nothing here deletes anything, but a baseline world in a save file is still litter.

Nothing here asserts a latency. Hardware decides these numbers, and a threshold written
into this file would be a number about my machine masquerading as a requirement. It
reports; comparing runs is a person's job, and docs/llm-performance-baseline.md says
how.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from typing import Any

import httpx

TURN_ACTIONS = [
    # Turn 1: the largest context a fresh session can have and the smallest history --
    # the opening. This build has no separate opening generation, so this is the
    # closest real thing to one.
    "I push open the door and look around the room.",
    "I ask the nearest person what has been happening here lately.",
    "I mention the rumour I heard on the road and watch how they react.",
    "I ask them to tell me who else I should be talking to.",
    "I thank them, step outside, and take stock of what I have learned.",
]

DEFAULT_TURNS = 3
REQUEST_TIMEOUT = 600.0
"""Ten minutes. A cold 8B model on CPU can genuinely take minutes for one turn, and a
harness that timed out before the thing it is measuring finished would be reporting on
itself."""


class BaselineError(RuntimeError):
    """Something the run cannot continue past, phrased for the person who ran it."""


async def main() -> int:
    args = _parse_args()
    async with httpx.AsyncClient(base_url=args.api_url, timeout=REQUEST_TIMEOUT) as client:
        try:
            return await _run(client, args)
        except BaselineError as exc:
            print(f"\nBaseline aborted: {exc}", file=sys.stderr)
            return 1
        except httpx.HTTPError as exc:
            print(f"\nCannot reach the API at {args.api_url}: {exc}", file=sys.stderr)
            return 1


async def _run(client: httpx.AsyncClient, args: argparse.Namespace) -> int:
    status = await _get(client, "/api/v1/ai/status")
    story = status["story"]
    print(f"Provider: {story['provider']}  model={story.get('model')}  state={story['state']}")
    for key, value in (story.get("extra") or {}).items():
        print(f"  {key}: {value}")

    if story["provider"] == "mock" and not args.allow_mock:
        raise BaselineError(
            "the API is running the mock provider, and mock timings are not a baseline. "
            "Set STORY_PROVIDER=ollama, or pass --allow-mock to exercise the harness itself."
        )
    if story["state"] != "ready":
        raise BaselineError(f"the story provider is not ready: {story['detail']}")

    session_id = await _bootstrap(client, args)
    print(f"\nSession {session_id}: playing {args.turns} turn(s).\n")

    wall_ms: list[float] = []
    for index in range(args.turns):
        action = TURN_ACTIONS[index % len(TURN_ACTIONS)]
        started = time.perf_counter()
        response = await client.post(
            f"/api/v1/sessions/{session_id}/turns", json={"action": action}
        )
        elapsed = (time.perf_counter() - started) * 1000
        if response.status_code != 200:
            raise BaselineError(
                f"turn {index + 1} failed with HTTP {response.status_code}: {response.text[:400]}"
            )
        wall_ms.append(elapsed)
        print(f"  turn {index + 1}: {elapsed / 1000:6.1f}s  {action[:52]}")

    report = await _get(client, f"/api/v1/dev/sessions/{session_id}/llm-performance")
    _print_report(report, wall_ms)
    if args.json:
        _write_json(args.json, report, wall_ms, story)
    return 0


async def _bootstrap(client: httpx.AsyncClient, args: argparse.Namespace) -> str:
    """A small world with enough in it to produce a representative prompt."""
    world = await _post(
        client,
        "/api/v1/worlds",
        {
            "name": "LLM baseline",
            "genre": "fantasy",
            "language": args.language,
            "description": "A throwaway world for measuring generation cost.",
            "setting": "A waystation on a trade road, a day from anywhere.",
            "rules_preset": "shonen",
        },
    )
    for character in _CHARACTERS:
        await _post(client, f"/api/v1/worlds/{world['id']}/characters", character)
    session = await _post(
        client,
        "/api/v1/sessions",
        {
            "world_id": world["id"],
            "title": "Baseline run",
            "player_name": "Rin",
            "player_description": "A courier who asks too many questions.",
        },
    )
    return str(session["id"])


def _print_report(report: dict[str, Any], wall_ms: list[float]) -> None:
    # The endpoint returns newest first; a baseline reads forwards.
    generations = list(reversed(report["generations"]))
    turns = list(reversed(report["turns"]))
    if not generations:
        raise BaselineError(
            "the API recorded no generations. Is this the same process that served the "
            "turns? The buffer is in-process and does not survive a restart."
        )

    print("\n== Per generation ==")
    header = (
        f"{'#':>2}  {'purpose':<18} {'prompt':>7} {'out':>5} {'total':>8} {'load':>8} "
        f"{'p.eval':>8} {'gen':>8} {'p tok/s':>8} {'g tok/s':>8} {'ctx%':>5}  done"
    )
    print(header)
    print("-" * len(header))
    for index, record in enumerate(generations, start=1):
        print(
            f"{index:>2}  {record['purpose']:<18} "
            f"{_n(record['prompt_tokens']):>7} {_n(record['generated_tokens']):>5} "
            f"{_ms(record['total_ms']):>8} {_ms(record['load_ms']):>8} "
            f"{_ms(record['prompt_eval_ms']):>8} {_ms(record['generation_ms']):>8} "
            f"{_f(record['prompt_tokens_per_second']):>8} "
            f"{_f(record['generation_tokens_per_second']):>8} "
            f"{_pct(record['prompt_context_utilization']):>5}  "
            f"{record['done_reason']}"
            f"{'  BUDGET REACHED' if record['output_budget_reached'] else ''}"
        )

    print("\n== Per turn: where the time went ==")
    header = (
        f"{'turn':>4} {'wall (client)':>14} {'total_turn_ms':>14} "
        f"{'story_gen_ms':>13} {'non_llm_ms':>11} {'llm calls':>10}"
    )
    print(header)
    print("-" * len(header))
    for index, record in enumerate(turns):
        client_ms = wall_ms[index] if index < len(wall_ms) else None
        print(
            f"{record['turn_index']:>4} {_ms(client_ms):>14} {_ms(record['total_turn_ms']):>14} "
            f"{_ms(record['story_generation_ms']):>13} "
            f"{_ms(record['non_llm_application_ms']):>11} {record['llm_call_count']:>10}"
        )

    _print_growth(generations)

    first_load = generations[0].get("load_ms")
    print("\n== Cold vs warm ==")
    if first_load is None:
        print("  The provider reported no load_duration. Cold-start cost is unmeasured.")
    else:
        later = [g["load_ms"] for g in generations[1:] if g.get("load_ms") is not None]
        print(f"  First call load_ms:  {first_load:.1f}")
        if later:
            print(f"  Later calls load_ms: {', '.join(f'{value:.1f}' for value in later)}")
        print(
            "  A large first value and near-zero later ones is a cold model load. "
            "Restart Ollama (or wait out OLLAMA_KEEP_ALIVE) and re-run to measure it again."
        )

    summary = report["summary"]
    print("\n== Summary ==")
    print(f"  samples: {summary['sample_count']} (measured: {summary['measured_sample_count']})")
    print(
        f"  total_ms  latest={_ms(summary['latest_total_ms'])} "
        f"avg={_ms(summary['average_total_ms'])} max={_ms(summary['max_total_ms'])}"
    )
    print(
        f"  prompt tokens  latest={_n(summary['latest_prompt_tokens'])} "
        f"avg={_f(summary['average_prompt_tokens'])} max={_n(summary['max_prompt_tokens'])}"
    )
    print(
        f"  output tokens  latest={_n(summary['latest_generated_tokens'])} "
        f"avg={_f(summary['average_generated_tokens'])} max={_n(summary['max_generated_tokens'])}"
    )
    print(f"  generation tok/s avg: {_f(summary['average_generation_tokens_per_second'])}")
    print(f"  max context utilization: {_pct(summary['max_prompt_context_utilization'])}")
    print(f"  budget reached: {summary['budget_reached_count']}   errors: {summary['error_count']}")
    if wall_ms:
        print(f"  client wall clock: median {statistics.median(wall_ms) / 1000:.1f}s")


def _print_growth(generations: list[dict[str, Any]]) -> None:
    """Turn 1 against the last turn -- the number this whole task exists to watch."""
    turns = [g for g in generations if g["purpose"] == "story_turn"]
    sizes = [g["prompt_tokens"] for g in turns if g.get("prompt_tokens") is not None]
    print("\n== Prompt growth ==")
    if len(sizes) < 2:
        print("  Fewer than two measured turns; run with --turns 3 or more.")
        return
    first, last = sizes[0], sizes[-1]
    delta = last - first
    ratio = (delta / first * 100) if first else 0.0
    print(f"  turn 1: {first} prompt tokens")
    print(f"  turn {len(sizes)}: {last} prompt tokens   ({delta:+d}, {ratio:+.1f}%)")
    print(
        "  Expect a small increase from the growing transcript and history, then a "
        "plateau once the recency caps in context_builder bind. Growth that tracks the "
        "session's total size is the failure this baseline exists to catch."
    )


def _write_json(
    path: str, report: dict[str, Any], wall_ms: list[float], story: dict[str, Any]
) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(
            {"provider": story, "client_wall_ms": wall_ms, "report": report},
            handle,
            indent=2,
            default=str,
        )
    print(f"\nWrote {path}")


async def _get(client: httpx.AsyncClient, path: str) -> dict[str, Any]:
    response = await client.get(path)
    if response.status_code != 200:
        raise BaselineError(f"GET {path} returned HTTP {response.status_code}: {response.text}")
    body: dict[str, Any] = response.json()
    return body


async def _post(client: httpx.AsyncClient, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = await client.post(path, json=payload)
    if response.status_code >= 300:
        raise BaselineError(f"POST {path} returned HTTP {response.status_code}: {response.text}")
    body: dict[str, Any] = response.json()
    return body


def _n(value: object) -> str:
    return "-" if value is None else str(value)


def _ms(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def _f(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.0f}%"


_CHARACTERS: list[dict[str, Any]] = [
    {
        "name": "Elena",
        "description": "The waystation's keeper, and the reason it still stands.",
        "appearance": "Grey-streaked braid, burn scars on both forearms.",
        "personality": "Sardonic, watchful, slow to trust and slower to say so.",
        "speech_style": "Short sentences. Answers the question under the question.",
        "goals": ["Keep the road open", "Find out who burned the north barn"],
        "secrets": ["She recognises Rin's courier seal and has not said so"],
    },
    {
        "name": "Bram",
        "description": "A caravan guard between contracts, drinking through the wait.",
        "appearance": "Broad, sunburnt, a splint on two fingers.",
        "personality": "Loud, generous, indiscreet after the second cup.",
        "speech_style": "Talks in stories, none of them short.",
        "goals": ["Get paid", "Avoid the eastern road"],
        "secrets": ["He left his last caravan before the ambush, not during it"],
    },
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--turns", type=int, default=DEFAULT_TURNS)
    parser.add_argument("--language", default="en", choices=["en", "es"])
    parser.add_argument("--json", default=None, help="Also write the raw report here.")
    parser.add_argument(
        "--allow-mock",
        action="store_true",
        help="Run against the mock provider. Exercises the harness; measures nothing.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
