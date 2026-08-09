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


class InvalidWorldRulesError(ValidationError):
    """A WorldRules document could not be read.

    A subclass of ValidationError so the existing 422 mapping applies, but distinct
    so a caller can tell "your rules are malformed" from any other bad input.
    """


class UnsupportedRulesVersionError(InvalidWorldRulesError):
    """The document declares a version this build does not know how to read.

    Deliberately fatal rather than best-effort: guessing at an unknown schema would
    quietly hand a language model rules nobody wrote.
    """

    def __init__(self, version: object, supported: tuple[int, ...]) -> None:
        listed = ", ".join(str(v) for v in supported)
        super().__init__(f"Unsupported WorldRules version {version!r}; this build reads {listed}.")
        self.version = version
        self.supported = supported


class TimeProgressionError(ValidationError):
    """Something asked to advance the clock in a world whose rules do not allow it.

    Distinct from a malformed request: the request was well-formed and the world said
    no. A paused world stays paused even when the caller is confident.
    """


class FactPolicyError(ValidationError):
    """Someone tried to write a fact their authority does not reach.

    The request was well formed and the property was real -- the asker simply may not
    establish it. Kept distinct from a malformed mutation so a caller can tell "I sent
    nonsense" from "the Story Director does not get to decide who is alive".
    """


class IncompatibleFactError(ValidationError):
    """The world's own rules forbid the truth this mutation would establish.

    Resurrection in a world where death is permanent, something supernatural in a
    world with no supernatural. Not a policy problem: nobody is authorised for this,
    because the universe does not work that way.
    """


class StaleStateError(DomainError):
    """A mutation batch was decided against a state revision that has since moved.

    Not a `ValidationError`: nothing about the request is wrong, it merely arrived
    late. The API maps it to 409 rather than 422, because the useful response is
    "re-read and try again" rather than "fix your input".
    """

    def __init__(self, *, expected: int, actual: int) -> None:
        super().__init__(
            f"Session state has moved on: the batch expected revision {expected}, "
            f"but the session is at {actual}."
        )
        self.expected = expected
        self.actual = actual


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
