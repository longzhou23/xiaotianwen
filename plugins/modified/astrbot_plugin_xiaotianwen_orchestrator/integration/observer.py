"""Versioned observation records for an explicit P1 runtime bridge.

The adapter stores structural facts by default.  Body text is only included
when a caller explicitly asks for a local display copy, and that copy is
still credential-redacted and bounded.  No platform object is retained.
"""

from __future__ import annotations

import hashlib
import math
import re
import time
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..contracts import ContextSection, TurnEnvelope
from ..contracts.validation import (
    ContractValidationError,
    JsonValue,
    ensure_json_value,
    require_finite_timestamp,
    require_identifier,
    require_non_empty_string,
    require_positive_int,
    require_source_name,
    sha256_text,
)


OBSERVATION_SCHEMA_VERSION = 2
CAPTURE_MODES = frozenset({"COMPLETE", "PARTIAL", "NOT_CONNECTED"})
REDACTED_SECRET = "[REDACTED_SECRET]"
_MAX_DISPLAY_CHARS = 4_000
_SENSITIVE_KEY = re.compile(
    r"(?:authorization|cookie|api[_-]?key|token|secret|password|private[_-]?key|auth[_-]?code)",
    re.IGNORECASE,
)
_BODY_KEY = re.compile(
    r"(?:prompt|content|message|text|persona|memory|context|instruction|payload)",
    re.IGNORECASE,
)
_ASSIGNMENT = re.compile(
    r"(?P<key>authorization|api[_-]?key|token|secret|password|cookie)\s*[:=]\s*(?P<value>[^\s,;]+)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
    re.DOTALL,
)
_KNOWN_SECRET = re.compile(r"\b(?:sk|rk|ghp|github_pat|xoxb|xoxp)_[A-Za-z0-9_-]{12,}\b", re.IGNORECASE)


def redact_text(value: str, *, max_chars: int = _MAX_DISPLAY_CHARS) -> str:
    """Return bounded display text without exposing common credentials."""

    if not isinstance(value, str):
        value = str(value)
    result = _PRIVATE_KEY.sub(REDACTED_SECRET, value)
    result = _BEARER.sub(f"Bearer {REDACTED_SECRET}", result)
    result = _ASSIGNMENT.sub(lambda match: f"{match.group('key')}={REDACTED_SECRET}", result)
    result = _KNOWN_SECRET.sub(REDACTED_SECRET, result)
    if len(result) > max_chars:
        return result[:max_chars] + "…"
    return result


def _body_summary(value: object) -> dict[str, JsonValue]:
    text = value if isinstance(value, str) else str(value)
    return {"chars": len(text), "sha256": sha256_text(text)}


def _safe_value(value: object, *, key: object | None = None, display_keys: frozenset[str] = frozenset()) -> JsonValue:
    key_text = key.lower() if isinstance(key, str) else ""
    if isinstance(key, str) and _SENSITIVE_KEY.search(key):
        return REDACTED_SECRET
    if isinstance(key, str) and key_text in display_keys:
        return redact_text(value if isinstance(value, str) else str(value))
    if isinstance(key, str) and _BODY_KEY.search(key):
        return _body_summary(value)
    if value is None or type(value) in (str, bool, int):
        return redact_text(value) if isinstance(value, str) else value  # type: ignore[return-value]
    if type(value) is float:
        return value if value == value and abs(value) != float("inf") else 0.0
    if isinstance(value, Mapping):
        return {
            str(child_key): _safe_value(child_value, key=child_key, display_keys=display_keys)
            for child_key, child_value in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [_safe_value(item, display_keys=display_keys) for item in value]
    return f"<{type(value).__name__}>"


def safe_payload(value: Mapping[str, object] | None, *, display_keys: frozenset[str] = frozenset()) -> dict[str, JsonValue]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ContractValidationError("observation payload must be a mapping")
    normalized = _safe_value(value, display_keys=display_keys)
    checked = ensure_json_value(normalized, "observation.payload")
    if not isinstance(checked, dict):
        raise ContractValidationError("observation payload must be a JSON object")
    return checked


@dataclass(frozen=True, slots=True)
class RuntimeObservation:
    """One event compatible with the Local Test Console timeline schema."""

    sequence: int
    timestamp: float
    kind: str
    source: str
    status: str
    run_id: str
    payload: dict[str, JsonValue] = field(default_factory=dict)
    session_id: str | None = None
    event_id: str | None = None
    turn_id: str | None = None
    request_id: str | None = None
    parent_request_id: str | None = None
    call_id: str | None = None
    delivery_id: str | None = None
    capture_mode: str = "PARTIAL"

    def __post_init__(self) -> None:
        object.__setattr__(self, "sequence", require_positive_int(self.sequence, "sequence"))
        object.__setattr__(self, "timestamp", require_finite_timestamp(self.timestamp, "timestamp"))
        object.__setattr__(self, "kind", require_source_name(self.kind, "kind"))
        object.__setattr__(self, "source", require_source_name(self.source, "source"))
        object.__setattr__(self, "status", require_source_name(self.status, "status"))
        object.__setattr__(self, "run_id", require_identifier(self.run_id, "run_id"))
        if self.capture_mode not in CAPTURE_MODES:
            raise ContractValidationError("capture_mode must be COMPLETE, PARTIAL or NOT_CONNECTED")
        for field_name in (
            "session_id",
            "event_id",
            "turn_id",
            "request_id",
            "parent_request_id",
            "call_id",
            "delivery_id",
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, require_identifier(value, field_name))
        checked = ensure_json_value(self.payload, "payload")
        if not isinstance(checked, dict):
            raise ContractValidationError("payload must be a JSON object")
        object.__setattr__(self, "payload", checked)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": OBSERVATION_SCHEMA_VERSION,
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "at": self.timestamp,
            "kind": self.kind,
            "source": self.source,
            "status": self.status,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "event_id": self.event_id,
            "turn_id": self.turn_id,
            "request_id": self.request_id,
            "parent_request_id": self.parent_request_id,
            "call_id": self.call_id,
            "delivery_id": self.delivery_id,
            "capture_mode": self.capture_mode,
            "payload": self.payload,
        }


@dataclass(slots=True)
class RuntimeObservationStore:
    """Bounded in-memory retention with size and optional time bounds."""

    run_id: str
    max_entries: int = 2_000
    retention_seconds: float | None = None
    _records: deque[RuntimeObservation] = field(default_factory=deque, init=False, repr=False)
    _next_sequence: int = field(default=1, init=False, repr=False)
    dropped_count: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.run_id = require_identifier(self.run_id, "run_id")
        self.max_entries = require_positive_int(self.max_entries, "max_entries")
        if self.retention_seconds is not None and (
            isinstance(self.retention_seconds, bool)
            or not isinstance(self.retention_seconds, (int, float))
            or not math.isfinite(float(self.retention_seconds))
            or self.retention_seconds <= 0
        ):
            raise ContractValidationError("retention_seconds must be a positive finite number")

    def emit(
        self,
        kind: str,
        *,
        source: str,
        status: str = "completed",
        timestamp: float | None = None,
        payload: Mapping[str, object] | None = None,
        display_keys: frozenset[str] = frozenset(),
        capture_mode: str = "PARTIAL",
        session_id: str | None = None,
        event_id: str | None = None,
        turn_id: str | None = None,
        request_id: str | None = None,
        parent_request_id: str | None = None,
        call_id: str | None = None,
        delivery_id: str | None = None,
    ) -> RuntimeObservation:
        record = RuntimeObservation(
            sequence=self._next_sequence,
            timestamp=time.time() if timestamp is None else timestamp,
            kind=kind,
            source=source,
            status=status,
            run_id=self.run_id,
            payload=safe_payload(payload, display_keys=display_keys),
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
        if len(self._records) >= self.max_entries:
            self._records.popleft()
            self.dropped_count += 1
        self._records.append(record)
        self._prune_by_time(at=record.timestamp)
        return record

    def _prune_by_time(self, *, at: float) -> None:
        if self.retention_seconds is None:
            return
        cutoff = at - float(self.retention_seconds)
        while self._records and self._records[0].timestamp < cutoff:
            self._records.popleft()
            self.dropped_count += 1

    def prune(self, *, at: float) -> int:
        """Drop observations older than the configured retention window."""

        at = require_finite_timestamp(at, "at")
        before = len(self._records)
        self._prune_by_time(at=at)
        return before - len(self._records)

    def snapshot(self) -> tuple[RuntimeObservation, ...]:
        return tuple(self._records)

    def to_dicts(self) -> list[dict[str, JsonValue]]:
        return [record.to_dict() for record in self._records]

    def clear(self) -> None:
        self._records.clear()
        self.dropped_count = 0


class ObservationAdapter:
    """High-level event names shared by fake and isolated AstrBot adapters."""

    def __init__(self, store: RuntimeObservationStore, *, source: str = "P1_ADAPTER") -> None:
        self.store = store
        self.source = require_source_name(source, "source")

    def input_received(self, turn: TurnEnvelope, *, capture_text: bool = False, timestamp: float | None = None) -> RuntimeObservation:
        if not isinstance(turn, TurnEnvelope):
            raise ContractValidationError("input_received requires TurnEnvelope")
        payload: dict[str, object] = {
            "route": turn.route,
            "trigger": turn.trigger,
            "text_chars": len(turn.text),
            "text_hash": turn.text_hash,
            "media_count": len(turn.media),
            "media_ids": [item.media_id for item in turn.media],
            "has_reply": turn.reply_to is not None,
        }
        if capture_text:
            payload["display"] = turn.text
        return self.store.emit(
            "ui.input.received",
            source=self.source,
            timestamp=timestamp,
            payload=payload,
            display_keys=frozenset({"display"}),
            capture_mode="COMPLETE" if capture_text else "PARTIAL",
            session_id=turn.session_id,
            event_id=str(turn.metadata.get("message_id", "")) or None,
            turn_id=turn.request_id,
            request_id=turn.request_id,
        )

    def normalized_event(self, turn: TurnEnvelope, *, action: str, timestamp: float | None = None) -> RuntimeObservation:
        return self.store.emit(
            "onebot.event.normalized",
            source=self.source,
            timestamp=timestamp,
            payload={"action": action, "turn_fingerprint": turn.structural_fingerprint()},
            capture_mode="PARTIAL",
            session_id=turn.session_id,
            turn_id=turn.request_id,
            request_id=turn.request_id,
        )

    def turn_stage(self, turn: TurnEnvelope, *, stage: str, status: str = "completed", timestamp: float | None = None) -> RuntimeObservation:
        return self.store.emit(
            f"turn.{stage.lower()}",
            source=self.source,
            status=status,
            timestamp=timestamp,
            payload={"route": turn.route},
            session_id=turn.session_id,
            turn_id=turn.request_id,
            request_id=turn.request_id,
        )

    def context_section(self, section: ContextSection, *, timestamp: float | None = None) -> RuntimeObservation:
        if not isinstance(section, ContextSection):
            raise ContractValidationError("context_section requires ContextSection")
        return self.store.emit(
            "context.section.added",
            source=self.source,
            timestamp=timestamp,
            payload=section.structural_summary(),
            capture_mode="PARTIAL",
        )

    def context_assembled(self, result: object, *, timestamp: float | None = None) -> RuntimeObservation:
        summary = result.structural_summary() if hasattr(result, "structural_summary") else {"result_type": type(result).__name__}
        return self.store.emit("context.assembled", source=self.source, timestamp=timestamp, payload=summary)

    def request_started(
        self,
        request_id: str,
        *,
        role: str,
        model: str,
        message_count: int,
        stream: bool,
        tool_count: int = 0,
        prompt: str | None = None,
        parent_request_id: str | None = None,
        timestamp: float | None = None,
    ) -> RuntimeObservation:
        if isinstance(message_count, bool) or not isinstance(message_count, int) or message_count < 0:
            raise ContractValidationError("message_count must be a non-negative integer")
        if isinstance(tool_count, bool) or not isinstance(tool_count, int) or tool_count < 0:
            raise ContractValidationError("tool_count must be a non-negative integer")
        payload: dict[str, object] = {
            "role": role,
            "model": model,
            "message_count": message_count,
            "stream": stream,
            "tool_count": tool_count,
        }
        if prompt is not None:
            payload.update({"prompt_chars": len(prompt), "prompt_hash": sha256_text(prompt)})
        return self.store.emit(
            "request.started",
            source=self.source,
            status="started",
            timestamp=timestamp,
            payload=payload,
            request_id=request_id,
            parent_request_id=parent_request_id,
        )

    def request_chunk(self, request_id: str, *, chunk: str, index: int, timestamp: float | None = None) -> RuntimeObservation:
        return self.store.emit(
            "request.chunk",
            source=self.source,
            status="partial",
            timestamp=timestamp,
            payload={"chunk_index": index, "chars": len(chunk), "hash": sha256_text(chunk)},
            request_id=request_id,
        )

    def request_completed(self, request_id: str, *, role: str, finish_reason: str, usage: Mapping[str, object] | None = None, tool_call_count: int = 0, parent_request_id: str | None = None, timestamp: float | None = None) -> RuntimeObservation:
        if isinstance(tool_call_count, bool) or not isinstance(tool_call_count, int) or tool_call_count < 0:
            raise ContractValidationError("tool_call_count must be a non-negative integer")
        payload: dict[str, object] = {"role": role, "finish_reason": finish_reason, "tool_call_count": tool_call_count}
        if usage:
            payload["usage"] = dict(usage)
        return self.store.emit(
            "request.completed",
            source=self.source,
            status="completed",
            timestamp=timestamp,
            payload=payload,
            request_id=request_id,
            parent_request_id=parent_request_id,
        )

    def request_failed(self, request_id: str, *, role: str, error_class: str, timestamp: float | None = None) -> RuntimeObservation:
        return self.store.emit(
            "request.failed",
            source=self.source,
            status="failed",
            timestamp=timestamp,
            payload={"role": role, "error_class": error_class},
            request_id=request_id,
        )

    def tool(self, kind: str, *, request_id: str, call_id: str, name: str, effect: str, status: str, result_chars: int = 0, timestamp: float | None = None) -> RuntimeObservation:
        if kind not in {"started", "completed", "suppressed"}:
            raise ContractValidationError("unsupported tool observation kind")
        return self.store.emit(
            f"tool.{kind}",
            source=self.source,
            status=status,
            timestamp=timestamp,
            payload={"name": name, "effect": effect, "result_chars": result_chars},
            request_id=request_id,
            call_id=call_id,
        )

    def audit(self, *, request_id: str, decision: str, labels: Sequence[str] = (), timestamp: float | None = None) -> RuntimeObservation:
        return self.store.emit(
            "audit.completed",
            source=self.source,
            timestamp=timestamp,
            payload={"decision": decision, "labels": list(labels)},
            request_id=request_id,
        )

    def output(self, *, request_id: str, stage: str, text: str, capture_text: bool = False, timestamp: float | None = None) -> RuntimeObservation:
        payload: dict[str, object] = {"stage": stage, "chars": len(text), "hash": sha256_text(text)}
        if capture_text:
            payload["display"] = text
        return self.store.emit(
            "output.cleaned" if stage == "cleaned" else "output.segmented",
            source=self.source,
            timestamp=timestamp,
            payload=payload,
            display_keys=frozenset({"display"}),
            capture_mode="COMPLETE" if capture_text else "PARTIAL",
            request_id=request_id,
        )

    def delivery(self, *, request_id: str, delivery_id: str, status: str, payload_type: str, text: str = "", timestamp: float | None = None) -> RuntimeObservation:
        kind = "delivery.completed" if status == "completed" else "delivery.suppressed" if status == "suppressed" else "delivery.attempted"
        return self.store.emit(
            kind,
            source=self.source,
            status=status,
            timestamp=timestamp,
            payload={"type": payload_type, "chars": len(text), "text_hash": sha256_text(text) if text else None},
            request_id=request_id,
            delivery_id=delivery_id,
        )

    def log(
        self,
        *,
        level: str,
        message: str,
        capture_text: bool = False,
        request_id: str | None = None,
        timestamp: float | None = None,
    ) -> RuntimeObservation:
        payload: dict[str, object] = {
            "level": level.upper(),
            "message_chars": len(message),
            "message_hash": sha256_text(message),
        }
        if capture_text:
            payload["display"] = message
        return self.store.emit(
            "log.emitted",
            source=self.source,
            status=level.lower(),
            timestamp=timestamp,
            payload=payload,
            display_keys=frozenset({"display"}),
            capture_mode="COMPLETE" if capture_text else "PARTIAL",
            request_id=request_id,
        )

    def guard(self, *, kind: str, reason: str, request_id: str | None = None, timestamp: float | None = None) -> RuntimeObservation:
        if kind not in {"network_blocked", "write_blocked"}:
            raise ContractValidationError("unsupported guard kind")
        return self.store.emit(
            f"guard.{kind}",
            source=self.source,
            status="blocked",
            timestamp=timestamp,
            payload={"reason": reason},
            request_id=request_id,
        )
