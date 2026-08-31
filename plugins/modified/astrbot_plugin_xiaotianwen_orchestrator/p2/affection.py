"""Idempotent, provider-explicit emotion bookkeeping for P2 migration."""

from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from ..contracts.validation import ContractValidationError, require_identifier, require_non_empty_string, require_positive_int


@dataclass(frozen=True, slots=True)
class ProviderBinding:
    interactive_provider_id: str
    idle_provider_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "interactive_provider_id", require_identifier(self.interactive_provider_id, "interactive_provider_id"))
        object.__setattr__(self, "idle_provider_id", require_identifier(self.idle_provider_id, "idle_provider_id"))

    def resolve(self, mode: str, available_provider_ids: set[str] | frozenset[str]) -> "ProviderResolution":
        normalized_mode = require_non_empty_string(mode, "mode").lower()
        if normalized_mode not in {"interactive", "idle"}:
            raise ContractValidationError("emotion mode must be interactive or idle")
        provider_id = self.interactive_provider_id if normalized_mode == "interactive" else self.idle_provider_id
        available = {str(item) for item in available_provider_ids}
        if provider_id not in available:
            return ProviderResolution(normalized_mode, provider_id, "provider_missing")
        return ProviderResolution(normalized_mode, provider_id, "available")


@dataclass(frozen=True, slots=True)
class ProviderResolution:
    mode: str
    provider_id: str
    status: str

    @property
    def usable(self) -> bool:
        return self.status == "available"


def classify_provider_result(value: object = None, *, error: BaseException | None = None, http_status: int | None = None) -> str:
    if error is not None:
        if isinstance(error, TimeoutError):
            return "timeout"
        if isinstance(error, LookupError):
            return "provider_missing"
        return "provider_error"
    if http_status == 400:
        return "api_400"
    if value is None or (isinstance(value, str) and not value.strip()):
        return "empty"
    if isinstance(value, str):
        stripped = value.strip()
        if not (stripped.startswith("{") and stripped.endswith("}")):
            return "non_json"
    return "success"


@dataclass(frozen=True, slots=True)
class EmotionObservation:
    bot_id_hash: str
    user_id_hash: str
    message_id: str
    mode: str
    provider_id: str
    parse_status: str
    write_status: str

    def to_dict(self) -> dict[str, str]:
        return {
            "bot_id_hash": self.bot_id_hash,
            "user_id_hash": self.user_id_hash,
            "message_id": self.message_id,
            "mode": self.mode,
            "provider_id": self.provider_id,
            "parse_status": self.parse_status,
            "write_status": self.write_status,
        }


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:16]


class EmotionObservationLedger:
    """One message ID can create at most one stored emotion observation."""

    def __init__(self, *, max_records: int = 1_000) -> None:
        self.max_records = require_positive_int(max_records, "max_records")
        self._claimed: set[tuple[str, str]] = set()
        self._records: deque[EmotionObservation] = deque(maxlen=self.max_records)

    def record(
        self,
        *,
        bot_id: str,
        user_id: str,
        message_id: str,
        mode: str,
        provider_id: str,
        parse_status: str,
        write_status: str = "recorded",
    ) -> tuple[EmotionObservation, bool]:
        bot = require_identifier(bot_id, "bot_id")
        user = require_identifier(user_id, "user_id")
        message = require_identifier(message_id, "message_id")
        key = (bot, message)
        accepted = key not in self._claimed
        if accepted:
            self._claimed.add(key)
        observation = EmotionObservation(
            _short_hash(bot), _short_hash(user), message,
            require_non_empty_string(mode, "mode"), require_identifier(provider_id, "provider_id"),
            require_non_empty_string(parse_status, "parse_status"),
            require_non_empty_string(write_status if accepted else "duplicate_suppressed", "write_status"),
        )
        if accepted:
            self._records.append(observation)
            while len(self._claimed) > self.max_records:
                self._claimed.pop()
        return observation, accepted

    def snapshot(self) -> tuple[EmotionObservation, ...]:
        return tuple(self._records)


class BackgroundTaskRegistry:
    """Keep one decay task per bot and cancel it explicitly on unload."""

    def __init__(self) -> None:
        self._decay_by_bot: dict[str, object] = {}

    def register_decay(self, bot_id: str, task_handle: object) -> bool:
        bot = require_identifier(bot_id, "bot_id")
        if task_handle is None:
            raise ContractValidationError("task_handle must not be None")
        existing = self._decay_by_bot.get(bot)
        if existing is not None and not getattr(existing, "done", lambda: False)():
            return False
        self._decay_by_bot[bot] = task_handle
        return True

    def cancel_bot(self, bot_id: str) -> bool:
        bot = require_identifier(bot_id, "bot_id")
        handle = self._decay_by_bot.pop(bot, None)
        if handle is None:
            return False
        cancel = getattr(handle, "cancel", None)
        if callable(cancel):
            cancel()
        return True

    def cancel_all(self) -> int:
        bots = tuple(self._decay_by_bot)
        return sum(1 for bot in bots if self.cancel_bot(bot))

    def count(self) -> int:
        return len(self._decay_by_bot)
