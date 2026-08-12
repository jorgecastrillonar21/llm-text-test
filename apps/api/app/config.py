"""Application settings, loaded from environment / repository-root .env."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# apps/api/app/config.py -> repository root
REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "data"


class StoryProvider(StrEnum):
    MOCK = "mock"
    OLLAMA = "ollama"


class ImageProvider(StrEnum):
    DISABLED = "disabled"
    MOCK = "mock"
    COMFYUI = "comfyui"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    log_level: str = "INFO"

    database_url: str = ""

    story_provider: StoryProvider = StoryProvider.MOCK
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = ""
    ollama_timeout_seconds: float = 120.0
    ollama_temperature: float = 0.7

    # Must be set explicitly. Ollama defaults num_ctx to 4096 regardless of what the
    # model supports, and llama.cpp truncates from the *start* of the prompt -- the
    # system prompt and the world/character definitions -- without reporting it.
    # A full context at the current retrieval caps measures ~6.7k tokens, so 4096
    # silently discards roughly two thirds of it and every world reads the same.
    #
    # This is also the *context window* in the provider-neutral sense: it is the number
    # `prompt_context_utilization` is measured against. Raising it costs memory and, on
    # a machine where that pushes layers off the GPU, throughput -- so it is set here
    # rather than defaulted to whatever the model claims to support.
    ollama_num_ctx: int = Field(default=8192, ge=512)

    # How long Ollama keeps the model resident after a request. Empty means "send
    # nothing and let Ollama use its own default", which is 5 minutes at the time of
    # writing -- long enough for a turn, short enough that a player who steps away pays
    # the load cost again on their next action. `30m` suits an active playthrough; `-1`
    # keeps it resident indefinitely and is a real memory commitment, not a free speedup.
    # Watch `load_ms` in the metrics to see whether this is costing you anything.
    ollama_keep_alive: str = ""

    # Output budgets, in tokens. See app/application/generation_policy.py for why these
    # differ: a turn is a structured JSON document and a narration is one paragraph.
    story_max_output_tokens: int = Field(default=1024, gt=0)
    narration_max_output_tokens: int = Field(default=320, gt=0)

    # Streaming exists in the Ollama adapter and is off by default. Its only benefit in
    # this build is `time_to_first_token_ms`, because output is schema-constrained JSON
    # that nothing can usefully display half-decoded. Turning it on changes the transport
    # of the main path, so it stays opt-in until something consumes the chunks.
    ollama_streaming_enabled: bool = False

    # Above this, a generation is logged as slow. Diagnostic only: nothing in the game
    # behaves differently, and a slow call is still a completed call.
    llm_slow_call_threshold_ms: float = Field(default=30_000.0, gt=0)

    # How many recent generation records the in-process diagnostics buffer keeps. Small
    # on purpose -- this is a developer's recent history, not a metrics store.
    llm_metrics_buffer_size: int = Field(default=200, ge=1, le=5_000)

    image_provider: ImageProvider = ImageProvider.DISABLED
    comfyui_base_url: str = "http://127.0.0.1:8188"
    comfyui_workflow_path: str = ""
    comfyui_timeout_seconds: float = 120.0

    # NoDecode is required, not cosmetic: without it pydantic-settings treats any
    # complex-typed field as JSON and calls json.loads on the raw env value, which
    # fails before the validator below ever runs. CORS_ORIGINS is comma-separated
    # in .env because that is what a human writes.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("database_url", mode="before")
    @classmethod
    def _blank_to_default(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            return f"sqlite+aiosqlite:///{(DATA_DIR / 'ooc.db').as_posix()}"
        return value

    @property
    def dev_endpoints_enabled(self) -> bool:
        """Whether `/api/v1/dev/*` is mounted.

        An allowlist, not a "not production" check: a typo in APP_ENV should leave
        developer tooling switched off rather than quietly switch it on.
        """
        return self.app_env.strip().casefold() in {"development", "test"}

    @property
    def workflow_path(self) -> Path | None:
        if not self.comfyui_workflow_path.strip():
            return None
        candidate = Path(self.comfyui_workflow_path)
        return candidate if candidate.is_absolute() else REPO_ROOT / candidate

    def validate_runtime(self) -> None:
        """Fail fast on configuration that cannot possibly work.

        Only enabled integrations are validated: a disabled provider must never
        block startup.
        """
        if self.story_provider is StoryProvider.OLLAMA and not self.ollama_model.strip():
            raise ValueError(
                "STORY_PROVIDER=ollama requires OLLAMA_MODEL to be set "
                "(e.g. OLLAMA_MODEL=llama3.1:8b)."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
