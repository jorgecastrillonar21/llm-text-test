"""Ollama story provider using the local /api/chat endpoint.

The model is constrained with the JSON Schema generated from TurnGeneration and
the reply is validated against the same model afterwards -- schema-constrained
decoding narrows the output but does not guarantee it.

This adapter never falls back to the mock provider. If STORY_PROVIDER=ollama is
set and Ollama is broken, the caller gets an error naming the cause; a silent
downgrade would hide a misconfiguration behind plausible prose.

# Generation options

Nothing here invents a number. `num_predict`, `num_ctx` and the temperature all arrive
as a resolved `GenerationOptions` from the application's `GenerationPolicy`, and this
file's only contribution is knowing that Ollama spells them that way. `keep_alive` is
the exception and is genuinely provider runtime configuration -- how long a model stays
resident is a property of the server, not of the generation -- so it is read from
settings and sent as-is.

# Metrics

Ollama reports what a request cost in the response body: `total_duration`,
`load_duration`, `prompt_eval_count`, `prompt_eval_duration`, `eval_count`,
`eval_duration`, `done`, `done_reason`, `model`. All of it is nanoseconds and provider
vocabulary, and none of it leaves this file in that form -- it is converted once, here,
into the `LlmGenerationMetrics` the rest of the application speaks. The application never
sees a raw Ollama response.

`prompt_eval_count` is the honest answer to "how big was the prompt". Everything else --
character counts, tokenizer estimates -- is a guess about a number the provider is
already telling us.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from pydantic import ValidationError as PydanticValidationError

from app.application.contracts import OutcomeNarration, TurnGeneration
from app.application.generation_policy import GenerationPolicy
from app.application.llm_metrics import (
    GenerationDoneReason,
    GenerationOptions,
    GenerationPurpose,
    LlmGenerationMetrics,
    ns_to_ms,
)
from app.application.ports import (
    OutcomeNarrationResult,
    ProviderStatus,
    StoryChunk,
    TurnGenerationResult,
)
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

_DONE_REASONS: dict[str, GenerationDoneReason] = {
    "stop": GenerationDoneReason.STOP,
    "length": GenerationDoneReason.LENGTH,
    "load": GenerationDoneReason.STOP,
    "unload": GenerationDoneReason.CONTEXT,
}
"""Ollama's `done_reason` strings, mapped onto the neutral vocabulary.

Anything not listed becomes `unknown` rather than `stop`. "The provider did not tell us
how this ended" and "the provider told us it ended cleanly" are different facts, and
collapsing the first into the second is how a truncated turn gets recorded as a whole
one.
"""


class OllamaStoryGenerator:
    name = "ollama"

    def __init__(
        self,
        settings: Settings,
        policy: GenerationPolicy,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = settings.ollama_base_url.rstrip("/")
        self._model = settings.ollama_model
        self._timeout = settings.ollama_timeout_seconds
        self._policy = policy
        self._keep_alive = settings.ollama_keep_alive.strip()
        self._streaming = settings.ollama_streaming_enabled
        self._client = client

    async def generate_turn(self, context: StoryContext) -> TurnGenerationResult:
        parsed, metrics = await self._chat(
            prompt_name="story_director",
            rendered=render_context(context),
            schema=TurnGeneration.model_json_schema(),
            purpose=GenerationPurpose.STORY_TURN,
            session_id=context.session.id,
        )
        try:
            generation = TurnGeneration.model_validate(parsed)
        except PydanticValidationError as exc:
            raise self._contract_error("TurnGeneration", exc, metrics) from exc
        return TurnGenerationResult(generation=generation, metrics=metrics)

    async def narrate_outcome(self, context: OutcomeContext) -> OutcomeNarrationResult:
        """Describe an already-committed outcome.

        Same transport, different prompt and a far narrower schema. The narrowness is
        the safety property: `OutcomeNarration` has one string field, so a model that
        wanted to revise the outcome has nowhere to write it.

        A different purpose too, and therefore a different output budget: this produces
        a paragraph, and a budget sized for a whole turn document would be four times
        what it can use.
        """
        parsed, metrics = await self._chat(
            prompt_name="outcome_narrator",
            rendered=render_outcome(context),
            schema=OutcomeNarration.model_json_schema(),
            purpose=GenerationPurpose.OUTCOME_NARRATION,
            session_id=None,
        )
        try:
            narration = OutcomeNarration.model_validate(parsed)
        except PydanticValidationError as exc:
            raise self._contract_error("OutcomeNarration", exc, metrics) from exc
        return OutcomeNarrationResult(narration=narration, metrics=metrics)

    async def stream_turn(self, context: StoryContext) -> AsyncIterator[StoryChunk]:
        """Yield a turn as Ollama produces it, ending with a chunk carrying metrics.

        Real, and unused by the HTTP API. What it buys is `time_to_first_token_ms`,
        which no amount of arithmetic on a completed request can recover -- and the
        transport a future streaming endpoint would need, already written and already
        tested, rather than a comment promising one.

        The text it yields is schema-constrained JSON, not prose, so a consumer cannot
        simply print it. That is a property of the current contract rather than of
        streaming, and it is the honest reason the turn endpoint does not consume this
        yet: making a partial turn *displayable* means deciding what a half-formed
        proposal means, which is transport work for a later epic.
        """
        payload = self._payload(
            prompt_name="story_director",
            rendered=render_context(context),
            schema=TurnGeneration.model_json_schema(),
            purpose=GenerationPurpose.STORY_TURN,
            stream=True,
        )
        options = self._policy.options_for(GenerationPurpose.STORY_TURN)
        started = time.perf_counter()
        first_token_at: float | None = None
        final: dict[str, Any] = {}

        async for frame in self._stream("/api/chat", payload, started):
            text = self._content_of(frame)
            if text and first_token_at is None:
                first_token_at = time.perf_counter()
            if frame.get("done"):
                final = frame
                break
            if text:
                yield StoryChunk(text=text)

        yield StoryChunk(
            done=True,
            metrics=self._metrics_from(
                final,
                purpose=GenerationPurpose.STORY_TURN,
                options=options,
                session_id=context.session.id,
                client_elapsed_ms=_ms_since(started),
                time_to_first_token_ms=(
                    None if first_token_at is None else _ms_between(started, first_token_at)
                ),
            ),
        )

    def supports_streaming(self) -> bool:
        """Whether this instance will actually stream. Configuration, not capability."""
        return self._streaming

    async def _chat(
        self,
        *,
        prompt_name: str,
        rendered: str,
        schema: dict[str, Any],
        purpose: GenerationPurpose,
        session_id: uuid.UUID | None,
    ) -> tuple[object, LlmGenerationMetrics]:
        """One schema-constrained chat request, decoded to plain JSON, plus its cost.

        Validation against the contract stays with the caller: only it knows which
        contract the reply was supposed to satisfy, and an error message that cannot
        name it is one nobody can act on.
        """
        options = self._policy.options_for(purpose)
        payload = self._payload(
            prompt_name=prompt_name,
            rendered=rendered,
            schema=schema,
            purpose=purpose,
            stream=self._streaming,
        )
        prompt_chars = len(str(payload["messages"][0]["content"])) + len(rendered)

        started = time.perf_counter()
        if self._streaming:
            content, data, first_token_at = await self._collect_stream(
                "/api/chat", payload, started
            )
        else:
            data = await self._post("/api/chat", payload, started=started)
            content = self._content_of(data)
            first_token_at = None

        metrics = self._metrics_from(
            data,
            purpose=purpose,
            options=options,
            session_id=session_id,
            client_elapsed_ms=_ms_since(started),
            time_to_first_token_ms=(
                None if first_token_at is None else _ms_between(started, first_token_at)
            ),
        )
        self._warn_if_prompt_was_truncated(prompt_chars, metrics)

        if not content.strip():
            raise StoryGenerationError(
                "Ollama returned an empty message. The model may not support structured output.",
                provider=self.name,
                retryable=True,
                model=self._model,
                error_code="empty_message",
                elapsed_ms=metrics.client_elapsed_ms,
            )

        try:
            return json.loads(content), metrics
        except json.JSONDecodeError as exc:
            # Truncated JSON and malformed JSON arrive as the same exception, and the
            # provider already told us which one this was.
            raise StoryGenerationError(
                f"Ollama returned content that is not valid JSON: {exc}."
                f"{self._budget_hint(metrics)}",
                provider=self.name,
                retryable=True,
                model=self._model,
                error_code=(
                    "budget_exhausted" if metrics.output_budget_reached else "invalid_json"
                ),
                elapsed_ms=metrics.client_elapsed_ms,
            ) from exc

    def _payload(
        self,
        *,
        prompt_name: str,
        rendered: str,
        schema: dict[str, Any],
        purpose: GenerationPurpose,
        stream: bool,
    ) -> dict[str, Any]:
        """The request body, with every generation option resolved from policy.

        No literal appears here that the application did not decide. That is the whole
        reason `GenerationPolicy` exists: a budget written into this file would be a
        budget nobody outside this file could see or change.
        """
        prompt = load_prompt(prompt_name)
        options = self._policy.options_for(purpose)
        payload: dict[str, Any] = {
            "model": self._model,
            "stream": stream,
            "format": schema,
            "options": {
                "temperature": options.temperature,
                # num_ctx is not optional: Ollama's 4096 default truncates the head of
                # the prompt -- system rules, world, characters -- and says nothing.
                "num_ctx": options.context_window,
                # The output ceiling. Without it a local model is effectively unlimited,
                # which is not a decision anybody made -- it is one nobody made.
                "num_predict": options.max_output_tokens,
            },
            "messages": [
                {"role": "system", "content": prompt.body},
                {"role": "user", "content": rendered},
            ],
        }
        if self._keep_alive:
            # Omitted rather than defaulted, so an unset OLLAMA_KEEP_ALIVE means
            # "Ollama's own default" instead of this file's opinion of one.
            payload["keep_alive"] = self._keep_alive
        return payload

    def _metrics_from(
        self,
        data: dict[str, Any],
        *,
        purpose: GenerationPurpose,
        options: GenerationOptions,
        session_id: uuid.UUID | None,
        client_elapsed_ms: float,
        time_to_first_token_ms: float | None,
    ) -> LlmGenerationMetrics:
        """Ollama's response fields as an application metrics record.

        Every read is defensive. This is a decoded JSON body from another process, and a
        metrics helper that raises on an unexpected shape would convert a successful
        generation into a failed turn -- which is precisely the inversion this whole
        module is supposed to prevent.
        """
        reported_model = data.get("model")
        return LlmGenerationMetrics(
            provider=self.name,
            model=reported_model if isinstance(reported_model, str) else self._model,
            purpose=purpose,
            session_id=session_id,
            prompt_tokens=_count(data.get("prompt_eval_count")),
            configured_context_window=options.context_window,
            generated_tokens=_count(data.get("eval_count")),
            configured_max_output_tokens=options.max_output_tokens,
            done_reason=_done_reason(data),
            total_ms=ns_to_ms(data.get("total_duration")),
            load_ms=ns_to_ms(data.get("load_duration")),
            prompt_eval_ms=ns_to_ms(data.get("prompt_eval_duration")),
            generation_ms=ns_to_ms(data.get("eval_duration")),
            time_to_first_token_ms=time_to_first_token_ms,
            client_elapsed_ms=client_elapsed_ms,
        )

    def _content_of(self, data: dict[str, Any]) -> str:
        message = data.get("message")
        if not isinstance(message, dict):
            return ""
        content = message.get("content")
        return content if isinstance(content, str) else ""

    def _contract_error(
        self, contract: str, exc: PydanticValidationError, metrics: LlmGenerationMetrics
    ) -> StoryGenerationError:
        """A schema violation, with the budget named when the budget is the likely cause.

        Structured output that ran out of tokens does not arrive short -- it arrives
        broken, because the closing braces never came. Reporting that as "the model does
        not follow schemas" sends the reader to the wrong problem entirely, and the
        provider already told us which one it was.
        """
        detail = (
            f"Ollama response did not match the {contract} contract: "
            f"{exc.error_count()} validation error(s). First: {exc.errors()[0]['msg']}"
        )
        return StoryGenerationError(
            f"{detail}{self._budget_hint(metrics)}",
            provider=self.name,
            retryable=True,
            model=self._model,
            error_code="budget_exhausted" if metrics.output_budget_reached else "contract_mismatch",
            elapsed_ms=metrics.client_elapsed_ms,
        )

    def _budget_hint(self, metrics: LlmGenerationMetrics) -> str:
        if not metrics.output_budget_reached:
            return ""
        return (
            f" The output stopped at the {metrics.configured_max_output_tokens}-token budget "
            f"(done_reason={metrics.done_reason.value}), so the JSON is truncated rather than "
            f"wrong. Raise STORY_MAX_OUTPUT_TOKENS."
        )

    def _warn_if_prompt_was_truncated(
        self, prompt_chars: int, metrics: LlmGenerationMetrics
    ) -> None:
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
        evaluated = metrics.prompt_tokens
        if evaluated is None or evaluated <= 0:
            return
        if evaluated < prompt_chars // _MIN_CHARS_PER_TOKEN:
            logger.warning(
                "Ollama evaluated only %d prompt tokens for a %d-character prompt: part "
                "of it was discarded, starting with the system rules and the world and "
                "character definitions. OLLAMA_NUM_CTX is %d -- raise it, or lower the "
                "retrieval limits in context_builder.py.",
                evaluated,
                prompt_chars,
                metrics.configured_context_window,
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
            extra={
                "base_url": self._base_url,
                "context_window": str(self._policy.context_window),
                "keep_alive": self._keep_alive or "ollama default",
                "streaming": "on" if self._streaming else "off",
            },
        )

    def _model_installed(self, installed: list[str]) -> bool:
        # Ollama reports "llama3.1:8b"; users often configure the bare "llama3.1".
        wanted = self._model if ":" in self._model else f"{self._model}:latest"
        return wanted in installed or self._model in installed

    async def _get(self, path: str) -> dict[str, Any]:
        return await self._request("GET", path, None, started=time.perf_counter())

    async def _post(
        self, path: str, payload: dict[str, Any], *, started: float | None = None
    ) -> dict[str, Any]:
        return await self._request(
            "POST", path, payload, started=time.perf_counter() if started is None else started
        )

    @asynccontextmanager
    async def _http(self) -> AsyncIterator[httpx.AsyncClient]:
        """This request's client: the injected one, or a short-lived one.

        A context manager so both branches close correctly and neither closes a client
        it did not create -- an injected client belongs to whoever injected it.
        """
        if self._client is not None:
            yield self._client
        else:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                yield client

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None, *, started: float
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        try:
            async with self._http() as client:
                response = await client.request(method, url, json=payload)
        except httpx.ConnectError as exc:
            raise self._transport_error(
                f"Cannot reach Ollama at {self._base_url}. Is `ollama serve` running?",
                code="connect_error",
                started=started,
            ) from exc
        except httpx.TimeoutException as exc:
            raise self._transport_error(
                f"Ollama timed out after {self._timeout:.0f}s. "
                f"Raise OLLAMA_TIMEOUT_SECONDS or use a smaller model.",
                code="timeout",
                started=started,
            ) from exc

        self._raise_for_status(response.status_code, response.text, path, started)

        try:
            body = response.json()
        except ValueError as exc:
            raise self._transport_error(
                f"Ollama returned a non-JSON body: {response.text[:200]}",
                code="invalid_json",
                started=started,
                retryable=False,
            ) from exc

        if not isinstance(body, dict):
            raise self._transport_error(
                f"Ollama returned unexpected JSON of type {type(body).__name__}.",
                code="invalid_json",
                started=started,
                retryable=False,
            )
        return body

    async def _stream(
        self, path: str, payload: dict[str, Any], started: float
    ) -> AsyncIterator[dict[str, Any]]:
        """Ollama's newline-delimited JSON frames, decoded.

        Malformed lines are skipped rather than fatal: the stream is a sequence of
        independent frames, and one unparseable line is a lost fragment, not a lost
        generation. A stream that produced *nothing* usable is caught by the caller,
        which sees empty content and says so.
        """
        url = f"{self._base_url}{path}"
        try:
            async with (
                self._http() as client,
                client.stream("POST", url, json=payload) as response,
            ):
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")
                    self._raise_for_status(response.status_code, body, path, started)
                async for line in response.aiter_lines():
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        frame = json.loads(stripped)
                    except json.JSONDecodeError:
                        logger.debug("Skipping unparseable stream frame from Ollama.")
                        continue
                    if isinstance(frame, dict):
                        yield frame
        except httpx.ConnectError as exc:
            raise self._transport_error(
                f"Cannot reach Ollama at {self._base_url}. Is `ollama serve` running?",
                code="connect_error",
                started=started,
            ) from exc
        except httpx.TimeoutException as exc:
            raise self._transport_error(
                f"Ollama timed out after {self._timeout:.0f}s while streaming. "
                f"Raise OLLAMA_TIMEOUT_SECONDS or use a smaller model.",
                code="timeout",
                started=started,
            ) from exc

    async def _collect_stream(
        self, path: str, payload: dict[str, Any], started: float
    ) -> tuple[str, dict[str, Any], float | None]:
        """Drain a stream into the same shape the non-streaming path produces.

        This is how streaming earns its place without changing anything above it: the
        caller gets one complete document and one metrics record, exactly as before, and
        the only difference is that `time_to_first_token_ms` is now a measurement instead
        of a None.
        """
        pieces: list[str] = []
        final: dict[str, Any] = {}
        first_token_at: float | None = None

        async for frame in self._stream(path, payload, started):
            text = self._content_of(frame)
            if text and first_token_at is None:
                first_token_at = time.perf_counter()
            pieces.append(text)
            if frame.get("done"):
                final = frame
                break

        return "".join(pieces), final, first_token_at

    def _raise_for_status(self, code: int, body: str, path: str, started: float) -> None:
        if code == 404:
            raise self._transport_error(
                f"Ollama returned 404 for {path}. "
                f"If this is a chat request, model '{self._model}' is likely not pulled "
                f"(run: ollama pull {self._model}).",
                code="http_404",
                started=started,
                retryable=False,
            )
        if code >= 400:
            raise self._transport_error(
                f"Ollama returned HTTP {code}: {body[:300]}",
                code=f"http_{code}",
                started=started,
                retryable=False,
            )

    def _transport_error(
        self, message: str, *, code: str, started: float, retryable: bool = True
    ) -> StoryGenerationError:
        """Every failure path, carrying what a performance record needs.

        The elapsed time is the useful half: a connection refused in two milliseconds and
        a timeout after ninety seconds are the same exception type and completely
        different operational problems.
        """
        return StoryGenerationError(
            message,
            provider=self.name,
            retryable=retryable,
            model=self._model,
            error_code=code,
            elapsed_ms=_ms_since(started),
        )


def _count(value: object) -> int | None:
    """A token count from the response, or None. Never a fabricated zero."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _done_reason(data: dict[str, Any]) -> GenerationDoneReason:
    reported = data.get("done_reason")
    if isinstance(reported, str):
        return _DONE_REASONS.get(reported.strip().lower(), GenerationDoneReason.UNKNOWN)
    return GenerationDoneReason.UNKNOWN


def _ms_since(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


def _ms_between(started: float, ended: float) -> float:
    return round((ended - started) * 1000, 3)
