"""Domain and integration errors.

The API layer maps these to HTTP responses; nothing here imports FastAPI.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base class for expected, reportable failures."""


class NotFoundError(DomainError):
    def __init__(self, entity: str, entity_id: object) -> None:
        super().__init__(f"{entity} {entity_id} not found")
        self.entity = entity
        self.entity_id = entity_id


class ValidationError(DomainError):
    """Caller supplied semantically invalid input."""


class StoryGenerationError(DomainError):
    """The story provider could not produce a valid turn.

    Never raised to hide a configuration problem: the message always names the
    concrete cause so a misconfigured Ollama is visible rather than silently
    downgraded to the mock provider.
    """

    def __init__(self, message: str, *, provider: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable


class ImageGenerationError(DomainError):
    def __init__(self, message: str, *, provider: str) -> None:
        super().__init__(message)
        self.provider = provider
