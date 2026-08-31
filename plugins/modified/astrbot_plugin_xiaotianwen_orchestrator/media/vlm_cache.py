"""Content-addressed VLM description cache; callers still own the VLM call."""

from __future__ import annotations

from dataclasses import dataclass

from ..contracts.validation import (
    ContractValidationError,
    require_finite_timestamp,
    require_non_empty_string,
    sha256_text,
)


@dataclass(frozen=True, slots=True)
class VlmCacheKey:
    content_hash: str
    provider: str
    prompt_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "content_hash", require_non_empty_string(self.content_hash, "content_hash"))
        object.__setattr__(self, "provider", require_non_empty_string(self.provider, "provider"))
        object.__setattr__(
            self,
            "prompt_version",
            require_non_empty_string(self.prompt_version, "prompt_version"),
        )

    @property
    def fingerprint(self) -> str:
        return sha256_text(f"{self.content_hash}\n{self.provider}\n{self.prompt_version}")


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    description: str
    stored_at: float
    expires_at: float | None


class VlmDescriptionCache:
    def __init__(self, *, max_entries: int = 512) -> None:
        if type(max_entries) is not int or max_entries <= 0:
            raise ContractValidationError("max_entries must be positive")
        self.max_entries = max_entries
        self._entries: dict[VlmCacheKey, _CacheEntry] = {}

    def get(self, key: VlmCacheKey, *, now: float) -> str | None:
        current = require_finite_timestamp(now, "now")
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at is not None and current >= entry.expires_at:
            self._entries.pop(key, None)
            return None
        return entry.description

    def put(
        self,
        key: VlmCacheKey,
        description: str,
        *,
        now: float,
        ttl_seconds: float | None = None,
    ) -> None:
        if not isinstance(key, VlmCacheKey):
            raise ContractValidationError("key must be VlmCacheKey")
        text = require_non_empty_string(description, "description")
        stored_at = require_finite_timestamp(now, "now")
        expires_at: float | None = None
        if ttl_seconds is not None:
            ttl = require_finite_timestamp(ttl_seconds, "ttl_seconds")
            if ttl <= 0:
                raise ContractValidationError("ttl_seconds must be positive")
            expires_at = stored_at + ttl
        self._entries.pop(key, None)
        self._entries[key] = _CacheEntry(text, stored_at, expires_at)
        while len(self._entries) > self.max_entries:
            self._entries.pop(next(iter(self._entries)))

    def cleanup(self, *, now: float) -> int:
        current = require_finite_timestamp(now, "now")
        expired = [
            key
            for key, value in self._entries.items()
            if value.expires_at is not None and current >= value.expires_at
        ]
        for key in expired:
            self._entries.pop(key, None)
        return len(expired)

    def structural_summary(self) -> dict[str, object]:
        return {
            "entry_count": len(self._entries),
            "keys": [key.fingerprint for key in self._entries],
        }
