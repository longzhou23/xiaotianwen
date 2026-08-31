"""Turn-level request envelope with stable IDs and safe serialization."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from .media import MediaRef
from .validation import (
    ContractValidationError,
    JsonValue,
    ensure_json_value,
    require_finite_timestamp,
    require_identifier,
    require_optional_string,
    require_source_name,
    sha256_text,
    structural_fingerprint,
)

ALLOWED_ROUTES = frozenset({"chat", "agent", "decision", "proactive", "vision", "background"})


@dataclass(frozen=True, slots=True)
class TurnEnvelope:
    """Normalized user intent before context construction or model dispatch.

    The model purposely contains only ordinary values. Platform event objects
    must remain at the ingress boundary and are never copied into metadata.
    """

    request_id: str
    session_id: str
    route: str
    trigger: str
    sender_id: str
    text: str
    reply_to: str | None
    media: tuple[MediaRef, ...]
    received_at: float
    batch_started_at: float
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", require_identifier(self.request_id, "request_id"))
        object.__setattr__(self, "session_id", require_identifier(self.session_id, "session_id"))
        route = require_source_name(self.route, "route").lower()
        if route not in ALLOWED_ROUTES:
            allowed = ", ".join(sorted(ALLOWED_ROUTES))
            raise ContractValidationError(f"route must be one of: {allowed}")
        object.__setattr__(self, "route", route)
        object.__setattr__(self, "trigger", require_source_name(self.trigger, "trigger"))
        object.__setattr__(self, "sender_id", require_identifier(self.sender_id, "sender_id"))
        if not isinstance(self.text, str):
            raise ContractValidationError("text must be a string")
        object.__setattr__(self, "reply_to", require_optional_string(self.reply_to, "reply_to"))
        normalized_media: list[MediaRef] = []
        for item in self.media:
            if not isinstance(item, MediaRef):
                raise ContractValidationError("media must only contain MediaRef values")
            normalized_media.append(item)
        if not self.text.strip() and not normalized_media:
            raise ContractValidationError("a turn must contain text or at least one media item")
        object.__setattr__(self, "media", tuple(normalized_media))
        received_at = require_finite_timestamp(self.received_at, "received_at")
        batch_started_at = require_finite_timestamp(
            self.batch_started_at, "batch_started_at"
        )
        if batch_started_at > received_at:
            raise ContractValidationError("batch_started_at must not be after received_at")
        object.__setattr__(self, "received_at", received_at)
        object.__setattr__(self, "batch_started_at", batch_started_at)
        if not isinstance(self.metadata, Mapping):
            raise ContractValidationError("metadata must be a mapping")
        normalized_metadata = ensure_json_value(dict(self.metadata), "metadata")
        if not isinstance(normalized_metadata, dict):
            raise ContractValidationError("metadata must be a JSON object")
        object.__setattr__(self, "metadata", MappingProxyType(normalized_metadata))

    @property
    def text_hash(self) -> str:
        return sha256_text(self.text)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "route": self.route,
            "trigger": self.trigger,
            "sender_id": self.sender_id,
            "text": self.text,
            "reply_to": self.reply_to,
            "media": [item.to_dict() for item in self.media],
            "received_at": self.received_at,
            "batch_started_at": self.batch_started_at,
            "metadata": dict(self.metadata),
        }

    def structural_summary(self) -> dict[str, JsonValue]:
        """A log-safe representation that never contains message body text."""

        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "route": self.route,
            "trigger": self.trigger,
            "sender_id": self.sender_id,
            "reply_to": self.reply_to,
            "text_length": len(self.text),
            "text_hash": self.text_hash,
            "media": [item.structural_summary() for item in self.media],
            "received_at": self.received_at,
            "batch_started_at": self.batch_started_at,
            "metadata_keys": sorted(self.metadata),
        }

    def structural_fingerprint(self) -> str:
        return structural_fingerprint(self.structural_summary())

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TurnEnvelope":
        if not isinstance(value, dict):
            raise ContractValidationError("turn must be represented by a dictionary")
        raw_media = value.get("media", ())
        if not isinstance(raw_media, (list, tuple)):
            raise ContractValidationError("media must be a list")
        return cls(
            request_id=value.get("request_id", ""),
            session_id=value.get("session_id", ""),
            route=value.get("route", ""),
            trigger=value.get("trigger", ""),
            sender_id=value.get("sender_id", ""),
            text=value.get("text", ""),
            reply_to=value.get("reply_to"),
            media=tuple(MediaRef.from_dict(item) for item in raw_media),
            received_at=value.get("received_at", 0),
            batch_started_at=value.get("batch_started_at", 0),
            metadata=value.get("metadata", {}),
        )
