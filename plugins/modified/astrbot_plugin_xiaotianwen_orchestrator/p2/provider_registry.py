"""Structured context-provider registration for the P2 migration.

Providers return ``ContextSection`` values from supplied snapshots.  They do
not receive or mutate AstrBot ``ProviderRequest`` objects.  The registry owns
source uniqueness and the one call to the shared assembler.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ..context import ContextAssembler, ContextAssemblyResult
from ..contracts import ContextSection, TurnEnvelope
from ..contracts.validation import ContractValidationError, require_positive_int, require_source_name


ProviderFunction = Callable[[TurnEnvelope, Mapping[str, object]], ContextSection | Iterable[ContextSection]]


@dataclass(frozen=True, slots=True)
class ProviderRegistration:
    name: str
    source: str
    provider: ProviderFunction
    enabled: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", require_source_name(self.name, "provider name"))
        object.__setattr__(self, "source", require_source_name(self.source, "provider source"))
        if not callable(self.provider):
            raise ContractValidationError("provider must be callable")
        if type(self.enabled) is not bool:
            raise ContractValidationError("provider enabled must be boolean")


@dataclass(frozen=True, slots=True)
class ProviderFailure:
    name: str
    source: str
    error_class: str

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "source": self.source, "error_class": self.error_class}


@dataclass(frozen=True, slots=True)
class ProviderCollection:
    sections: tuple[ContextSection, ...]
    failures: tuple[ProviderFailure, ...]
    dropped: tuple[tuple[str, str], ...]

    def structural_summary(self) -> dict[str, object]:
        return {
            "section_count": len(self.sections),
            "sources": [section.source for section in self.sections],
            "sections": [section.structural_summary() for section in self.sections],
            "failures": [failure.to_dict() for failure in self.failures],
            "dropped": [{"source": source, "reason": reason} for source, reason in self.dropped],
        }


class ContextProviderRegistry:
    """One owner for structured provider output and final context assembly."""

    ASSEMBLER_OWNER = "xiaotianwen_context_assembler"

    def __init__(self, *, assembler: ContextAssembler | None = None, max_providers: int = 32) -> None:
        self.assembler = assembler or ContextAssembler()
        self.max_providers = require_positive_int(max_providers, "max_providers")
        self._registrations: list[ProviderRegistration] = []

    def register(self, registration: ProviderRegistration) -> None:
        if not isinstance(registration, ProviderRegistration):
            raise ContractValidationError("registry accepts ProviderRegistration values")
        if len(self._registrations) >= self.max_providers:
            raise ContractValidationError("provider registration limit reached")
        if any(item.name == registration.name for item in self._registrations):
            raise ContractValidationError(f"provider name already registered: {registration.name}")
        if any(item.source == registration.source for item in self._registrations):
            raise ContractValidationError(f"provider source already registered: {registration.source}")
        self._registrations.append(registration)

    def registrations(self) -> tuple[ProviderRegistration, ...]:
        return tuple(self._registrations)

    def collect(self, turn: TurnEnvelope, snapshots: Mapping[str, object] | None = None) -> ProviderCollection:
        if not isinstance(turn, TurnEnvelope):
            raise ContractValidationError("context collection requires TurnEnvelope")
        if snapshots is not None and not isinstance(snapshots, Mapping):
            raise ContractValidationError("context snapshots must be a mapping")
        supplied = snapshots or {}
        sections: list[ContextSection] = []
        failures: list[ProviderFailure] = []
        dropped: list[tuple[str, str]] = []
        seen_hashes: set[str] = set()
        for registration in self._registrations:
            if not registration.enabled:
                dropped.append((registration.source, "disabled"))
                continue
            try:
                produced = registration.provider(turn, supplied)
                values = (produced,) if isinstance(produced, ContextSection) else tuple(produced)
                for section in values:
                    if not isinstance(section, ContextSection):
                        raise ContractValidationError("provider returned a non-ContextSection value")
                    if section.source != registration.source:
                        raise ContractValidationError(
                            f"provider {registration.name} returned source {section.source!r}, expected {registration.source!r}"
                        )
                    if section.content_hash in seen_hashes:
                        dropped.append((section.source, "duplicate_content"))
                        continue
                    seen_hashes.add(section.content_hash)
                    sections.append(section)
            except Exception as exc:
                failures.append(ProviderFailure(registration.name, registration.source, type(exc).__name__))
        return ProviderCollection(tuple(sections), tuple(failures), tuple(dropped))

    def assemble(self, turn: TurnEnvelope, snapshots: Mapping[str, object] | None = None) -> tuple[ProviderCollection, ContextAssemblyResult]:
        collection = self.collect(turn, snapshots)
        return collection, self.assembler.assemble(collection.sections, route=turn.route)
