"""Conservative effect policy for future tool-loop orchestration."""

from __future__ import annotations

from dataclasses import dataclass

from .validation import (
    ContractValidationError,
    JsonValue,
    require_bool,
    require_non_negative_int,
    require_positive_int,
    require_source_name,
)

ALLOWED_TOOL_EFFECTS = frozenset({"pure", "read", "write", "send"})


@dataclass(frozen=True, slots=True)
class ToolExecutionPolicy:
    """Describe tool side effects before the future executor is introduced."""

    effect: str
    parallel_safe: bool
    retry_safe: bool
    cache_ttl_seconds: int
    max_result_chars: int

    def __post_init__(self) -> None:
        effect = require_source_name(self.effect, "effect").lower()
        if effect not in ALLOWED_TOOL_EFFECTS:
            allowed = ", ".join(sorted(ALLOWED_TOOL_EFFECTS))
            raise ContractValidationError(f"effect must be one of: {allowed}")
        object.__setattr__(self, "effect", effect)
        object.__setattr__(
            self, "parallel_safe", require_bool(self.parallel_safe, "parallel_safe")
        )
        object.__setattr__(self, "retry_safe", require_bool(self.retry_safe, "retry_safe"))
        object.__setattr__(
            self,
            "cache_ttl_seconds",
            require_non_negative_int(self.cache_ttl_seconds, "cache_ttl_seconds"),
        )
        object.__setattr__(
            self,
            "max_result_chars",
            require_positive_int(self.max_result_chars, "max_result_chars"),
        )
        if self.effect in {"write", "send"}:
            if self.parallel_safe or self.retry_safe or self.cache_ttl_seconds:
                raise ContractValidationError(
                    "write/send tools must be serial, non-retryable and non-cacheable"
                )

    @classmethod
    def conservative_default(cls, max_result_chars: int = 4_000) -> "ToolExecutionPolicy":
        """Unknown tools default to a serial, uncacheable write policy."""

        return cls(
            effect="write",
            parallel_safe=False,
            retry_safe=False,
            cache_ttl_seconds=0,
            max_result_chars=max_result_chars,
        )

    @property
    def is_cacheable(self) -> bool:
        return self.effect in {"pure", "read"} and self.cache_ttl_seconds > 0

    @property
    def can_run_in_parallel(self) -> bool:
        return self.effect in {"pure", "read"} and self.parallel_safe

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "effect": self.effect,
            "parallel_safe": self.parallel_safe,
            "retry_safe": self.retry_safe,
            "cache_ttl_seconds": self.cache_ttl_seconds,
            "max_result_chars": self.max_result_chars,
        }
