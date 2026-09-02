"""Deterministic EpisodeAssembler routing (L0/L1 only, no LLM)."""

from __future__ import annotations

from typing import Mapping

from .contracts import CanonicalExperience
from .episode import (
    BoundaryAction,
    Episode,
    EpisodeBoundaryDecision,
)


def _reply_event_id(experience: CanonicalExperience) -> str | None:
    raw = experience.event.raw_metadata
    if isinstance(raw, Mapping):
        value = raw.get("reply_event_id")
        if isinstance(value, str) and value:
            return value
    return None


def _is_self_directed(experience: CanonicalExperience, self_entity: str) -> bool:
    event = experience.event
    if event.mode == "private":
        return True
    if event.reply_to is not None and event.reply_to.entity_id == self_entity:
        return True
    if any(entity.entity_id == self_entity for entity in event.mentioned_entities):
        return True
    return False


class EpisodeAssembler:
    """Deterministic boundary router.

    Priority:
    1. exact reply-to membership
    2. direct SELF interaction
    3. conservative participant + topic continuity (only when unambiguous)
    4. otherwise NO_EPISODE / NEW
    """

    owner = "Episode Assembler"

    def __init__(self, self_entity: str = "agent:xiaotianwen") -> None:
        self.self_entity = self_entity

    def decide(
        self,
        experience: CanonicalExperience,
        *,
        active_episodes: tuple[Episode, ...] = (),
        event_to_episode: Mapping[str, str] | None = None,
    ) -> EpisodeBoundaryDecision:
        event_to_episode = event_to_episode or {}
        reply_id = _reply_event_id(experience)
        if reply_id and reply_id in event_to_episode:
            return EpisodeBoundaryDecision(
                action=BoundaryAction.ATTACH,
                episode_id=event_to_episode[reply_id],
                reason="exact_reply_to_member_event",
                basis=("reply_event_id",),
            )

        if _is_self_directed(experience, self_entity=self.self_entity):
            return EpisodeBoundaryDecision(
                action=BoundaryAction.NEW,
                reason="self_directed_interaction",
                basis=("private", "mention_self", "reply_self") if experience.event.mode != "private" else ("private",),
            )

        # No topic-only fallback in P1b.  Without exact reply or direct SELF
        # interaction, an ambiguous group message is not force-attached.
        return EpisodeBoundaryDecision(
            action=BoundaryAction.NO_EPISODE,
            reason="ambient_or_unresolved_group_message",
            basis=(),
        )
