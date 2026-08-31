"""Deterministic fake adapters and observations used by the offline harness."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from .ids import DeterministicIdFactory
from .redact import redact_text, redact_value


def _fingerprint(value: Any) -> str:
    encoded = repr(redact_value(value)).encode("utf-8", "replace")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class SpyRecord:
    sequence: int
    kind: str
    status: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "kind": self.kind,
            "status": self.status,
            "payload": redact_value(self.payload),
        }


@dataclass(slots=True)
class _Probe:
    id_factory: DeterministicIdFactory = field(default_factory=DeterministicIdFactory)
    records: list[SpyRecord] = field(default_factory=list)

    def _record(self, kind: str, status: str, **payload: Any) -> SpyRecord:
        record = SpyRecord(len(self.records) + 1, kind, status, redact_value(payload))
        self.records.append(record)
        return record


class ProviderSpy(_Probe):
    """Records role-separated fake model calls; it never contacts a provider."""

    def request(
        self,
        *,
        role: str,
        messages: list[dict[str, Any]],
        stream: bool = False,
        outcome: str = "final",
    ) -> dict[str, Any]:
        request_id = self.id_factory.next("request")
        self._record(
            "provider.request",
            "started",
            request_id=request_id,
            role=role,
            stream=stream,
            message_count=len(messages),
            messages_fingerprint=_fingerprint(messages),
        )
        status = "completed" if outcome in {"final", "tool_call"} else outcome
        self._record("provider.request", status, request_id=request_id, role=role, outcome=outcome)
        return {"request_id": request_id, "role": role, "outcome": outcome, "stream": stream}


class VLMSpy(_Probe):
    """Tracks first analysis versus an existing image-description cache hit."""

    def analyze(self, media_id: str, *, existing_summary: bool = False, outcome: str = "completed") -> dict[str, Any]:
        if existing_summary:
            self._record("vlm.cache", "hit", media_id=media_id)
            return {"media_id": media_id, "status": "cache_hit", "summary": "synthetic-summary"}
        call_id = self.id_factory.next("vlm")
        self._record("vlm.request", "started", call_id=call_id, media_id=media_id)
        self._record("vlm.request", outcome, call_id=call_id, media_id=media_id)
        return {"media_id": media_id, "call_id": call_id, "status": outcome, "summary": "synthetic-summary"}


class EmbeddingSpy(_Probe):
    def embed(self, texts: list[str], *, model_class: str = "fake-embedding") -> dict[str, Any]:
        request_id = self.id_factory.next("embedding")
        self._record(
            "embedding.request",
            "completed",
            request_id=request_id,
            model_class=model_class,
            input_count=len(texts),
            inputs_fingerprint=_fingerprint(texts),
        )
        return {"request_id": request_id, "vectors": [[0.0, 1.0] for _ in texts]}


class ToolSpy(_Probe):
    """Records fake tool calls and suppresses all P0 side-effect effects."""

    _SIDE_EFFECTS = frozenset({"send", "write", "steal", "status"})

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._executed_keys: set[tuple[str, str]] = set()

    def execute(
        self,
        *,
        name: str,
        effect: str = "read",
        arguments: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        call_id = self.id_factory.next("call")
        safe_key = idempotency_key or call_id
        identity = (name, safe_key)
        duplicate = identity in self._executed_keys
        self._executed_keys.add(identity)
        status = "duplicate" if duplicate else ("suppressed" if effect in self._SIDE_EFFECTS else "completed")
        self._record(
            "tool.execution",
            status,
            call_id=call_id,
            name=name,
            effect=effect,
            arguments_fingerprint=_fingerprint(arguments or {}),
            idempotency_key=safe_key,
        )
        return {"call_id": call_id, "status": status, "name": name, "effect": effect}


class DeliverySpy(_Probe):
    """A fake final sender with per-turn idempotency and cancellation suppression."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._delivered: set[tuple[str, str]] = set()

    def deliver(self, *, turn_id: str, payload_type: str, text: str, cancelled: bool = False) -> dict[str, Any]:
        delivery_id = self.id_factory.next("delivery")
        identity = (turn_id, payload_type)
        if cancelled:
            status = "suppressed"
        elif identity in self._delivered:
            status = "duplicate"
        else:
            self._delivered.add(identity)
            status = "completed"
        self._record(
            "delivery",
            status,
            delivery_id=delivery_id,
            turn_id=turn_id,
            payload_type=payload_type,
            chars=len(text),
            fingerprint=_fingerprint(text),
        )
        return {"delivery_id": delivery_id, "status": status}


class StorageSpy(_Probe):
    """Records intended persistence without opening any database or host file."""

    def write(self, *, target: str, operation: str = "upsert", allowed: bool = False) -> dict[str, Any]:
        status = "recorded" if allowed else "suppressed"
        self._record("storage.write", status, target=target, operation=operation)
        return {"status": status, "target": target}


class LoggerProbe(_Probe):
    def log(self, level: str, message: str, **fields: Any) -> SpyRecord:
        return self._record("log", level.upper(), message=redact_text(message), fields=redact_value(fields))


class HookTrace(_Probe):
    def stage(self, name: str, *, owner: str, status: str = "completed") -> SpyRecord:
        return self._record("hook.stage", status, name=name, owner=owner)


class CancellationProbe(_Probe):
    def cancel(self, turn_id: str, *, reason: str) -> SpyRecord:
        return self._record("turn.cancel", "requested", turn_id=turn_id, reason=reason)

    def late_result(self, turn_id: str, *, delivery_suppressed: bool) -> SpyRecord:
        return self._record(
            "turn.late_result",
            "suppressed" if delivery_suppressed else "delivered",
            turn_id=turn_id,
        )
