"""Versioned, bounded and content-safe observations for offline replay runs."""

from __future__ import annotations

import hashlib
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .redact import redact_value


TRACE_SCHEMA_VERSION = 1


def fingerprint_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()


@dataclass(frozen=True, slots=True)
class TraceEvent:
    """One chronological trace event with only redacted/structural payload."""

    sequence: int
    at: float
    kind: str
    source: str
    run_id: str
    payload: dict[str, Any]
    session_id: str | None = None
    event_id: str | None = None
    turn_id: str | None = None
    request_id: str | None = None
    parent_request_id: str | None = None
    call_id: str | None = None
    delivery_id: str | None = None
    capture_mode: str = "COMPLETE"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": TRACE_SCHEMA_VERSION,
            "sequence": self.sequence,
            "at": self.at,
            "kind": self.kind,
            "source": self.source,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "event_id": self.event_id,
            "turn_id": self.turn_id,
            "request_id": self.request_id,
            "parent_request_id": self.parent_request_id,
            "call_id": self.call_id,
            "delivery_id": self.delivery_id,
            "capture_mode": self.capture_mode,
            "payload": redact_value(self.payload),
        }


@dataclass(slots=True)
class TraceStore:
    """In-memory retention with size and optional virtual-time bounds."""

    run_id: str
    max_events: int = 1_000
    retention_seconds: float | None = None
    _events: deque[TraceEvent] = field(default_factory=deque, init=False, repr=False)
    _next_sequence: int = field(default=1, init=False, repr=False)
    dropped_count: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise ValueError("run_id must be non-empty")
        if type(self.max_events) is not int or self.max_events <= 0:
            raise ValueError("max_events must be positive")
        if self.retention_seconds is not None and (
            isinstance(self.retention_seconds, bool)
            or not isinstance(self.retention_seconds, (int, float))
            or not math.isfinite(float(self.retention_seconds))
            or self.retention_seconds <= 0
        ):
            raise ValueError("retention_seconds must be a positive finite number")

    def emit(
        self,
        kind: str,
        *,
        at: float,
        source: str,
        payload: dict[str, Any] | None = None,
        session_id: str | None = None,
        event_id: str | None = None,
        turn_id: str | None = None,
        request_id: str | None = None,
        parent_request_id: str | None = None,
        call_id: str | None = None,
        delivery_id: str | None = None,
        capture_mode: str = "COMPLETE",
    ) -> TraceEvent:
        if self.max_events <= 0:
            raise ValueError("max_events must be positive")
        event = TraceEvent(
            sequence=self._next_sequence,
            at=float(at),
            kind=kind,
            source=source,
            run_id=self.run_id,
            payload=redact_value(payload or {}),
            session_id=session_id,
            event_id=event_id,
            turn_id=turn_id,
            request_id=request_id,
            parent_request_id=parent_request_id,
            call_id=call_id,
            delivery_id=delivery_id,
            capture_mode=capture_mode,
        )
        self._next_sequence += 1
        if len(self._events) >= self.max_events:
            self._events.popleft()
            self.dropped_count += 1
        self._events.append(event)
        self._prune_by_time(at=float(at))
        return event

    def _prune_by_time(self, *, at: float) -> None:
        if self.retention_seconds is None:
            return
        cutoff = at - float(self.retention_seconds)
        while self._events and self._events[0].at < cutoff:
            self._events.popleft()
            self.dropped_count += 1

    def prune(self, *, at: float) -> int:
        """Drop events older than the configured retention window."""

        before = len(self._events)
        self._prune_by_time(at=float(at))
        return before - len(self._events)

    def clear(self) -> None:
        self._events.clear()
        self.dropped_count = 0

    def snapshot(self) -> tuple[TraceEvent, ...]:
        return tuple(self._events)

    def to_dicts(self) -> list[dict[str, Any]]:
        return [event.to_dict() for event in self._events]
