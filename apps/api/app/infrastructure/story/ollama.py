"""Ollama story provider using the local /api/chat endpoint.

The model is constrained with the JSON Schema generated from TurnGeneration and
the reply is validated against the same model afterwards -- schema-constrained
decoding narrows the output but does not guarantee it.

This adapter never falls back to the mock provider. If STORY_PROVIDER=ollama is
set and Ollama is broken, the caller gets an error naming the cause; a silent
downgrade would hide a misconfiguration behind plausible prose.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from pydantic import ValidationError as PydanticValidationError

from app.application.contracts import OutcomeNarration, TurnGeneration
from app.application.ports import ProviderStatus
from app.application.story_context import OutcomeContext, StoryContext
from app.config import Settings
from app.domain.errors import StoryGenerationError
from app.infrastructure.prompts import load_prompt
from app.infrastructure.story.rendering import render_context, render_outcome

logger = logging.getLogger(__name__)

# Conservative floor for the truncation check. Real text tokenizes at 2.5-4.5
# characters per token; measured Spanish context sat at 3.0, and a truncated prompt
# measured 9.6. Six separates the two cases without false positives.
_MIN_CHARS_PER_TOKEN = 6


class OllamaStoryGenerator:
    name = "ollama"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._base_url = settings.ollama_base_url.rstrip("/")
        self._model = settings.ollama_model
        self._timeout = settings.ollama_timeout_seconds
        self._temperature = settings.ollama_temperature
        self._num_ctx = settings.ollama_num_ctx
        self._client = client

    async def generate_turn(self, context: StoryContext) -> TurnGeneration:
        parsed = await self._chat(
            prompt_name="story_director",
            rendered=render_context(context),
            schema=TurnGeneration.model_json_schema(),
        )
        try:
            return TurnGeneration.model_validate(parsed)
        except PydanticValidationError as exc:
            raise self._contract_error("TurnGeneration", exc) from exc

    async def narrate_outcome(self, context: OutcomeContext) -> OutcomeNarration:
        """Describe an already-committed outcome.

        Same transport, different prompt and a far narrower schema. The narrowness is
        the safety property: `OutcomeNarration` has one string field, so a model that
        wanted to revise the outcome has nowhere to write it.
        """
        parsed = await self._chat(
            prompt_name="outcome_narrator",
            rendered=render_outcome(context),
            schema=OutcomeNarration.model_json_schema(),
        )
        try:
            return OutcomeNarration.model_validate(parsed)
        except PydanticValidationError as exc:
            raise self._contract_error("OutcomeNarration", exc) from exc

    async def _chat(self, *, prompt_name: str, rendered: str, schema: dict[str, Any]) -> object:
        """One schema-constrained chat request, decoded to plain JSON.

        Validation against the contract stays with the caller: only it knows which
        contract the reply was supposed to satisfy, and an error message that cannot
        name it is one nobody can act on.
        """
        prompt = load_prompt(prompt_name)
        payload = {
            "model": self._model,
            "stream": False,
            "format": schema,
            # num_ctx is not optional: Ollama's 4096 default truncates the head of the
            # prompt -- system rules, world, characters -- and says nothing.
            "options": {"temperature": self._temperature, "num_ctx": self._num_ctx},
            "messages": [
                {"role": "system", "content": prompt.body},
                {"role": "user", "content": rendered},
            ],
        }

        data = await self._post("/api/chat", payload)
        self._warn_if_prompt_was_truncated(len(prompt.body) + len(rendered), data)

        content = data.get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise StoryGenerationError(
                "Ollama returned an empty message. The model may not support structured output.",
                provider=self.name,
                retryable=True,
            )

        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise StoryGenerationError(
                f"Ollama returned content that is not valid JSON: {exc}. "
                f"Model '{self._model}' may be too small to follow the schema reliably.",
                provider=self.name,
                retryable=True,
            ) from exc

    def _contract_error(self, contract: str, exc: PydanticValidationError) -> StoryGenerationError:
        return StoryGenerationError(
            f"Ollama response did not match the {contract} contract: "
            f"{exc.error_count()} validation error(s). First: {exc.errors()[0]['msg']}",
            provider=self.name,
            retryable=True,
        )

    def _warn_if_prompt_was_truncated(self, prompt_chars: int, data: dict[str, Any]) -> None:
        """Surface the silent context loss that num_ctx exists to prevent.

        Ollama reports how many prompt tokens it evaluated but never reports that it
        dropped the rest, so compare against a floor: natural language does not reach
        six characters per token, and a count below that means content was discarded.
        The check is one-sided on purpose -- it can miss truncation, but it does not
        cry wolf.

        This warns rather than raises. The heuristic is an estimate, and the turn it
        would abort still produced a valid, playable result; a log line the developer
        can act on is proportionate. The real fix is configuration, not a failed turn.
        """
        evaluated = data.get("prompt_eval_count")
        if not isinstance(evaluated, int) or evaluated <= 0:
            return
        if evaluated < prompt_chars // _MIN_CHARS_PER_TOKEN:
            logger.warning(
                "Ollama evaluated only %d prompt tokens for a %d-character prompt: part "
                "of it was discarded, starting with the system rules and the world and "
                "character definitions. OLLAMA_NUM_CTX is %d -- raise it, or lower the "
                "retrieval limits in context_builder.py.",
                evaluated,
                prompt_chars,
                self._num_ctx,
            )

    async def status(self) -> ProviderStatus:
        if not self._model.strip():
            return ProviderStatus(
                provider=self.name,
                state="misconfigured",
                detail="OLLAMA_MODEL is not set.",
                extra={"base_url": self._base_url},
            )
        try:
            data = await self._get("/api/tags")
        except StoryGenerationError as exc:
            return ProviderStatus(
                provider=self.name,
                state="unreachable",
                detail=str(exc),
                model=self._model,
                extra={"base_url": self._base_url},
            )

        installed = [m.get("name", "") for m in data.get("models", [])]
        if not self._model_installed(installed):
            return ProviderStatus(
                provider=self.name,
                state="misconfigured",
                detail=(f"Model '{self._model}' is not installed. Run: ollama pull {self._model}"),
                model=self._model,
                extra={"base_url": self._base_url, "installed": ", ".join(installed) or "none"},
            )

        return ProviderStatus(
            provider=self.name,
            state="ready",
            detail=f"Ollama reachable at {self._base_url}.",
            model=self._model,
            extra={"base_url": self._base_url},
        )

    def _model_installed(self, installed: list[str]) -> bool:
        # Ollama reports "llama3.1:8b"; users often configure the bare "llama3.1".
        wanted = self._model if ":" in self._model else f"{self._model}:latest"
        return wanted in installed or self._model in installed

    async def _get(self, path: str) -> dict[str, Any]:
        return await self._request("GET", path, None)

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", path, payload)

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        try:
            if self._client is not None:
                response = await self._client.request(method, url, json=payload)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.request(method, url, json=payload)
        except httpx.ConnectError as exc:
            raise StoryGenerationError(
                f"Cannot reach Ollama at {self._base_url}. Is `ollama serve` running?",
                provider=self.name,
                retryable=True,
            ) from exc
        except httpx.TimeoutException as exc:
            raise StoryGenerationError(
                f"Ollama timed out after {self._timeout:.0f}s. "
                f"Raise OLLAMA_TIMEOUT_SECONDS or use a smaller model.",
                provider=self.name,
                retryable=True,
            ) from exc

        if response.status_code == 404:
            raise StoryGenerationError(
                f"Ollama returned 404 for {path}. "
                f"If this is a chat request, model '{self._model}' is likely not pulled "
                f"(run: ollama pull {self._model}).",
                provider=self.name,
            )
        if response.status_code >= 400:
            raise StoryGenerationError(
                f"Ollama returned HTTP {response.status_code}: {response.text[:300]}",
                provider=self.name,
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise StoryGenerationError(
                f"Ollama returned a non-JSON body: {response.text[:200]}",
                provider=self.name,
            ) from exc

        if not isinstance(body, dict):
            raise StoryGenerationError(
                f"Ollama returned unexpected JSON of type {type(body).__name__}.",
                provider=self.name,
            )
        return body
