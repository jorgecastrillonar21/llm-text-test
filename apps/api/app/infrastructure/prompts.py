"""Loads prompts from version-controlled markdown files.

Prompts are files, not string literals, so they can be diffed and reviewed. The
`version` key in front matter identifies the prompt revision; see
docs/ai-contract.md for the versioning strategy.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@dataclass(frozen=True, slots=True)
class Prompt:
    name: str
    version: str
    body: str


def _parse(raw: str, fallback_name: str) -> Prompt:
    name = fallback_name
    version = "0"
    body = raw

    if raw.startswith("---"):
        _, _, rest = raw.partition("---\n")
        front_matter, sep, remainder = rest.partition("---\n")
        if sep:
            body = remainder
            for line in front_matter.splitlines():
                key, delim, value = line.partition(":")
                if not delim:
                    continue
                if key.strip() == "version":
                    version = value.strip()
                elif key.strip() == "name":
                    name = value.strip()

    return Prompt(name=name, version=version, body=body.strip())


@lru_cache
def load_prompt(name: str) -> Prompt:
    path = PROMPTS_DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"prompt '{name}' not found at {path}")
    return _parse(path.read_text(encoding="utf-8"), name)
