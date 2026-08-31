"""Pure proactive-chat and long-running status policies.

The policy returns decisions and status intents.  It never fabricates an
AstrBot event, invokes a Hook chain, starts a task, or sends a message.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..contracts.validation import ContractValidationError, require_finite_timestamp, require_identifier, require_positive_int


def _bounded_probability(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise ContractValidationError(f"{name} must be between zero and one")
    return float(value)


@dataclass(frozen=True, slots=True)
class ProactiveInput:
    session_id: str
    scope: str
    enabled: bool
    allowlisted: bool
    user_activity_count: int
    min_user_activity: int
    silence_seconds: float
    min_silence_seconds: float
    cooldown_seconds: float
    probability: float
    draw: float
    quiet_time: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", require_identifier(self.session_id, "session_id"))
        if self.scope not in {"group", "private"}:
            raise ContractValidationError("proactive scope must be group or private")
        if type(self.enabled) is not bool or type(self.allowlisted) is not bool or type(self.quiet_time) is not bool:
            raise ContractValidationError("proactive flags must be boolean")
        if type(self.user_activity_count) is not int or self.user_activity_count < 0:
            raise ContractValidationError("user_activity_count must be a non-negative integer")
        object.__setattr__(self, "user_activity_count", require_positive_int(self.user_activity_count + 1, "user_activity_count") - 1)
        object.__setattr__(self, "min_user_activity", require_positive_int(self.min_user_activity, "min_user_activity"))
        for name in ("silence_seconds", "min_silence_seconds", "cooldown_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or not math.isfinite(float(value)):
                raise ContractValidationError(f"{name} must be non-negative")
        object.__setattr__(self, "probability", _bounded_probability(self.probability, "probability"))
        object.__setattr__(self, "draw", _bounded_probability(self.draw, "draw"))


@dataclass(frozen=True, slots=True)
class ProactiveDecision:
    session_id: str
    triggered: bool
    reason_code: str
    route: str = "proactive"

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", require_identifier(self.session_id, "session_id"))
        if type(self.triggered) is not bool:
            raise ContractValidationError("proactive triggered must be boolean")
        if self.reason_code not in {
            "disabled",
            "not_allowlisted",
            "quiet_time",
            "insufficient_activity",
            "not_silent_enough",
            "cooldown",
            "probability_miss",
            "triggered",
        }:
            raise ContractValidationError("unsupported proactive reason_code")

    def to_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "triggered": self.triggered,
            "reason_code": self.reason_code,
            "route": self.route,
        }


class ProactivePolicy:
    """Evaluate one common policy for group and private sessions."""

    def evaluate(self, candidate: ProactiveInput) -> ProactiveDecision:
        if not isinstance(candidate, ProactiveInput):
            raise ContractValidationError("proactive policy requires ProactiveInput")
        if not candidate.enabled:
            return ProactiveDecision(candidate.session_id, False, "disabled")
        if not candidate.allowlisted:
            return ProactiveDecision(candidate.session_id, False, "not_allowlisted")
        if candidate.quiet_time:
            return ProactiveDecision(candidate.session_id, False, "quiet_time")
        if candidate.user_activity_count < candidate.min_user_activity:
            return ProactiveDecision(candidate.session_id, False, "insufficient_activity")
        if candidate.silence_seconds < candidate.min_silence_seconds:
            return ProactiveDecision(candidate.session_id, False, "not_silent_enough")
        if candidate.cooldown_seconds > 0:
            return ProactiveDecision(candidate.session_id, False, "cooldown")
        if candidate.draw >= candidate.probability:
            return ProactiveDecision(candidate.session_id, False, "probability_miss")
        return ProactiveDecision(candidate.session_id, True, "triggered")


@dataclass(frozen=True, slots=True)
class StatusNotice:
    request_id: str
    route: str
    action: str
    status: str
    elapsed_seconds: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", require_identifier(self.request_id, "request_id"))
        if self.action not in {"show", "update", "retract"}:
            raise ContractValidationError("unsupported status notice action")
        if self.status not in {"working", "completed", "cancelled"}:
            raise ContractValidationError("unsupported status notice state")
        object.__setattr__(self, "elapsed_seconds", require_finite_timestamp(self.elapsed_seconds, "elapsed_seconds"))


@dataclass(slots=True)
class StatusNoticeCoordinator:
    """Correlate optional platform status notices with the final request."""

    notice_after_seconds: float = 3.0
    _started: dict[str, tuple[str, float]] = field(default_factory=dict, init=False, repr=False)
    _emitted: set[str] = field(default_factory=set, init=False, repr=False)

    def __post_init__(self) -> None:
        if (
            isinstance(self.notice_after_seconds, bool)
            or not isinstance(self.notice_after_seconds, (int, float))
            or not math.isfinite(float(self.notice_after_seconds))
            or self.notice_after_seconds <= 0
        ):
            raise ContractValidationError("notice_after_seconds must be positive")

    def start(self, request_id: str, *, route: str, started_at: float) -> None:
        request_id = require_identifier(request_id, "request_id")
        started_at = require_finite_timestamp(started_at, "started_at")
        self._started[request_id] = (route, started_at)
        self._emitted.discard(request_id)

    def maybe_show(self, request_id: str, *, now: float) -> StatusNotice | None:
        request_id = require_identifier(request_id, "request_id")
        started = self._started.get(request_id)
        if started is None:
            raise ContractValidationError("unknown request_id for status notice")
        route, started_at = started
        now = require_finite_timestamp(now, "now")
        elapsed = max(0.0, now - started_at)
        if request_id in self._emitted or elapsed < self.notice_after_seconds:
            return None
        self._emitted.add(request_id)
        return StatusNotice(request_id, route, "show", "working", elapsed)

    def finish(self, request_id: str, *, now: float, cancelled: bool = False) -> StatusNotice | None:
        request_id = require_identifier(request_id, "request_id")
        started = self._started.pop(request_id, None)
        emitted = request_id in self._emitted
        self._emitted.discard(request_id)
        if started is None or not emitted:
            return None
        route, started_at = started
        now = require_finite_timestamp(now, "now")
        elapsed = max(0.0, now - started_at)
        return StatusNotice(request_id, route, "retract" if cancelled else "update", "cancelled" if cancelled else "completed", elapsed)

    def clear(self) -> None:
        self._started.clear()
        self._emitted.clear()
