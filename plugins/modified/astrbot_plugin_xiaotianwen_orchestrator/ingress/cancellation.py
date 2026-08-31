"""A record-only cancellation marker for shadow-mode comparisons."""

from __future__ import annotations

from dataclasses import dataclass

from ..contracts.validation import require_finite_timestamp, require_identifier, require_non_empty_string


@dataclass(frozen=True, slots=True)
class ShadowCancellation:
    """Represents a would-cancel decision without touching a live task."""

    request_id: str
    reason: str
    at: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", require_identifier(self.request_id, "request_id"))
        object.__setattr__(self, "reason", require_non_empty_string(self.reason, "reason"))
        object.__setattr__(self, "at", require_finite_timestamp(self.at, "at"))
