"""Bounded, content-redacted in-memory observation records for shadow tests."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

from ..contracts.validation import (
    ContractValidationError,
    JsonValue,
    ensure_json_value,
    require_finite_timestamp,
    require_positive_int,
)


@dataclass(frozen=True, slots=True)
class ShadowObservation:
    """A diagnostic record that is safe to emit as structured telemetry."""

    kind: str
    at: float
    payload: dict[str, JsonValue]

    def __post_init__(self) -> None:
        if self.kind not in {"turn_comparison", "context_comparison"}:
            raise ContractValidationError("unsupported shadow observation kind")
        object.__setattr__(self, "at", require_finite_timestamp(self.at, "at"))
        validated = ensure_json_value(self.payload, "payload")
        if not isinstance(validated, dict):
            raise ContractValidationError("observation payload must be a JSON object")
        object.__setattr__(self, "payload", validated)

    def to_dict(self) -> dict[str, JsonValue]:
        return {"kind": self.kind, "at": self.at, "payload": self.payload}


class StructuralObservationStore:
    """A bounded in-memory store; it never writes user text or a database."""

    def __init__(self, *, max_entries: int = 200) -> None:
        self.max_entries = require_positive_int(max_entries, "max_entries")
        self._records: deque[ShadowObservation] = deque(maxlen=self.max_entries)

    def record(
        self,
        kind: str,
        payload: dict[str, JsonValue],
        *,
        at: float | None = None,
    ) -> ShadowObservation:
        observation = ShadowObservation(
            kind=kind,
            at=time.time() if at is None else at,
            payload=payload,
        )
        self._records.append(observation)
        return observation

    def snapshot(self) -> tuple[ShadowObservation, ...]:
        return tuple(self._records)

    def clear(self) -> None:
        self._records.clear()
