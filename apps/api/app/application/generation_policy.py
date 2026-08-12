"""How much output each kind of model call is allowed to produce.

One place, so the numbers are a policy rather than a scattering of literals. An adapter
that carried `"num_predict": 256` inline would make the budget invisible from the layer
that owns it and unchangeable without a code edit, and the second provider would grow
its own copy of the same constant with a different value.

# Why a budget at all

Local generation is the slowest thing in a turn by an order of magnitude, and its cost
is close to linear in tokens produced. An unbudgeted call is therefore not "as long as
it needs" -- it is "as long as the model feels like", which on a 7B model asked for a
scene is occasionally four times the useful length and four times the wait. The budget
is a ceiling, not a target: the overwhelming majority of calls stop well below it, and
the ones that hit it are exactly the ones worth being told about.

# Why per purpose

Because the jobs are not the same shape. `OutcomeNarration` is one prose field and is
finished in a paragraph. `TurnGeneration` is a structured document -- narration, dialogue
lines, suggested actions, memory candidates, relationship changes, world events and three
kinds of proposal -- and the JSON syntax around all of that is itself tokens. Giving both
the same ceiling means one of them is either wasteful or broken.

The failure mode for the structured one deserves naming: output is schema-constrained
JSON, so a budget that runs out mid-document does not yield a short turn, it yields
invalid JSON and a failed turn. That makes the story budget a correctness setting, not
only a latency one, and it is why the default here is set from measured output sizes with
headroom rather than from a round number.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.application.llm_metrics import GenerationOptions, GenerationPurpose


class GenerationPolicy(BaseModel):
    """The resolved generation settings, per purpose.

    Built once from configuration and handed to the provider, which asks it for options
    and does not otherwise interpret it. Every purpose must be present: a missing entry
    would silently mean "no budget", and the whole point of this file is that no call
    ends up unbudgeted by omission.
    """

    model_config = ConfigDict(frozen=True)

    context_window: int = Field(gt=0)
    """Shared across purposes. It is a property of how the model is loaded, not of what
    it is being asked to do -- a second value would mean a second load."""

    temperature: float = Field(ge=0.0)

    max_output_tokens: dict[GenerationPurpose, int]

    @model_validator(mode="after")
    def _every_purpose_is_budgeted(self) -> GenerationPolicy:
        missing = sorted(
            purpose.value for purpose in GenerationPurpose if purpose not in self.max_output_tokens
        )
        if missing:
            raise ValueError(
                f"GenerationPolicy is missing an output budget for: {', '.join(missing)}. "
                "Adding a GenerationPurpose means deciding what it is allowed to produce."
            )
        for purpose, budget in self.max_output_tokens.items():
            if budget <= 0:
                raise ValueError(
                    f"Output budget for {purpose.value} must be positive, got {budget}. "
                    "There is no spelling of 'unlimited' here on purpose."
                )
        return self

    def options_for(self, purpose: GenerationPurpose) -> GenerationOptions:
        return GenerationOptions(
            max_output_tokens=self.max_output_tokens[purpose],
            context_window=self.context_window,
            temperature=self.temperature,
        )
