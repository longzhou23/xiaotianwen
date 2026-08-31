"""Deterministic context assembly and content-redacted old/new comparison."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..contracts import ContextSection
from ..contracts.validation import ContractValidationError, JsonValue, sha256_text, structural_fingerprint
from .budgets import ContextAssemblyPolicy


@dataclass(frozen=True, slots=True)
class ContextAssemblyResult:
    """Actual assembled payload plus safe metadata for shadow observations."""

    route: str
    payload: str
    sections: tuple[ContextSection, ...]
    dropped: tuple[tuple[str, str], ...]
    budget_chars: int
    content_chars: int
    overflow_chars: int

    @property
    def fingerprint(self) -> str:
        return structural_fingerprint(
            {
                "route": self.route,
                "sections": [section.structural_summary() for section in self.sections],
                "payload_hash": sha256_text(self.payload),
                "payload_length": len(self.payload),
            }
        )

    def structural_summary(self) -> dict[str, JsonValue]:
        return {
            "route": self.route,
            "section_count": len(self.sections),
            "sources": [section.source for section in self.sections],
            "sections": [section.structural_summary() for section in self.sections],
            "dropped": [{"source": source, "reason": reason} for source, reason in self.dropped],
            "budget_chars": self.budget_chars,
            "content_chars": self.content_chars,
            "payload_length": len(self.payload),
            "payload_hash": sha256_text(self.payload),
            "overflow_chars": self.overflow_chars,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True, slots=True)
class PayloadStructuralDiff:
    """Old/new payload comparison safe for normal production observability."""

    legacy_length: int
    legacy_hash: str
    new_length: int
    new_hash: str
    byte_equal: bool
    new_sources: tuple[str, ...]
    dropped_count: int
    result_fingerprint: str

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "legacy_length": self.legacy_length,
            "legacy_hash": self.legacy_hash,
            "new_length": self.new_length,
            "new_hash": self.new_hash,
            "byte_equal": self.byte_equal,
            "new_sources": list(self.new_sources),
            "dropped_count": self.dropped_count,
            "result_fingerprint": self.result_fingerprint,
        }


class ContextAssembler:
    """Apply one ordering/budget/dedup owner without mutating ProviderRequest."""

    def __init__(self, policy: ContextAssemblyPolicy | None = None) -> None:
        self.policy = policy or ContextAssemblyPolicy()

    def assemble(
        self,
        sections: Iterable[ContextSection],
        *,
        route: str = "chat",
    ) -> ContextAssemblyResult:
        if not isinstance(route, str) or not route.strip():
            raise ContractValidationError("route must be a non-empty string")
        candidates: list[tuple[int, ContextSection]] = []
        for index, section in enumerate(sections):
            if not isinstance(section, ContextSection):
                raise ContractValidationError("assembler accepts only ContextSection values")
            candidates.append((index, section))
        candidates.sort(key=lambda item: (item[1].priority, item[0]))

        selected: list[ContextSection] = []
        dropped: list[tuple[str, str]] = []
        seen_sources: set[str] = set()
        for _, section in candidates:
            if self.policy.excludes(section.source, route):
                dropped.append((section.source, "route_excluded"))
                continue
            if section.source in seen_sources:
                dropped.append((section.source, "duplicate_source"))
                continue
            seen_sources.add(section.source)
            selected.append(section)

        rendered: list[tuple[ContextSection, str]] = []
        used_chars = 0
        for section in selected:
            source_cap = self.policy.cap_for(section.source, section.max_chars)
            candidate = section.bounded_content(source_cap)
            separator_chars = len(self.policy.separator) if rendered else 0
            remaining = self.policy.total_budget_chars - used_chars - separator_chars
            if section.source in self.policy.fixed_prefix_sources:
                # Stable prefixes are never additionally shortened by the total
                # budget. If configured caps themselves are too small that is a
                # configuration error to fix, not a hidden runtime rewrite.
                chosen = candidate
            elif remaining <= 0:
                dropped.append((section.source, "total_budget"))
                continue
            else:
                chosen = candidate[:remaining]
                if not chosen:
                    dropped.append((section.source, "total_budget"))
                    continue
            rendered.append((section, chosen))
            used_chars += separator_chars + len(chosen)

        payload = self.policy.separator.join(content for _, content in rendered)
        included_sections = tuple(section for section, _ in rendered)
        overflow_chars = max(0, used_chars - self.policy.total_budget_chars)
        return ContextAssemblyResult(
            route=route,
            payload=payload,
            sections=included_sections,
            dropped=tuple(dropped),
            budget_chars=self.policy.total_budget_chars,
            content_chars=used_chars,
            overflow_chars=overflow_chars,
        )

    @staticmethod
    def compare_legacy_payload(
        legacy_payload: str,
        result: ContextAssemblyResult,
    ) -> PayloadStructuralDiff:
        if not isinstance(legacy_payload, str):
            raise ContractValidationError("legacy_payload must be a string")
        return PayloadStructuralDiff(
            legacy_length=len(legacy_payload),
            legacy_hash=sha256_text(legacy_payload),
            new_length=len(result.payload),
            new_hash=sha256_text(result.payload),
            byte_equal=legacy_payload == result.payload,
            new_sources=tuple(section.source for section in result.sections),
            dropped_count=len(result.dropped),
            result_fingerprint=result.fingerprint,
        )
