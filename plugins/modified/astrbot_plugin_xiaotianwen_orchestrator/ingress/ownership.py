"""Mode and canary ownership decisions before a main reply is created."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from ..contracts import TurnEnvelope
from ..contracts.validation import ContractValidationError, require_identifier


class OrchestratorMode(str, Enum):
    DISABLED = "disabled"
    SHADOW = "shadow"
    CANARY = "canary"
    ACTIVE = "active"

    @classmethod
    def parse(cls, value: object) -> "OrchestratorMode":
        try:
            return cls(str(value).strip().lower())
        except ValueError as exc:
            raise ContractValidationError(
                "orchestrator_mode must be disabled, shadow, canary or active"
            ) from exc


@dataclass(frozen=True, slots=True)
class CanaryPolicy:
    private_ids: frozenset[str] = frozenset()
    group_ids: frozenset[str] = frozenset()

    @classmethod
    def from_values(
        cls,
        *,
        private_ids: Iterable[str] = (),
        group_ids: Iterable[str] = (),
    ) -> "CanaryPolicy":
        return cls(
            frozenset(require_identifier(value, "private_id") for value in private_ids),
            frozenset(require_identifier(value, "group_id") for value in group_ids),
        )

    def allows(self, session_id: str) -> bool:
        if session_id.startswith("private:"):
            return session_id.split(":", 1)[1] in self.private_ids
        if session_id.startswith("group:"):
            return session_id.split(":", 1)[1] in self.group_ids
        return False


@dataclass(frozen=True, slots=True)
class OwnershipDecision:
    request_id: str
    event_id: str
    mode: OrchestratorMode
    owner: str
    should_dispatch: bool
    reason: str


class PrimaryReplyOwnership:
    """Ensures one event and one request never acquire two reply owners."""

    ORCHESTRATOR = "xiaotianwen_orchestrator"
    LEGACY = "group_chat_plus"

    def __init__(
        self,
        mode: OrchestratorMode | str = OrchestratorMode.DISABLED,
        *,
        canary: CanaryPolicy | None = None,
        max_events: int = 2_000,
    ) -> None:
        self.mode = mode if isinstance(mode, OrchestratorMode) else OrchestratorMode.parse(mode)
        self.canary = canary or CanaryPolicy()
        if type(max_events) is not int or max_events <= 0:
            raise ContractValidationError("max_events must be positive")
        self.max_events = max_events
        self._by_event: dict[str, OwnershipDecision] = {}
        self._by_request: dict[str, OwnershipDecision] = {}
        self._fallback_sessions: set[str] = set()

    def decide(self, turn: TurnEnvelope, event_id: str) -> OwnershipDecision:
        if not isinstance(turn, TurnEnvelope):
            raise ContractValidationError("ownership requires TurnEnvelope")
        event = require_identifier(event_id, "event_id")
        previous = self._by_event.get(event)
        if previous is not None:
            return previous
        if turn.session_id in self._fallback_sessions:
            owner, dispatch, reason = self.LEGACY, False, "session is in explicit legacy fallback"
        elif self.mode is OrchestratorMode.ACTIVE:
            owner, dispatch, reason = self.ORCHESTRATOR, True, "active mode owns main reply"
        elif self.mode is OrchestratorMode.CANARY and self.canary.allows(turn.session_id):
            owner, dispatch, reason = self.ORCHESTRATOR, True, "session is in canary allowlist"
        elif self.mode is OrchestratorMode.SHADOW:
            owner, dispatch, reason = self.LEGACY, False, "shadow observes without dispatch"
        else:
            owner, dispatch, reason = self.LEGACY, False, "legacy path retains ownership"
        decision = OwnershipDecision(turn.request_id, event, self.mode, owner, dispatch, reason)
        existing_request = self._by_request.get(turn.request_id)
        if existing_request is not None and existing_request.owner != owner:
            raise ContractValidationError("request_id cannot have two main reply owners")
        self._by_event[event] = decision
        self._by_request[turn.request_id] = decision
        while len(self._by_event) > self.max_events:
            oldest_event = next(iter(self._by_event))
            removed = self._by_event.pop(oldest_event)
            if self._by_request.get(removed.request_id) == removed:
                self._by_request.pop(removed.request_id, None)
        return decision

    def fallback_session(self, session_id: str) -> None:
        self._fallback_sessions.add(require_identifier(session_id, "session_id"))

    def restore_session(self, session_id: str) -> None:
        self._fallback_sessions.discard(session_id)

    def clear(self) -> None:
        self._by_event.clear()
        self._by_request.clear()
        self._fallback_sessions.clear()

    @property
    def event_count(self) -> int:
        return len(self._by_event)
