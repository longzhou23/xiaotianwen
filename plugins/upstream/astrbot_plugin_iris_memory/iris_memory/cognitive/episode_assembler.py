"""Deterministic EpisodeAssembler routing (L0/L1 only, no LLM)."""

from __future__ import annotations

from typing import Mapping

from .contracts import CanonicalExperience
from .episode import (
    BoundaryAction,
    Episode,
    EpisodeBoundaryDecision,
    EpisodeState,
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

        # A private message without a reply is a continuation only when the
        # current scope has one unambiguous OPEN episode.  This deliberately
        # does not apply to a message carrying an unresolved reply id: reply
        # semantics remain higher-priority and must not be silently replaced
        # with generic continuation.  The observer supplies same-scope active
        # candidates, while this scope check keeps the assembler safe for
        # direct callers as well.
        if experience.event.mode == "private" and reply_id is None:
            candidates = tuple(
                episode
                for episode in active_episodes
                if episode.scope_id == experience.event.session_id
                and episode.state is EpisodeState.OPEN
            )
            if len(candidates) == 1:
                return EpisodeBoundaryDecision(
                    action=BoundaryAction.ATTACH,
                    episode_id=candidates[0].episode_id,
                    reason="private_continuation_unique_open",
                    basis=("private_unique_open",),
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
