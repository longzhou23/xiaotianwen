"""Stable service boundary for a future Web panel or isolated host."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..contracts import TurnEnvelope
from ..ingress import ShadowTurnCoordinator
from .provider_registry import ContextProviderRegistry


@dataclass(frozen=True, slots=True)
class ServiceSnapshot:
    """Read-only state; no manager or platform object crosses this boundary."""

    assembler_owner: str
    pending_turns: int
    active_timer_count: int
    terminal_turns: int
    provider_sources: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "assembler_owner": self.assembler_owner,
            "pending_turns": self.pending_turns,
            "active_timer_count": self.active_timer_count,
            "terminal_turns": self.terminal_turns,
            "provider_sources": list(self.provider_sources),
        }


class OrchestratorService:
    """Small facade that Web/API code can consume without importing managers."""

    def __init__(self, *, coordinator: ShadowTurnCoordinator | None = None, providers: ContextProviderRegistry | None = None) -> None:
        self.coordinator = coordinator or ShadowTurnCoordinator(enabled=True)
        self.providers = providers or ContextProviderRegistry()

    def ingest(self, event: object, *, now: float | None = None) -> object:
        return self.coordinator.ingest_event(event, now=now)

    def ingest_envelope(self, turn: TurnEnvelope, *, now: float | None = None) -> object:
        return self.coordinator.ingest_envelope(turn, now=now)

    def ready(self, *, now: float | None = None) -> tuple[object, ...]:
        return self.coordinator.flush_ready(now=now)

    def snapshot(self) -> ServiceSnapshot:
        return ServiceSnapshot(
            assembler_owner=self.providers.ASSEMBLER_OWNER,
            pending_turns=self.coordinator.pending_count,
            active_timer_count=self.coordinator.active_timer_count,
            terminal_turns=len(self.coordinator.terminal_turns),
            provider_sources=tuple(item.source for item in self.providers.registrations()),
        )
