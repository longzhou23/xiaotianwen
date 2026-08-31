"""A deterministic three-second shadow debounce state machine.

The coordinator never creates an asyncio task, provider request or outbound
message. Calling code advances it through ``flush_ready`` during replay or an
explicit observation hook, so disabling it cannot leave background timers.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Iterable

from ..contracts import MediaRef, TurnEnvelope
from ..contracts.validation import ContractValidationError, JsonValue, require_finite_timestamp
from .cancellation import ShadowCancellation
from .deduplicate import EventDeduplicator, event_fingerprint, event_to_envelope


class TurnState(str, Enum):
    COLLECTING = "COLLECTING"
    READY = "READY"
    REQUESTING = "REQUESTING"
    TOOL_LOOP = "TOOL_LOOP"
    RESPONDING = "RESPONDING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class ShadowTurnSnapshot:
    """Immutable, safe-to-log view of a shadow turn."""

    turn: TurnEnvelope
    state: TurnState
    ready_at: float
    event_fingerprints: tuple[str, ...]
    transitions: tuple[str, ...]
    merge_count: int
    cancellation: ShadowCancellation | None = None

    def structural_summary(self) -> dict[str, JsonValue]:
        return {
            "turn": self.turn.structural_summary(),
            "state": self.state.value,
            "ready_at": self.ready_at,
            "event_count": len(self.event_fingerprints),
            "merge_count": self.merge_count,
            "transitions": list(self.transitions),
            "cancelled": self.cancellation is not None,
            "cancellation_reason": self.cancellation.reason if self.cancellation else None,
        }


@dataclass(frozen=True, slots=True)
class ShadowIngestResult:
    """Result of observing one incoming event; no response side effect occurs."""

    accepted: bool
    action: str
    request_id: str | None
    state: TurnState | None
    fingerprint: str
    snapshot: ShadowTurnSnapshot | None


@dataclass(slots=True)
class _MutableShadowTurn:
    turn: TurnEnvelope
    state: TurnState
    ready_at: float
    event_fingerprints: list[str]
    transitions: list[str] = field(default_factory=lambda: [TurnState.COLLECTING.value])
    merge_count: int = 0
    cancellation: ShadowCancellation | None = None

    def snapshot(self) -> ShadowTurnSnapshot:
        return ShadowTurnSnapshot(
            turn=self.turn,
            state=self.state,
            ready_at=self.ready_at,
            event_fingerprints=tuple(self.event_fingerprints),
            transitions=tuple(self.transitions),
            merge_count=self.merge_count,
            cancellation=self.cancellation,
        )

    def transition(self, state: TurnState) -> None:
        self.state = state
        self.transitions.append(state.value)


def _merge_media(existing: Iterable[MediaRef], incoming: Iterable[MediaRef]) -> tuple[MediaRef, ...]:
    result: list[MediaRef] = []
    seen: set[str] = set()
    for item in (*tuple(existing), *tuple(incoming)):
        if item.media_id in seen:
            continue
        seen.add(item.media_id)
        result.append(replace(item, order=len(result)))
    return tuple(result)


def _merged_text(existing: str, incoming: str) -> str:
    if not existing:
        return incoming
    if not incoming:
        return existing
    return f"{existing}\n{incoming}"


class ShadowTurnCoordinator:
    """Observe intended batching/cancellation without dispatching a reply.

    One active turn exists per session. A same-sender message within the quiet
    window merges into the pending turn; another sender or an already-ready
    turn produces a record-only cancellation and starts a replacement turn.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        quiet_window_seconds: float = 3.0,
        dedup_ttl_seconds: float = 30.0,
    ) -> None:
        if type(quiet_window_seconds) not in (int, float) or quiet_window_seconds <= 0:
            raise ContractValidationError("quiet_window_seconds must be a positive number")
        self.enabled = bool(enabled)
        self.quiet_window_seconds = float(quiet_window_seconds)
        self._deduplicator = EventDeduplicator(dedup_ttl_seconds)
        self._active_by_session: dict[str, _MutableShadowTurn] = {}
        self._terminal: list[ShadowTurnSnapshot] = []

    @property
    def active_timer_count(self) -> int:
        """Always zero: shadow timing is advanced by calls, never background tasks."""

        return 0

    @property
    def pending_count(self) -> int:
        return len(self._active_by_session)

    @property
    def dedup_cache_size(self) -> int:
        return self._deduplicator.size

    @property
    def terminal_turns(self) -> tuple[ShadowTurnSnapshot, ...]:
        return tuple(self._terminal)

    def _now(self, value: float | None) -> float:
        return time.time() if value is None else require_finite_timestamp(value, "now")

    def ingest_event(self, event: object, *, now: float | None = None) -> ShadowIngestResult:
        current = self._now(now)
        envelope = event_to_envelope(event, received_at=current)
        return self.ingest_envelope(
            envelope,
            fingerprint=event_fingerprint(event, received_at=current),
            now=current,
        )

    def ingest_envelope(
        self,
        envelope: TurnEnvelope,
        *,
        fingerprint: str | None = None,
        now: float | None = None,
    ) -> ShadowIngestResult:
        current = self._now(now)
        resolved_fingerprint = fingerprint or envelope.structural_fingerprint()
        if not self.enabled:
            return ShadowIngestResult(
                accepted=False,
                action="disabled",
                request_id=None,
                state=None,
                fingerprint=resolved_fingerprint,
                snapshot=None,
            )
        if self._deduplicator.seen_or_add(resolved_fingerprint, now=current):
            current_turn = self._active_by_session.get(envelope.session_id)
            return ShadowIngestResult(
                accepted=False,
                action="duplicate",
                request_id=current_turn.turn.request_id if current_turn else None,
                state=current_turn.state if current_turn else None,
                fingerprint=resolved_fingerprint,
                snapshot=current_turn.snapshot() if current_turn else None,
            )

        current_turn = self._active_by_session.get(envelope.session_id)
        if current_turn is not None and current_turn.state is TurnState.COLLECTING:
            if current_turn.turn.sender_id == envelope.sender_id and current <= current_turn.ready_at:
                current_turn.turn = replace(
                    current_turn.turn,
                    text=_merged_text(current_turn.turn.text, envelope.text),
                    media=_merge_media(current_turn.turn.media, envelope.media),
                    received_at=max(current_turn.turn.received_at, envelope.received_at),
                    metadata={
                        **dict(current_turn.turn.metadata),
                        "message_ids": [
                            *dict(current_turn.turn.metadata).get("message_ids", [
                                current_turn.turn.metadata.get("message_id", "")
                            ]),
                            envelope.metadata.get("message_id", ""),
                        ],
                        "event_count": len(current_turn.event_fingerprints) + 1,
                    },
                )
                current_turn.ready_at = current + self.quiet_window_seconds
                current_turn.event_fingerprints.append(resolved_fingerprint)
                current_turn.merge_count += 1
                current_turn.transitions.extend(["MERGED", TurnState.COLLECTING.value])
                snapshot = current_turn.snapshot()
                return ShadowIngestResult(
                    accepted=True,
                    action="merged",
                    request_id=current_turn.turn.request_id,
                    state=current_turn.state,
                    fingerprint=resolved_fingerprint,
                    snapshot=snapshot,
                )

        if current_turn is not None:
            current_turn.cancellation = ShadowCancellation(
                request_id=current_turn.turn.request_id,
                reason=(
                    "new_message_same_session"
                    if current_turn.turn.sender_id != envelope.sender_id
                    else "new_message_after_quiet_window"
                ),
                at=current,
            )
            current_turn.transition(TurnState.CANCELLED)
            self._terminal.append(current_turn.snapshot())

        started = replace(envelope, batch_started_at=current)
        mutable = _MutableShadowTurn(
            turn=started,
            state=TurnState.COLLECTING,
            ready_at=current + self.quiet_window_seconds,
            event_fingerprints=[resolved_fingerprint],
        )
        self._active_by_session[envelope.session_id] = mutable
        return ShadowIngestResult(
            accepted=True,
            action="created",
            request_id=started.request_id,
            state=mutable.state,
            fingerprint=resolved_fingerprint,
            snapshot=mutable.snapshot(),
        )

    def flush_ready(self, *, now: float | None = None) -> tuple[ShadowTurnSnapshot, ...]:
        """Move eligible turns to READY without constructing a ProviderRequest."""

        current = self._now(now)
        ready: list[ShadowTurnSnapshot] = []
        for turn in self._active_by_session.values():
            if turn.state is TurnState.COLLECTING and current >= turn.ready_at:
                turn.transition(TurnState.READY)
                ready.append(turn.snapshot())
        return tuple(ready)

    def mark_stage(self, request_id: str, state: TurnState) -> ShadowTurnSnapshot:
        """Replay-only state transition. It never cancels or invokes a live task."""

        allowed = {
            TurnState.READY: {TurnState.REQUESTING, TurnState.CANCELLED},
            TurnState.REQUESTING: {TurnState.TOOL_LOOP, TurnState.RESPONDING, TurnState.CANCELLED},
            TurnState.TOOL_LOOP: {TurnState.RESPONDING, TurnState.CANCELLED},
            TurnState.RESPONDING: {TurnState.COMPLETED, TurnState.CANCELLED},
        }
        for session_id, turn in list(self._active_by_session.items()):
            if turn.turn.request_id != request_id:
                continue
            if state not in allowed.get(turn.state, set()):
                raise ContractValidationError(
                    f"cannot transition {turn.state.value} to {state.value} in shadow mode"
                )
            turn.transition(state)
            snapshot = turn.snapshot()
            if state in {TurnState.COMPLETED, TurnState.CANCELLED}:
                self._terminal.append(snapshot)
                self._active_by_session.pop(session_id, None)
            return snapshot
        raise ContractValidationError("unknown shadow request_id")

    def disable(self) -> None:
        """Leave no timed work or retained dedup/cache state behind."""

        self.enabled = False
        self._active_by_session.clear()
        self._terminal.clear()
        self._deduplicator.clear()

    def enable(self) -> None:
        self.enabled = True
