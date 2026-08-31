"""Ingress normalization, deduplication and no-dispatch debounce state."""

from .debounce import (
    ShadowIngestResult,
    ShadowTurnCoordinator,
    ShadowTurnSnapshot,
    TurnState,
)
from .deduplicate import EventDeduplicator, event_fingerprint, event_to_envelope
from .ownership import (
    CanaryPolicy,
    OrchestratorMode,
    OwnershipDecision,
    PrimaryReplyOwnership,
)

__all__ = [
    "EventDeduplicator",
    "CanaryPolicy",
    "OrchestratorMode",
    "OwnershipDecision",
    "PrimaryReplyOwnership",
    "ShadowIngestResult",
    "ShadowTurnCoordinator",
    "ShadowTurnSnapshot",
    "TurnState",
    "event_fingerprint",
    "event_to_envelope",
]
