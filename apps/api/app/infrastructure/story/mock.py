"""Deterministic story provider. Explicitly a mock -- it does no inference.

Its job is to make the whole game loop, the tests, and the E2E flow runnable with
no AI runtime installed. Output is derived from the context by simple rules, so
the same context always produces the same turn.

It honours the world's language so the Spanish path is exercised end to end
without needing a model.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.application.contracts import (
    DialogueLine,
    MemoryCandidate,
    RelationshipChange,
    TurnGeneration,
    VisualCue,
    WorldEvent,
)
from app.application.ports import ProviderStatus
from app.application.story_context import CharacterContext, StoryContext
from app.domain.enums import Language, MemoryKind

# Words hinting the player did something with lasting consequences.
_SIGNIFICANT = (
    "attack",
    "kill",
    "promise",
    "swear",
    "steal",
    "kiss",
    "flee",
    "betray",
    "atacar",
    "ataco",
    "matar",
    "prometo",
    "juro",
    "robar",
    "besar",
    "huir",
    "traicionar",
)
_FRIENDLY = (
    "thank",
    "help",
    "apolog",
    "gift",
    "protect",
    "save",
    "gracias",
    "ayud",
    "disculp",
    "regal",
    "proteg",
    "salv",
)
_HOSTILE = (
    "attack",
    "threaten",
    "insult",
    "steal",
    "betray",
    "atac",
    "amenaz",
    "insult",
    "rob",
    "traicion",
)


@dataclass(frozen=True, slots=True)
class _Phrases:
    acts: str
    hesitates: str
    settles: str
    line: str
    memory: str
    reaction: str
    ask: str
    look: str
    wait: str


_PHRASES: dict[Language, _Phrases] = {
    Language.EN: _Phrases(
        acts="acts: “{action}.”",
        hesitates="hesitates, saying nothing.",
        settles="The air of {location} shifts, and the moment settles into something new.",
        line="“So that is your choice.” {name} sounds {trait}. “Then we deal with what follows.”",
        memory="{player} did something consequential: {action}",
        reaction="Reacted to: {action}",
        ask="Ask {who} what they are not telling me.",
        look="Look around {location} for anything out of place.",
        wait="Wait, and let the silence do the work.",
    ),
    Language.ES: _Phrases(
        acts="actúa: «{action}».",
        hesitates="duda, sin decir nada.",
        settles="El aire de {location} cambia, y el momento se asienta en algo nuevo.",
        line=(
            "«Así que esa es tu decisión.» {name} suena {trait}. "
            "«Entonces afrontaremos lo que venga.»"
        ),
        memory="{player} hizo algo de consecuencias: {action}",
        reaction="Reaccionó a: {action}",
        ask="Preguntar a {who} qué es lo que no me está contando.",
        look="Mirar alrededor de {location} por si algo está fuera de lugar.",
        wait="Esperar, y dejar que el silencio haga el trabajo.",
    ),
}


class MockStoryGenerator:
    """Rule-based stand-in for a real model. Never claims to be one."""

    name = "mock"

    async def generate_turn(self, context: StoryContext) -> TurnGeneration:
        phrases = _PHRASES[context.world.language]
        action = context.player_action.strip()
        lowered = action.lower()
        speaker = self._pick_speaker(context)
        location = context.session.current_location or context.world.setting or context.world.name

        act = phrases.acts.format(action=action.rstrip(".!?")) if action else phrases.hesitates
        narration = f"{context.player.name} {act} " + phrases.settles.format(location=location)

        dialogue: list[DialogueLine] = []
        if speaker is not None:
            trait = speaker.personality.split(",")[0].strip() or speaker.name
            dialogue.append(
                DialogueLine(
                    character_id=speaker.id,
                    speaker=speaker.name,
                    text=phrases.line.format(name=speaker.name, trait=trait),
                )
            )

        significant = any(token in lowered for token in _SIGNIFICANT)

        memories: list[MemoryCandidate] = []
        if significant:
            memories.append(
                MemoryCandidate(
                    character_id=speaker.id if speaker else None,
                    kind=MemoryKind.EPISODIC,
                    summary=phrases.memory.format(player=context.player.name, action=action[:180]),
                    importance=3,
                )
            )

        changes: list[RelationshipChange] = []
        if speaker is not None:
            delta = self._sentiment(lowered)
            if delta != 0:
                changes.append(
                    RelationshipChange(
                        character_id=speaker.id,
                        trust_delta=delta,
                        affection_delta=delta,
                        reason=phrases.reaction.format(action=action[:110]),
                    )
                )

        events: list[WorldEvent] = []
        if significant:
            events.append(
                WorldEvent(
                    type="player_action",
                    description=f"{context.player.name}: {action[:200]}",
                )
            )

        who = speaker.name if speaker else context.world.name
        return TurnGeneration(
            narration=narration,
            dialogue=dialogue,
            suggested_actions=[
                phrases.ask.format(who=who),
                phrases.look.format(location=location),
                phrases.wait,
            ],
            memory_candidates=memories,
            relationship_changes=changes,
            world_events=events,
            visual_cue=VisualCue(generate=False, reason="Mock provider does not request images."),
        )

    async def status(self) -> ProviderStatus:
        return ProviderStatus(
            provider=self.name,
            state="ready",
            detail="Deterministic mock provider. No AI runtime is used.",
        )

    def _pick_speaker(self, context: StoryContext) -> CharacterContext | None:
        """Choose deterministically: a character named in the action, else rotate by turn."""
        if not context.relevant_characters:
            return None
        lowered = context.player_action.lower()
        for character in context.relevant_characters:
            if character.name.lower() in lowered:
                return character
        digest = hashlib.sha256(str(context.session.turn_index).encode()).hexdigest()
        return context.relevant_characters[int(digest, 16) % len(context.relevant_characters)]

    def _sentiment(self, lowered: str) -> int:
        if any(token in lowered for token in _HOSTILE):
            return -2
        if any(token in lowered for token in _FRIENDLY):
            return 2
        return 0
