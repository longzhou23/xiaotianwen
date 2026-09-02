"""Deterministic local replay for sanitized or private historical chat fixtures."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .contracts import CanonicalExperience, EntityReference, Perspective, ResolvedEvent
from .iris_adapter import CognitiveRuntime


class LocalHistoricalReplayRunner:
    """Runs a fixture without a Provider, LLM, network call, or Iris writeback."""

    owner = "Cognitive Replay Runner"

    def __init__(self) -> None:
        """Always create an isolated runtime; replay cannot receive the live singleton."""
        self.runtime = CognitiveRuntime(record_traces=False)

    @staticmethod
    def load(path: str | Path) -> tuple[Mapping[str, Any], ...]:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        cases = payload.get("cases")
        if not isinstance(cases, list):
            raise ValueError("historical replay fixture requires a 'cases' list")
        return tuple(case for case in cases if isinstance(case, dict))

    @staticmethod
    def _experience(case: Mapping[str, Any]) -> CanonicalExperience:
        event_id = str(case.get("event_id") or "replay:missing-id")
        session_id = str(case.get("session_id") or "replay:unknown-session")
        actor_id = str(case.get("actor_entity") or "person:replay:unknown")
        actor = EntityReference(actor_id, "replay_fixture", 1.0, ("local historical replay",))
        mentioned = []
        for entity_id in case.get("mentioned_entities", []) or []:
            if isinstance(entity_id, str) and entity_id:
                mentioned.append(
                    EntityReference(entity_id, "replay_fixture", 1.0, ("local historical replay",))
                )
        reply_to = None
        reply_id = case.get("reply_to_entity")
        if isinstance(reply_id, str) and reply_id:
            reply_to = EntityReference(reply_id, "replay_fixture", 1.0, ("local historical replay",))
        event = ResolvedEvent(
            event_id=event_id,
            source=str(case.get("source") or "replay"),
            occurred_at=datetime.fromisoformat(str(case.get("occurred_at") or "2026-09-02T00:00:00+00:00")).astimezone(timezone.utc),
            session_id=session_id,
            mode=str(case.get("mode") or "casual_group_chat"),
            content=str(case.get("content") or ""),
            actor=actor,
            mentioned_entities=tuple(mentioned),
            reply_to=reply_to,
        )
        return CanonicalExperience(
            id=f"experience:{event_id}",
            event=event,
            subject=actor,
            perspective=Perspective.INTERPERSONAL,
            provenance=("local_historical_replay",),
        )

    def run_case(self, case: Mapping[str, Any]) -> dict[str, Any]:
        experience = self._experience(case)
        result = self.runtime.run_behavior(experience)
        legacy = case.get("legacy_observation")
        legacy = legacy if isinstance(legacy, Mapping) else {}
        actually_replied = bool(legacy.get("actually_replied", False))
        output = str(legacy.get("output") or "") if actually_replied else ""
        execution = self.runtime.observe_host_output(
            result, output, legacy_fallthrough=True
        )
        return {
            "event_id": experience.event.event_id,
            "legacy_observation": {
                "actually_replied": actually_replied,
                "output": output,
            },
            "cognitive": {
                "runtime_mode": execution.trace.runtime_mode.value,
                "trigger": execution.trace.trigger.should_start_loop,
                "participation": execution.trace.participation.decision.value if execution.trace.participation else None,
                "intent": execution.trace.intent.action.value if execution.trace.intent and execution.trace.intent.action else None,
                "grounding": {
                    "status": execution.trace.grounding.status.value,
                    "enforcement": execution.trace.grounding.enforcement.value,
                } if execution.trace.grounding else None,
                "terminal_result": execution.trace.exit_reason.value if execution.trace.exit_reason else "ACTION_PROPOSED",
            },
            "comparison": {"divergence": execution.comparison.divergence.value},
        }

    def run_file(self, path: str | Path) -> tuple[dict[str, Any], ...]:
        return tuple(self.run_case(case) for case in self.load(path))
