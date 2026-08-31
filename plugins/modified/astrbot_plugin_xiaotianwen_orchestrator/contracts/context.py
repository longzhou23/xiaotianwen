"""Context provider output contract and log-safe fingerprints."""

from __future__ import annotations

from dataclasses import dataclass

from .validation import (
    ContractValidationError,
    JsonValue,
    require_bool,
    require_non_negative_int,
    require_non_empty_string,
    require_source_name,
    sha256_text,
)

ALLOWED_CACHE_SCOPES = frozenset({"none", "request", "session", "shared"})


@dataclass(frozen=True, slots=True)
class ContextSection:
    """One bounded context contribution, not a mutable ProviderRequest hook."""

    source: str
    priority: int
    content: str
    max_chars: int
    cache_scope: str
    version: str
    sensitive: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", require_source_name(self.source, "source"))
        if type(self.priority) is not int:
            raise ContractValidationError("priority must be an integer")
        # The assembler must preserve stable persona/tool prefixes byte for
        # byte. Validate that content has meaningful text but keep its original
        # leading/trailing whitespace rather than normalizing it here.
        if not isinstance(self.content, str) or not self.content.strip():
            raise ContractValidationError("content must be a non-empty string")
        max_chars = require_non_negative_int(self.max_chars, "max_chars")
        if max_chars == 0:
            raise ContractValidationError("max_chars must be greater than zero")
        object.__setattr__(self, "max_chars", max_chars)
        cache_scope = require_source_name(self.cache_scope, "cache_scope").lower()
        if cache_scope not in ALLOWED_CACHE_SCOPES:
            allowed = ", ".join(sorted(ALLOWED_CACHE_SCOPES))
            raise ContractValidationError(f"cache_scope must be one of: {allowed}")
        object.__setattr__(self, "cache_scope", cache_scope)
        object.__setattr__(self, "version", require_non_empty_string(self.version, "version"))
        object.__setattr__(self, "sensitive", require_bool(self.sensitive, "sensitive"))

    @property
    def content_hash(self) -> str:
        return sha256_text(self.content)

    def bounded_content(self, limit: int | None = None) -> str:
        """Return content bounded without adding non-deterministic truncation prose."""

        bound = self.max_chars if limit is None else min(self.max_chars, max(0, limit))
        return self.content[:bound]

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "source": self.source,
            "priority": self.priority,
            "content": self.content,
            "max_chars": self.max_chars,
            "cache_scope": self.cache_scope,
            "version": self.version,
            "sensitive": self.sensitive,
        }

    def structural_summary(self) -> dict[str, JsonValue]:
        """Return diagnostics without the potentially private section body."""

        return {
            "source": self.source,
            "priority": self.priority,
            "content_length": len(self.content),
            "content_hash": self.content_hash,
            "max_chars": self.max_chars,
            "cache_scope": self.cache_scope,
            "version": self.version,
            "sensitive": self.sensitive,
        }
