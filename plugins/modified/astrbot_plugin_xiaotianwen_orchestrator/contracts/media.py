"""Stable media references shared by image, meme and astronomy paths."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .validation import (
    ContractValidationError,
    JsonValue,
    require_finite_timestamp,
    require_identifier,
    require_non_negative_int,
    require_optional_string,
    require_source_name,
    sha256_text,
)


@dataclass(frozen=True, slots=True)
class MediaRef:
    """A serializable reference to one media item in its original message order.

    ``local_path`` and ``source_url`` are optional recovery hints, never the
    only identity. ``media_id`` stays stable even after cached files expire.
    """

    media_id: str
    kind: str
    message_id: str
    sender_id: str
    order: int
    description: str | None = None
    local_path: str | None = None
    source_url: str | None = None
    artifacts: tuple[str, ...] = field(default_factory=tuple)
    content_hash: str | None = None
    created_at: float | None = None
    expires_at: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "media_id", require_identifier(self.media_id, "media_id"))
        object.__setattr__(self, "kind", require_source_name(self.kind, "kind").lower())
        object.__setattr__(self, "message_id", require_identifier(self.message_id, "message_id"))
        object.__setattr__(self, "sender_id", require_identifier(self.sender_id, "sender_id"))
        object.__setattr__(self, "order", require_non_negative_int(self.order, "order"))
        object.__setattr__(
            self, "description", require_optional_string(self.description, "description")
        )
        object.__setattr__(
            self, "local_path", require_optional_string(self.local_path, "local_path")
        )
        object.__setattr__(
            self, "source_url", require_optional_string(self.source_url, "source_url")
        )
        object.__setattr__(
            self, "content_hash", require_optional_string(self.content_hash, "content_hash")
        )
        if self.created_at is not None:
            object.__setattr__(
                self,
                "created_at",
                require_finite_timestamp(self.created_at, "created_at"),
            )
        if self.expires_at is not None:
            object.__setattr__(
                self,
                "expires_at",
                require_finite_timestamp(self.expires_at, "expires_at"),
            )
        if self.created_at is not None and self.expires_at is not None:
            if self.expires_at < self.created_at:
                raise ContractValidationError("expires_at must not precede created_at")
        try:
            raw_artifacts = tuple(self.artifacts)
        except TypeError as exc:
            raise ContractValidationError("artifacts must be an iterable of strings") from exc
        normalized_artifacts: list[str] = []
        for index, artifact in enumerate(raw_artifacts):
            normalized_artifacts.append(
                require_optional_string(artifact, f"artifacts[{index}]") or ""
            )
        if any(not artifact for artifact in normalized_artifacts):
            raise ContractValidationError("artifacts must not contain empty values")
        object.__setattr__(self, "artifacts", tuple(normalized_artifacts))

    @property
    def description_hash(self) -> str | None:
        return sha256_text(self.description) if self.description else None

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "media_id": self.media_id,
            "kind": self.kind,
            "message_id": self.message_id,
            "sender_id": self.sender_id,
            "order": self.order,
            "description": self.description,
            "local_path": self.local_path,
            "source_url": self.source_url,
            "artifacts": list(self.artifacts),
            "content_hash": self.content_hash,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }

    def structural_summary(self) -> dict[str, JsonValue]:
        """Safe diagnostic representation; it deliberately omits paths/URLs/text."""

        return {
            "media_id": self.media_id,
            "kind": self.kind,
            "message_id": self.message_id,
            "sender_id": self.sender_id,
            "order": self.order,
            "description_length": len(self.description or ""),
            "description_hash": self.description_hash,
            "artifact_count": len(self.artifacts),
            "has_local_path": self.local_path is not None,
            "has_source_url": self.source_url is not None,
            "content_hash": self.content_hash,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "MediaRef":
        if not isinstance(value, dict):
            raise ContractValidationError("media must be represented by a dictionary")
        raw_artifacts = value.get("artifacts", ())
        if not isinstance(raw_artifacts, (list, tuple)):
            raise ContractValidationError("artifacts must be a list")
        return cls(
            media_id=value.get("media_id", ""),
            kind=value.get("kind", ""),
            message_id=value.get("message_id", ""),
            sender_id=value.get("sender_id", ""),
            order=value.get("order", 0),
            description=value.get("description"),
            local_path=value.get("local_path"),
            source_url=value.get("source_url"),
            artifacts=tuple(raw_artifacts),
            content_hash=value.get("content_hash"),
            created_at=value.get("created_at"),
            expires_at=value.get("expires_at"),
        )
