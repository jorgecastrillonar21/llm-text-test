"""Settings loading, including the shape `.env.example` actually ships."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import REPO_ROOT, ImageProvider, Settings, StoryProvider


def _settings(**kwargs: object) -> Settings:
    """Build Settings ignoring the developer's real .env, which must not leak in."""
    return Settings(_env_file=None, **kwargs)  # type: ignore[arg-type]


def test_defaults_are_safe_without_any_configuration() -> None:
    settings = _settings()

    assert settings.story_provider is StoryProvider.MOCK
    assert settings.image_provider is ImageProvider.DISABLED
    assert settings.cors_origins == ["http://localhost:5173", "http://127.0.0.1:5173"]
    assert settings.database_url.startswith("sqlite+aiosqlite:///")


def test_cors_origins_parses_a_comma_separated_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: pydantic-settings JSON-decodes complex fields unless told not to.

    Without `NoDecode` this raised SettingsError at import time for every developer
    who had a real .env, while passing for everyone running on defaults.
    """
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173, http://192.168.1.50:5173 ,,")

    assert _settings().cors_origins == [
        "http://localhost:5173",
        "http://192.168.1.50:5173",
    ]


def test_the_shipped_env_example_loads(tmp_path: Path) -> None:
    """`cp .env.example .env` must produce a working configuration, not a crash."""
    env_file = tmp_path / ".env"
    env_file.write_text((REPO_ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")

    settings = Settings(_env_file=env_file)  # type: ignore[call-arg]

    assert settings.story_provider is StoryProvider.MOCK
    assert settings.image_provider is ImageProvider.DISABLED
    assert settings.cors_origins == ["http://localhost:5173", "http://127.0.0.1:5173"]
    # Blank DATABASE_URL must fall back to the default rather than staying empty.
    assert settings.database_url.endswith("data/ooc.db")
    assert settings.workflow_path is None


def test_ollama_without_a_model_fails_fast() -> None:
    settings = _settings(story_provider=StoryProvider.OLLAMA, ollama_model="  ")

    with pytest.raises(ValueError, match="OLLAMA_MODEL"):
        settings.validate_runtime()


def test_mock_provider_does_not_require_an_ollama_model() -> None:
    _settings(story_provider=StoryProvider.MOCK, ollama_model="").validate_runtime()


def test_relative_workflow_path_resolves_from_the_repository_root() -> None:
    settings = _settings(comfyui_workflow_path="ai/comfyui/workflows/x.api.json")

    assert settings.workflow_path == REPO_ROOT / "ai/comfyui/workflows/x.api.json"
