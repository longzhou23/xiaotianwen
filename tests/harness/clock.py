"""Deterministic virtual time for replay fixtures and fake adapters."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class VirtualClock:
    """A manually advanced monotonic clock expressed in seconds."""

    start: float = 0.0
    _now: float = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._now = float(self.start)

    @property
    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> float:
        seconds = float(seconds)
        if seconds < 0:
            raise ValueError("virtual time may not move backwards")
        self._now += seconds
        return self._now

    def set(self, value: float) -> float:
        value = float(value)
        if value < self._now:
            raise ValueError("virtual time may not move backwards")
        self._now = value
        return self._now
