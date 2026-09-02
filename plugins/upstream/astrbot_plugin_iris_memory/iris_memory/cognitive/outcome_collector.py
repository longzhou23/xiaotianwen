"""Deterministic OutcomeCollector for explicit and structural observations.

No inferred sentiment and no reward/quality fields.
"""

from __future__ import annotations

from datetime import datetime

from .contracts import CanonicalExperience
from .episode import Episode
from .outcome import (
    OutcomeExplicitness,
    OutcomeKind,
    OutcomeObservation,
    make_outcome_observation_id,
)


class OutcomeCollector:
    owner = "Outcome Collector"

    _ACK_MARKERS = ("谢谢", "多谢", "感谢", "赞", "收到")
    _CORRECTION_MARKERS = ("不对", "错了", "不是", "说错", "搞错")
    _STOP_MARKERS = ("别说了", "闭嘴", "停一下", "不要再说了", "别讲了")
    _QUESTION_MARKERS = (
        "?", "？", "几点", "什么时候", "何时", "怎么", "为什么", "吗",
        "哪", "谁", "哪里", "在哪", "是否", "能不能", "多少",
    )

    def classify_experience(
        self,
        experience: CanonicalExperience,
        *,
        target_episode_id: str,
    ) -> tuple[OutcomeObservation, ...]:
        event = experience.event
        content = (event.content or "").strip()
        now = event.occurred_at
        observations: list[OutcomeObservation] = []

        if event.reply_to is not None:
            observations.append(self._build(
                target_episode_id,
                OutcomeKind.REPLY_OBSERVED,
                experience,
                OutcomeExplicitness.STRUCTURAL,
                now,
                ("reply_to",),
            ))
        if any(entity.entity_id == "agent:xiaotianwen" for entity in event.mentioned_entities):
            observations.append(self._build(
                target_episode_id,
                OutcomeKind.MENTION_OBSERVED,
                experience,
                OutcomeExplicitness.STRUCTURAL,
                now,
                ("mention_self",),
            ))

        if any(marker in content for marker in self._CORRECTION_MARKERS):
            observations.append(self._build(
                target_episode_id,
                OutcomeKind.EXPLICIT_CORRECTION,
                experience,
                OutcomeExplicitness.EXPLICIT,
                now,
                ("explicit_correction_marker",),
            ))
        elif any(marker in content for marker in self._STOP_MARKERS):
            observations.append(self._build(
                target_episode_id,
                OutcomeKind.EXPLICIT_STOP_REQUEST,
                experience,
                OutcomeExplicitness.EXPLICIT,
                now,
                ("explicit_stop_marker",),
            ))
        elif any(marker in content for marker in self._ACK_MARKERS):
            observations.append(self._build(
                target_episode_id,
                OutcomeKind.EXPLICIT_ACKNOWLEDGEMENT,
                experience,
                OutcomeExplicitness.EXPLICIT,
                now,
                ("explicit_ack_marker",),
            ))

        if any(marker in content for marker in self._QUESTION_MARKERS):
            observations.append(self._build(
                target_episode_id,
                OutcomeKind.FOLLOWUP_QUESTION,
                experience,
                OutcomeExplicitness.EXPLICIT,
                now,
                ("question_marker",),
            ))

        return tuple(observations)

    def observation_window_elapsed(
        self,
        *,
        target_episode_id: str,
        observed_at: datetime,
    ) -> OutcomeObservation:
        return OutcomeObservation(
            observation_id=make_outcome_observation_id(
                target_episode_id,
                OutcomeKind.OBSERVATION_WINDOW_ELAPSED,
            ),
            target_episode_id=target_episode_id,
            kind=OutcomeKind.OBSERVATION_WINDOW_ELAPSED,
            observed_at=observed_at,
            explicitness=OutcomeExplicitness.ABSENCE,
            evidence=("observation_policy_window_elapsed",),
            producer="deterministic_outcome_collector",
            provenance=("outcome_collector",),
        )

    def _build(
        self,
        target_episode_id: str,
        kind: OutcomeKind,
        experience: CanonicalExperience,
        explicitness: OutcomeExplicitness,
        observed_at: datetime,
        evidence: tuple[str, ...],
    ) -> OutcomeObservation:
        event = experience.event
        return OutcomeObservation(
            observation_id=make_outcome_observation_id(
                target_episode_id,
                kind,
                source_event_id=event.event_id,
            ),
            target_episode_id=target_episode_id,
            kind=kind,
            observed_at=observed_at,
            source_event_id=event.event_id,
            actor_entity=event.actor,
            explicitness=explicitness,
            evidence=evidence,
            producer="deterministic_outcome_collector",
            provenance=("outcome_collector",),
        )
