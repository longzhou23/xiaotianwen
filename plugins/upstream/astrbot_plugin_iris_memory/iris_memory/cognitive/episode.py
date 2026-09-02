"""Minimal Episode contracts and deterministic boundary decision types.

Episode organizes facts; it never judges them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from .contracts import EntityReference, canonical_tuple


class EpisodeState(str, Enum):
    OPEN = "OPEN"
    SOFT_CLOSED = "SOFT_CLOSED"
    FINALIZED = "FINALIZED"
    INTERRUPTED = "INTERRUPTED"


class BoundaryAction(str, Enum):
    ATTACH = "ATTACH"
    NEW = "NEW"
    LATE_OBSERVATION = "LATE_OBSERVATION"
    NO_EPISODE = "NO_EPISODE"


class EpisodeEventKind(str, Enum):
    EXPERIENCE = "EXPERIENCE"
    COGNITIVE_PROPOSAL = "COGNITIVE_PROPOSAL"
    HOST_OUTPUT = "HOST_OUTPUT"
    DISPATCH = "DISPATCH"
    DELIVERY = "DELIVERY"
    TOOL_RESULT = "TOOL_RESULT"
    INTENTIONAL_SILENCE = "INTENTIONAL_SILENCE"
    NO_INTENT = "NO_INTENT"
    TRIGGER_NO = "TRIGGER_NO"
    GUARD_BLOCKED = "GUARD_BLOCKED"


def make_episode_id(scope_id: str, root_event_id: str) -> str:
    return f"episode:{scope_id}:{root_event_id}"


def make_episode_event_ref_id(
    kind: EpisodeEventKind,
    *,
    source_event_id: str | None = None,
    trace_id: str | None = None,
    execution_record_id: str | None = None,
) -> str:
    stable = source_event_id or trace_id or execution_record_id
    if not stable:
        raise ValueError("EpisodeEventRef needs a stable source identity")
    parts = [stable]
    if trace_id and trace_id != stable:
        parts.append(trace_id)
    if execution_record_id and execution_record_id != stable:
        parts.append(execution_record_id)
    return f"{kind.value}:{':'.join(parts)}"


@dataclass(frozen=True, slots=True)
class EpisodeEventRef:
    ref_id: str
    kind: EpisodeEventKind
    source_event_id: str | None = None
    trace_id: str | None = None
    execution_record_id: str | None = None
    observed_at: datetime | None = None
    actor_entity: EntityReference | None = None

    def __post_init__(self) -> None:
        if not self.ref_id:
            raise ValueError("EpisodeEventRef ref_id is required")
        if self.observed_at is None:
            object.__setattr__(self, "observed_at", datetime.now().astimezone())
        if self.source_event_id is None and self.trace_id is None and self.execution_record_id is None:
            raise ValueError("EpisodeEventRef requires at least one stable source identity")


@dataclass(frozen=True, slots=True)
class Episode:
    episode_id: str
    scope_id: str
    state: EpisodeState
    root_event_id: str
    opened_at: datetime
    last_activity_at: datetime
    participants: tuple[EntityReference, ...] = ()
    topic_hint: str | None = None
    event_refs: tuple[EpisodeEventRef, ...] = ()
    unresolved_refs: tuple[str, ...] = ()
    soft_closed_at: datetime | None = None
    finalized_at: datetime | None = None
    revision: int = 0
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.episode_id or not self.scope_id or not self.root_event_id:
            raise ValueError("episode_id, scope_id, root_event_id are required")
        if self.revision < 0:
            raise ValueError("revision cannot be negative")
        object.__setattr__(self, "participants", canonical_tuple(self.participants))
        object.__setattr__(self, "event_refs", tuple(self.event_refs))
        object.__setattr__(self, "unresolved_refs", canonical_tuple(self.unresolved_refs))
        object.__setattr__(self, "provenance", canonical_tuple(self.provenance))


@dataclass(frozen=True, slots=True)
class EpisodeBoundaryDecision:
    action: BoundaryAction
    reason: str
    basis: tuple[str, ...] = ()
    episode_id: str | None = None
    confidence: float = 1.0
    producer: str = "deterministic_episode_assembler"

    def __post_init__(self) -> None:
        if not self.reason:
            raise ValueError("boundary reason is required")
        object.__setattr__(self, "basis", canonical_tuple(self.basis))
        if self.action in (BoundaryAction.ATTACH, BoundaryAction.LATE_OBSERVATION) and not self.episode_id:
            raise ValueError("ATTACH/LATE_OBSERVATION require episode_id")
        if self.action is BoundaryAction.NEW and self.episode_id is not None:
            raise ValueError("NEW decision should not carry a pre-chosen episode_id")
