"""Stable ordering and route-aware context budget policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from ..contracts.validation import ContractValidationError, require_non_negative_int


DEFAULT_SOURCE_PRIORITIES = MappingProxyType(
    {
        "persona": 10,
        "tool_rules": 20,
        "short_history": 30,
        "shared_context": 35,
        "context_aware": 40,
        "iris_l2": 50,
        "iris_l3": 60,
        "iris_profile": 70,
        "iris_affection": 80,
        "image_context_pool": 90,
        "current_message": 100,
    }
)


@dataclass(frozen=True, slots=True)
class ContextAssemblyPolicy:
    """Pure policy used by shadow tests; it makes no model or provider calls."""

    total_budget_chars: int = 12_000
    separator: str = "\n\n"
    fixed_prefix_sources: frozenset[str] = frozenset({"persona", "tool_rules"})
    source_caps: Mapping[str, int] = field(default_factory=dict)
    decision_excluded_sources: frozenset[str] = frozenset(
        {"iris_l1", "iris_l2", "iris_l3", "iris_profile", "iris_affection", "tool_rules"}
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "total_budget_chars",
            require_non_negative_int(self.total_budget_chars, "total_budget_chars"),
        )
        if not isinstance(self.separator, str):
            raise ContractValidationError("separator must be a string")
        if not isinstance(self.source_caps, Mapping):
            raise ContractValidationError("source_caps must be a mapping")
        normalized_caps: dict[str, int] = {}
        for source, limit in self.source_caps.items():
            if not isinstance(source, str) or not source:
                raise ContractValidationError("source_caps keys must be non-empty strings")
            normalized_caps[source] = require_non_negative_int(
                limit, f"source_caps.{source}"
            )
        object.__setattr__(self, "source_caps", MappingProxyType(normalized_caps))

    def cap_for(self, source: str, section_cap: int) -> int:
        return min(section_cap, self.source_caps.get(source, section_cap))

    def excludes(self, source: str, route: str) -> bool:
        # Decision/preflight calls should retain stable persona and current
        # scene but must not accidentally carry main-reply-only Iris/profile
        # data or an entire tool manifest.
        return route == "decision" and source in self.decision_excluded_sources
