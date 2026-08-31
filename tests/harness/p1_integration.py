"""Local P1 fake integration runner.

This runner deliberately exercises only the disposable FakeAstrBotRuntime.
It is useful for the CLI/report/UI contract while the real AstrBot instance
gate remains a separate, explicitly authorized operation.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .matrix import PluginTestResult


@dataclass(frozen=True, slots=True)
class P1FakeReport:
    checks: tuple[PluginTestResult, ...]
    observations: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "runtime": "FakeAstrBotRuntime",
            "checks": [item.to_dict() for item in self.checks],
            "observation_count": len(self.observations),
            "capture_modes": sorted({str(item.get("capture_mode", "UNKNOWN")) for item in self.observations}),
        }


def _event(message_id: str) -> dict[str, object]:
    return {
        "message_id": message_id,
        "user_id": "p1-user",
        "group_id": "p1-group",
        "raw_message": "P1 synthetic message",
        "message": [{"type": "text", "data": {"text": "P1 synthetic message"}}],
    }


def _check(identifier: str, started: float, passed: bool, output: str, reason: str | None = None) -> PluginTestResult:
    return PluginTestResult(
        identifier=identifier,
        status="PASSED" if passed else "FAILED",
        duration_ms=int((time.monotonic() - started) * 1_000),
        reason=None if passed else reason or "P1 fake contract assertion failed",
        returncode=0 if passed else 1,
        command=("in-process", "FakeAstrBotRuntime"),
        output=output,
    )


def run_p1_fake_suite(repository_root: Path) -> P1FakeReport:
    """Run P1 fake scenarios without sockets, Docker, credentials or files."""

    modified_root = repository_root / "plugins" / "modified"
    if str(modified_root) not in sys.path:
        sys.path.insert(0, str(modified_root))
    from astrbot_plugin_xiaotianwen_orchestrator.integration import FakeAstrBotRuntime
    from astrbot_plugin_xiaotianwen_orchestrator.ingress import OrchestratorMode

    checks: list[PluginTestResult] = []
    all_observations: list[dict[str, Any]] = []

    started = time.monotonic()
    final = FakeAstrBotRuntime(run_id="p1-cli-final", provider_outcome="stream")
    final.submit_event(_event("p1-message-001"), now=100.0)
    final.flush(now=103.0)
    final_records = final.observations()
    passed = (
        len(final.provider.calls) == 1
        and any(item["kind"] == "request.chunk" for item in final_records)
        and any(item["kind"] == "delivery.completed" for item in final_records)
    )
    checks.append(_check("p1-fake-stream-delivery", started, passed, "stream/usage/delivery"))
    all_observations.extend(final_records)

    started = time.monotonic()
    tool = FakeAstrBotRuntime(run_id="p1-cli-tool", provider_outcome="tool")
    tool.submit_event(_event("p1-message-002"), now=200.0)
    tool.flush(now=203.0)
    tool_records = tool.observations()
    provider_roles = [str(item["role"]) for item in tool.provider.calls]
    passed = provider_roles == ["main_reply", "tool_continuation"] and any(item["kind"] == "tool.completed" for item in tool_records)
    checks.append(_check("p1-fake-tool-continuation", started, passed, "parent request and call_id correlation"))
    all_observations.extend(tool_records)

    started = time.monotonic()
    shadow = FakeAstrBotRuntime(run_id="p1-cli-shadow", mode=OrchestratorMode.SHADOW)
    shadow.submit_event(_event("p1-message-003"), now=300.0)
    shadow.flush(now=303.0)
    shadow_records = shadow.observations()
    passed = not shadow.provider.calls and any(item["kind"] == "turn.shadow_skipped" for item in shadow_records)
    checks.append(_check("p1-fake-shadow-no-dispatch", started, passed, "shadow has no provider or delivery"))
    all_observations.extend(shadow_records)

    started = time.monotonic()
    disconnected = FakeAstrBotRuntime(run_id="p1-cli-disconnected")
    disconnected.onebot.disconnect(timestamp=400.0)
    disconnected.submit_event(_event("p1-message-004"), now=401.0)
    disconnected_records = disconnected.observations()
    passed = any(item["kind"] == "onebot.event.dropped" and item["capture_mode"] == "NOT_CONNECTED" for item in disconnected_records)
    checks.append(_check("p1-fake-not-connected", started, passed, "disconnected source is explicit"))
    all_observations.extend(disconnected_records)

    return P1FakeReport(tuple(checks), tuple(all_observations))
