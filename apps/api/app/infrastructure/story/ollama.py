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

from app.application.contracts import TurnGeneration
from app.application.ports import ProviderStatus
from app.application.story_context import StoryContext
from app.config import Settings
from app.domain.errors import StoryGenerationError
from app.infrastructure.prompts import load_prompt
from app.infrastructure.story.rendering import render_context

logger = logging.getLogger(__name__)


class OllamaStoryGenerator:
    name = "ollama"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._base_url = settings.ollama_base_url.rstrip("/")
        self._model = settings.ollama_model
        self._timeout = settings.ollama_timeout_seconds
        self._temperature = settings.ollama_temperature
        self._client = client

    async def generate_turn(self, context: StoryContext) -> TurnGeneration:
        prompt = load_prompt("story_director")
        payload = {
            "model": self._model,
            "stream": False,
            "format": TurnGeneration.model_json_schema(),
            "options": {"temperature": self._temperature},
            "messages": [
                {"role": "system", "content": prompt.body},
                {"role": "user", "content": render_context(context)},
            ],
        }

        data = await self._post("/api/chat", payload)

        content = data.get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise StoryGenerationError(
                "Ollama returned an empty message. The model may not support structured output.",
                provider=self.name,
                retryable=True,
            )

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise StoryGenerationError(
                f"Ollama returned content that is not valid JSON: {exc}. "
                f"Model '{self._model}' may be too small to follow the schema reliably.",
                provider=self.name,
                retryable=True,
            ) from exc

        try:
            return TurnGeneration.model_validate(parsed)
        except PydanticValidationError as exc:
            raise StoryGenerationError(
                f"Ollama response did not match the TurnGeneration contract: "
                f"{exc.error_count()} validation error(s). First: {exc.errors()[0]['msg']}",
                provider=self.name,
                retryable=True,
            ) from exc

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
