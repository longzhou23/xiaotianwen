"""AstrBot-facing shell for the P1/P2 shadow library.

It intentionally registers no ingress or LLM hook in P1.  Installation alone
therefore cannot alter a production response, and later canary work has one
explicit integration point instead of silently changing existing plugins.
"""

from __future__ import annotations

from typing import Any

from astrbot import logger
from astrbot.api import star

from .context import ContextAssembler, ContextAssemblyPolicy, SharedContextAdapter
from .ingress import ShadowTurnCoordinator
from .integration import AstrBotObservationAdapter, ObservationAdapter, RuntimeObservationStore


class Main(star.Star):
    """Dormant plugin shell; P1/P2 expose only in-memory local helpers."""

    def __init__(self, context: star.Context, config: Any | None = None) -> None:
        super().__init__(context)
        self._config = config
        self.shadow_enabled = self._cfg_bool("shadow_enabled", False)
        self.shared_context_enabled = self._cfg_bool("shared_context_enabled", False)
        self.observation_capture_text = self._cfg_bool("observation_capture_text", False)
        self.observation_retention_seconds = max(
            60.0,
            self._cfg_float("observation_retention_seconds", 86_400.0),
        )
        # This is an explicit adapter target for a future isolated test
        # instance.  It is intentionally not registered with AstrBot here:
        # merely installing the plugin must not observe or rewrite production
        # requests.
        self.observation_store = RuntimeObservationStore(
            "orchestrator-instance",
            retention_seconds=self.observation_retention_seconds,
        )
        self.observation_adapter = ObservationAdapter(
            self.observation_store,
            source="ORCHESTRATOR_ADAPTER",
        )
        self.astrbot_observation_adapter = AstrBotObservationAdapter(
            self.observation_adapter
        )
        self.coordinator = ShadowTurnCoordinator(
            enabled=self.shadow_enabled,
            quiet_window_seconds=max(0.1, self._cfg_float("quiet_window_seconds", 3.0)),
            dedup_ttl_seconds=max(1.0, self._cfg_float("dedup_ttl_seconds", 30.0)),
        )
        self.context_assembler = ContextAssembler(
            ContextAssemblyPolicy(
                total_budget_chars=max(0, self._cfg_int("context_budget_chars", 12_000))
            )
        )
        self.shared_context_adapter = SharedContextAdapter(
            enabled=self.shared_context_enabled
        )

    def _cfg(self, key: str, default: Any) -> Any:
        try:
            return self._config.get(key, default) if self._config is not None else default
        except Exception:
            return default

    def _cfg_bool(self, key: str, default: bool) -> bool:
        value = self._cfg(key, default)
        if type(value) is bool:
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _cfg_float(self, key: str, default: float) -> float:
        try:
            return float(self._cfg(key, default))
        except (TypeError, ValueError):
            return default

    def _cfg_int(self, key: str, default: int) -> int:
        try:
            return int(self._cfg(key, default))
        except (TypeError, ValueError):
            return default

    async def initialize(self) -> None:
        logger.info(
            "[XiaotianwenOrchestrator] P1/P2 local shadow library loaded; "
            f"shadow_enabled={self.shadow_enabled}, "
            f"shared_context_enabled={self.shared_context_enabled}; "
            "explicit observation adapter is prepared but not connected; "
            "no event hook, LLM request, tool call, timer or delivery owner is active"
        )

    async def terminate(self) -> None:
        self.coordinator.disable()
        self.observation_store.clear()
