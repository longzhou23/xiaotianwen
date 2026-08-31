"""Deterministic ID allocation for replay observations."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field


_PREFIX = re.compile(r"[^a-z0-9_-]+")


@dataclass(slots=True)
class DeterministicIdFactory:
    """Allocate stable, readable IDs without UUIDs or wall-clock input."""

    namespace: str = "synthetic"
    _counters: dict[str, int] = field(default_factory=lambda: defaultdict(int), init=False)

    def next(self, kind: str) -> str:
        normalized_kind = _PREFIX.sub("-", kind.lower()).strip("-") or "item"
        self._counters[normalized_kind] += 1
        return f"{self.namespace}-{normalized_kind}-{self._counters[normalized_kind]:04d}"

    def reset(self) -> None:
        self._counters.clear()
