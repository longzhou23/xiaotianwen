"""Outcome observation contracts. Outcomes observe facts; they never assign quality."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import hashlib

from .contracts import EntityReference, canonical_tuple


class OutcomeKind(str, Enum):
    EXPLICIT_ACKNOWLEDGEMENT = "EXPLICIT_ACKNOWLEDGEMENT"
    EXPLICIT_CORRECTION = "EXPLICIT_CORRECTION"
    EXPLICIT_STOP_REQUEST = "EXPLICIT_STOP_REQUEST"
    FOLLOWUP_QUESTION = "FOLLOWUP_QUESTION"
    ANSWER_OBSERVED = "ANSWER_OBSERVED"
    REACTION_OBSERVED = "REACTION_OBSERVED"

    REPLY_OBSERVED = "REPLY_OBSERVED"
    MENTION_OBSERVED = "MENTION_OBSERVED"
    CONVERSATION_CONTINUED = "CONVERSATION_CONTINUED"

    TOOL_RESULT_RECEIVED = "TOOL_RESULT_RECEIVED"
    TOOL_SUCCEEDED = "TOOL_SUCCEEDED"
    TOOL_FAILED = "TOOL_FAILED"

    DISPATCH_OBSERVED = "DISPATCH_OBSERVED"
    DELIVERY_FAILED = "DELIVERY_FAILED"

    OBSERVATION_WINDOW_ELAPSED = "OBSERVATION_WINDOW_ELAPSED"


class OutcomeExplicitness(str, Enum):
    EXPLICIT = "EXPLICIT"
    STRUCTURAL = "STRUCTURAL"
    ABSENCE = "ABSENCE"


def make_outcome_observation_id(
    target_episode_id: str,
    kind: OutcomeKind,
    *,
    source_event_id: str | None = None,
    source_ref_id: str | None = None,
) -> str:
    stable = f"{target_episode_id}:{kind.value}:{source_event_id or ''}:{source_ref_id or ''}"
    return f"outcome:{hashlib.sha1(stable.encode('utf-8')).hexdigest()}"


@dataclass(frozen=True, slots=True)
class OutcomeObservation:
    observation_id: str
    target_episode_id: str
    kind: OutcomeKind
    observed_at: datetime
    source_event_id: str | None = None
    source_ref_id: str | None = None
    actor_entity: EntityReference | None = None
    target_entity: EntityReference | None = None
    explicitness: OutcomeExplicitness = OutcomeExplicitness.STRUCTURAL
    confidence: float = 1.0
    evidence: tuple[str, ...] = ()
    producer: str = "deterministic_outcome_collector"
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.observation_id or not self.target_episode_id:
            raise ValueError("observation_id and target_episode_id are required")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")
        object.__setattr__(self, "evidence", canonical_tuple(self.evidence))
        object.__setattr__(self, "provenance", canonical_tuple(self.provenance))

    @property
    def dedupe_key(self) -> str:
        return (
            f"{self.target_episode_id}:{self.kind.value}:"
            f"{self.source_event_id or ''}:{self.source_ref_id or ''}"
        )
