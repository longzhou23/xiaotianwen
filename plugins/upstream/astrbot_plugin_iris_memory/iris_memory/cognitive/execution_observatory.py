"""Bounded, in-memory observability for completed behavior executions.

This is a diagnostic/runtime cache, not cognitive memory. Records are already
frozen ``BehaviorExecutionRecord`` contracts; the registry never edits or
reconstructs them, and it is intentionally empty after a runtime restart.
"""

from __future__ import annotations

from collections import deque
from threading import RLock

from .contracts import BehaviorExecutionRecord
from .episode import Episode, EpisodeEventKind, EpisodeEventRef


class ExecutionRecordObservatory:
    """Thread-safe bounded registry of recently completed execution records."""

    DEFAULT_MAX_RECORDS = 256

    def __init__(self, *, max_records: int = DEFAULT_MAX_RECORDS) -> None:
        if not isinstance(max_records, int) or max_records <= 0:
            raise ValueError("max_records must be a positive integer")
        self._max_records = max_records
        self._records: deque[BehaviorExecutionRecord] = deque(maxlen=max_records)
        self._lock = RLock()

    @property
    def max_records(self) -> int:
        return self._max_records

    def record(self, execution: BehaviorExecutionRecord) -> BehaviorExecutionRecord:
        """Record one fully formed immutable execution and return it unchanged."""
        if not isinstance(execution, BehaviorExecutionRecord):
            raise TypeError("ExecutionRecordObservatory accepts BehaviorExecutionRecord only")
        with self._lock:
            self._records.append(execution)
        return execution

    def recent(self) -> tuple[BehaviorExecutionRecord, ...]:
        """Return a detached insertion-ordered view of the bounded buffer."""
        with self._lock:
            return tuple(self._records)

    def find_for_event_ref(self, ref: EpisodeEventRef) -> BehaviorExecutionRecord | None:
        """Find the exact execution represented by a HOST_OUTPUT Episode ref."""
        if ref.kind is not EpisodeEventKind.HOST_OUTPUT:
            return None
        if not ref.trace_id or not ref.source_event_id or not ref.execution_record_id:
            return None
        with self._lock:
            candidates = tuple(self._records)
        matches = [
            record
            for record in candidates
            if record.trace.trace_id == ref.trace_id
            and record.trace.event_id == ref.source_event_id
            and f"{record.trace.trace_id}:{record.revision}" == ref.execution_record_id
            and record.stage.value == "HOST_OUTPUT"
        ]
        return matches[-1] if matches else None

    def find_for_episode(self, episode: Episode) -> tuple[tuple[EpisodeEventRef, BehaviorExecutionRecord], ...]:
        """Return all exact HOST_OUTPUT matches in deterministic Episode order."""
        matches: list[tuple[EpisodeEventRef, BehaviorExecutionRecord]] = []
        for ref in episode.event_refs:
            if ref.kind is not EpisodeEventKind.HOST_OUTPUT:
                continue
            record = self.find_for_event_ref(ref)
            if record is not None:
                matches.append((ref, record))
        return tuple(matches)

    def clear(self) -> None:
        """Clear diagnostic state; not used by cognitive behavior or Web routes."""
        with self._lock:
            self._records.clear()
