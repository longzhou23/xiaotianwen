"""Deterministic, offline P0 replay runner built on the shadow turn coordinator."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .redact import find_secret_hits, redact_text, redact_value
from .path_policy import RepositoryPathPolicy
from .schema import ensure_valid, validate_functional_catalog, validate_injection_catalog
from .trace import TraceStore, fingerprint_text


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MODIFIED_PLUGIN_ROOT = REPOSITORY_ROOT / "plugins" / "modified"
if str(MODIFIED_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(MODIFIED_PLUGIN_ROOT))

from astrbot_plugin_xiaotianwen_orchestrator.ingress import (  # noqa: E402
    ShadowTurnCoordinator,
    TurnState,
)


class ReplayValidationError(AssertionError):
    """The candidate's structural behavior diverged from a fixture baseline."""


@dataclass(frozen=True, slots=True)
class ReplayResult:
    """One local replay result. All persisted representations are redacted."""

    run_id: str
    case_id: str
    title: str
    trace: tuple[dict[str, Any], ...]
    inputs: tuple[dict[str, Any], ...]
    requests: tuple[dict[str, Any], ...]
    tools: tuple[dict[str, Any], ...]
    outputs: tuple[dict[str, Any], ...]
    logs: tuple[dict[str, Any], ...]
    summary: dict[str, Any]
    validation_errors: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.validation_errors

    def to_dict(self) -> dict[str, Any]:
        return redact_value(
            {
                "schema_version": 1,
                "run_id": self.run_id,
                "case_id": self.case_id,
                "title": self.title,
                "passed": self.passed,
                "summary": self.summary,
                "validation_errors": list(self.validation_errors),
                "inputs": list(self.inputs),
                "requests": list(self.requests),
                "tools": list(self.tools),
                "outputs": list(self.outputs),
                "logs": list(self.logs),
                "trace": list(self.trace),
            }
        )


def _catalog_path(name: str) -> Path:
    if not name.replace("-", "").replace("_", "").replace(" ", "").isalnum():
        raise ValueError("catalog name may only contain letters, numbers, hyphens and underscores")
    candidates = (
        REPOSITORY_ROOT / "tests" / "fixtures" / "cases" / f"{name}.json",
        REPOSITORY_ROOT / "tests" / "fixtures" / "replay" / f"{name}.json",
    )
    for path in candidates:
        if path.is_file():
            return RepositoryPathPolicy(REPOSITORY_ROOT).assert_safe_read(path)
    raise FileNotFoundError(f"unknown replay catalog: {name}")


def load_case_catalog(name: str = "p0_cases") -> tuple[dict[str, Any], ...]:
    """Load a versioned, JSON-only fixture catalog from the repository."""

    payload = json.loads(_catalog_path(name).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError(f"{name} must contain schema_version=1")
    ensure_valid(validate_functional_catalog(payload), label=f"functional fixture catalog {name}")
    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise ValueError(f"{name} must contain a cases list")
    inherited_compare = payload.get("default_compare")
    normalized: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError(f"{name} contains a non-object case")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id.startswith("p0-"):
            raise ValueError(f"{name} case id must start with p0-")
        copied = dict(case)
        if "compare" not in copied and isinstance(inherited_compare, dict):
            copied["compare"] = dict(inherited_compare)
        copied.setdefault("tags", ["uncategorized"])
        normalized.append(copied)
    return tuple(normalized)


def load_injection_catalog(name: str = "p0_injection_cases") -> tuple[dict[str, Any], ...]:
    """Load synthetic security input cases without attempting a real LLM call."""

    payload = json.loads(_catalog_path(name).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{name} must contain an object")
    ensure_valid(validate_injection_catalog(payload), label=f"injection fixture catalog {name}")
    cases = payload.get("cases")
    assert isinstance(cases, list)  # validated above; keeps the public return type narrow.
    return tuple(dict(case) for case in cases)


def _event_payload(raw: dict[str, Any]) -> dict[str, Any]:
    event = dict(raw)
    event.setdefault("message_id", "synthetic-message")
    event.setdefault("user_id", "synthetic-user")
    event.setdefault("group_id", "synthetic-group")
    event.setdefault("trigger", "message")
    text = event.get("raw_message", event.get("text", ""))
    if not isinstance(text, str):
        raise ValueError("event text must be a string")
    event["raw_message"] = text
    event.setdefault("message", [{"type": "text", "data": {"text": text}}])
    return event


def build_interactive_case(
    *,
    case_id: str,
    text: str,
    route: str = "chat",
    stream: bool = False,
) -> dict[str, Any]:
    """Create an in-memory fixture for the Local Test Console input composer."""

    if not isinstance(text, str) or not text.strip():
        raise ValueError("text is required")
    return {
        "id": case_id,
        "title": "Local Console synthetic input",
        "route": route,
        "timeline": [
            {
                "op": "event",
                "at": 0.0,
                "event": {
                    "message_id": f"{case_id}-message-001",
                    "user_id": "synthetic-ui-user",
                    "group_id": "synthetic-ui-group",
                    "raw_message": text,
                    "message": [{"type": "text", "data": {"text": text}}],
                },
            },
            {"op": "flush", "at": 3.0},
        ],
        "simulation": {
            "request_roles": ["main_reply"],
            "stream": bool(stream),
            "audit": "allow",
            "deliveries": 1,
            "output": "合成测试回复：已完成本地回放。",
        },
        "expected": {"main_reply_requests": 1, "deliveries": 1, "security_safe": True},
        "compare": {"text": "structural", "ignore_fields": ["duration_ms", "generated_at"]},
        "tags": ["interactive", "local-console"],
    }


class ReplayEngine:
    """Run synthetic P0 cases without network, files outside the sandbox or LLMs."""

    def __init__(self, *, quiet_window_seconds: float = 3.0, max_trace_events: int = 1_000) -> None:
        self.quiet_window_seconds = quiet_window_seconds
        self.max_trace_events = max_trace_events

    def run_case(self, case: dict[str, Any], *, run_id: str | None = None) -> ReplayResult:
        case_id = str(case.get("id", ""))
        if not case_id:
            raise ValueError("case id is required")
        title = str(case.get("title", case_id))
        route = str(case.get("route", "chat"))
        resolved_run_id = run_id or f"replay-{case_id}"
        trace = TraceStore(run_id=resolved_run_id, max_events=self.max_trace_events)
        coordinator = ShadowTurnCoordinator(quiet_window_seconds=self.quiet_window_seconds)
        simulation = case.get("simulation", {})
        if not isinstance(simulation, dict):
            raise ValueError(f"{case_id} simulation must be an object")
        timeline = case.get("timeline")
        if not isinstance(timeline, list):
            raise ValueError(f"{case_id} timeline must be a list")

        inputs: list[dict[str, Any]] = []
        requests: list[dict[str, Any]] = []
        tools: list[dict[str, Any]] = []
        outputs: list[dict[str, Any]] = []
        logs: list[dict[str, Any]] = []
        ready_turns = 0
        cancelled_turns = 0
        request_counter = 0
        tool_counter = 0
        delivery_counter = 0
        last_request_id: str | None = None
        context_sections: list[dict[str, Any]] = []
        write_tool_calls = 0
        duplicate_write_tool_attempts = 0
        invalid_tool_call_ids = 0
        late_delivery_count = 0
        audit_before_delivery = True
        secret_leak_detected = 0

        def log(level: str, message: str, *, at: float, request_id: str | None = None) -> None:
            record = {
                "at": at,
                "level": level,
                "source": "HARNESS",
                "message": redact_text(message),
                "request_id": request_id,
            }
            logs.append(record)
            trace.emit(
                "log.emitted",
                at=at,
                source="HARNESS",
                payload={"level": level, "message": record["message"]},
                request_id=request_id,
            )

        def record_turn(snapshot: Any, *, action: str, at: float) -> None:
            nonlocal cancelled_turns
            turn = snapshot.turn
            if snapshot.state is TurnState.CANCELLED:
                cancelled_turns += 1
            trace.emit(
                f"turn.{action}",
                at=at,
                source="SHADOW_COORDINATOR",
                payload=snapshot.structural_summary(),
                session_id=turn.session_id,
                event_id=str(turn.metadata.get("message_id", "")) or None,
                turn_id=turn.request_id,
                request_id=turn.request_id,
            )

        def execute_ready_turn(snapshot: Any, *, at: float) -> None:
            nonlocal request_counter, tool_counter, delivery_counter
            nonlocal write_tool_calls, duplicate_write_tool_attempts, invalid_tool_call_ids
            nonlocal late_delivery_count, audit_before_delivery, secret_leak_detected
            turn = snapshot.turn
            turn_id = turn.request_id
            coordinator.mark_stage(turn_id, TurnState.REQUESTING)
            trace.emit(
                "turn.requesting",
                at=at,
                source="SHADOW_COORDINATOR",
                payload={"state": TurnState.REQUESTING.value},
                session_id=turn.session_id,
                turn_id=turn_id,
                request_id=turn_id,
            )
            text = redact_text(turn.text)
            raw_context_sections = simulation.get("context_sections", [])
            if not isinstance(raw_context_sections, list):
                raise ValueError(f"{case_id} context_sections must be a list")
            ordered_context: list[dict[str, Any]] = []
            for index, raw_section in enumerate(raw_context_sections):
                if not isinstance(raw_section, dict):
                    raise ValueError(f"{case_id} context section entries must be objects")
                section = {
                    "source": str(raw_section.get("source", "fake-context")),
                    "name": str(raw_section.get("name", f"section-{index + 1}")),
                    "order": int(raw_section.get("order", index)),
                    "version": str(raw_section.get("version", "fixture-v1")),
                    "chars": int(raw_section.get("chars", 0)),
                    "tokens": int(raw_section.get("tokens", 0)),
                    "fingerprint": fingerprint_text(str(raw_section.get("content", raw_section.get("name", index)))),
                }
                ordered_context.append(section)
                trace.emit(
                    "context.section.added",
                    at=at + 0.001 + index * 0.001,
                    source="FAKE_CONTEXT",
                    payload=section,
                    session_id=turn.session_id,
                    turn_id=turn_id,
                    request_id=turn_id,
                )
            if not simulation.get("fault_context_preserve_input_order", False):
                ordered_context.sort(key=lambda section: (section["order"], section["source"], section["name"]))
            context_sections.extend(ordered_context)
            trace.emit(
                "context.assembled",
                at=at + 0.01,
                source="FAKE_CONTEXT",
                payload={
                    "sections": len(ordered_context),
                    "chars": sum(section["chars"] for section in ordered_context),
                    "tokens": sum(section["tokens"] for section in ordered_context),
                    "order": [section["name"] for section in ordered_context],
                },
                session_id=turn.session_id,
                turn_id=turn_id,
                request_id=turn_id,
            )
            roles = simulation.get("request_roles", ["main_reply"])
            if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
                raise ValueError(f"{case_id} request_roles must be a list of strings")
            parent_request_id: str | None = None
            for role in roles:
                request_counter += 1
                request_id = f"{turn_id}-request-{request_counter:02d}"
                request_payload = {
                    "role": role,
                    "messages": [{"role": "user", "content": text}],
                    "model": "fake-local-model",
                    "stream": bool(simulation.get("stream", False)),
                    "context_sections": ordered_context,
                }
                request = {
                    "request_id": request_id,
                    "parent_request_id": parent_request_id,
                    "turn_id": turn_id,
                    "role": role,
                    "status": "completed",
                    "payload": request_payload,
                    "response": {"finish_reason": "stop", "usage": {"input_tokens": 1, "output_tokens": 1}},
                }
                requests.append(request)
                trace.emit(
                    "request.started",
                    at=at,
                    source="FAKE_PROVIDER",
                    payload={"role": role, "model": "fake-local-model", "stream": request_payload["stream"]},
                    session_id=turn.session_id,
                    turn_id=turn_id,
                    request_id=request_id,
                    parent_request_id=parent_request_id,
                )
                if request_payload["stream"]:
                    trace.emit(
                        "request.chunk",
                        at=at + 0.01,
                        source="FAKE_PROVIDER",
                        payload={"chars": 8, "chunk_index": 1},
                        session_id=turn.session_id,
                        turn_id=turn_id,
                        request_id=request_id,
                    )
                trace.emit(
                    "request.completed",
                    at=at + 0.02,
                    source="FAKE_PROVIDER",
                    payload={"role": role, "finish_reason": "stop", "usage": request["response"]["usage"]},
                    session_id=turn.session_id,
                    turn_id=turn_id,
                    request_id=request_id,
                    parent_request_id=parent_request_id,
                )
                log("INFO", f"fake request completed: {role}", at=at + 0.02, request_id=request_id)
                parent_request_id = request_id

            tool_specs = simulation.get("tools", [])
            if not isinstance(tool_specs, list):
                raise ValueError(f"{case_id} tools must be a list")
            if tool_specs:
                coordinator.mark_stage(turn_id, TurnState.TOOL_LOOP)
                trace.emit(
                    "turn.tool_loop",
                    at=at + 0.03,
                    source="SHADOW_COORDINATOR",
                    payload={"state": TurnState.TOOL_LOOP.value},
                    session_id=turn.session_id,
                    turn_id=turn_id,
                    request_id=turn_id,
                )
            seen_write_idempotency: set[tuple[str, str]] = set()
            for raw_tool in tool_specs:
                if not isinstance(raw_tool, dict) or not isinstance(raw_tool.get("name"), str):
                    raise ValueError(f"{case_id} tool entries require a name")
                tool_counter += 1
                call_id = f"{turn_id}-call-{tool_counter:02d}"
                name = raw_tool["name"]
                effect = str(raw_tool.get("effect", "read"))
                idempotency_key = str(raw_tool.get("idempotency_key", f"{name}-{tool_counter}"))
                if effect == "write":
                    write_tool_calls += 1
                    identity = (name, idempotency_key)
                    if identity in seen_write_idempotency:
                        duplicate_write_tool_attempts += 1
                    seen_write_idempotency.add(identity)
                result_call_id = str(raw_tool.get("result_call_id", call_id))
                if result_call_id != call_id:
                    invalid_tool_call_ids += 1
                tool = {
                    "call_id": call_id,
                    "name": name,
                    "effect": effect,
                    "status": "suppressed" if effect in {"send", "write", "steal"} else "completed",
                    "result_length": int(raw_tool.get("result_length", 24)),
                    "idempotency_key": idempotency_key,
                    "result_call_id": result_call_id,
                }
                tools.append(tool)
                trace.emit(
                    "tool.started",
                    at=at + 0.04,
                    source="FAKE_TOOL",
                    payload={"name": name, "effect": effect},
                    session_id=turn.session_id,
                    turn_id=turn_id,
                    request_id=parent_request_id,
                    call_id=call_id,
                )
                trace.emit(
                    "tool.result.continuation",
                    at=at + 0.055,
                    source="FAKE_PROVIDER",
                    payload={"name": name, "result_call_id": result_call_id, "matches_call_id": result_call_id == call_id},
                    session_id=turn.session_id,
                    turn_id=turn_id,
                    request_id=parent_request_id,
                    call_id=result_call_id,
                )
                trace.emit(
                    "tool.suppressed" if tool["status"] == "suppressed" else "tool.completed",
                    at=at + 0.05,
                    source="FAKE_TOOL",
                    payload={"name": name, "effect": effect, "result_length": tool["result_length"]},
                    session_id=turn.session_id,
                    turn_id=turn_id,
                    request_id=parent_request_id,
                    call_id=call_id,
                )
                log("INFO", f"fake tool {tool['status']}: {name}", at=at + 0.05, request_id=parent_request_id)

            coordinator.mark_stage(turn_id, TurnState.RESPONDING)
            trace.emit(
                "turn.responding",
                at=at + 0.06,
                source="SHADOW_COORDINATOR",
                payload={"state": TurnState.RESPONDING.value},
                session_id=turn.session_id,
                turn_id=turn_id,
                request_id=turn_id,
            )
            audit = str(simulation.get("audit", "allow"))
            audit_bypassed = bool(simulation.get("audit_bypassed", False))
            audit_before_delivery = audit_before_delivery and not audit_bypassed
            if not audit_bypassed:
                trace.emit(
                    "audit.completed",
                    at=at + 0.07,
                    source="FAKE_OUTPUT_AUDIT",
                    payload={"decision": audit},
                    session_id=turn.session_id,
                    turn_id=turn_id,
                    request_id=parent_request_id,
                )
            raw_output_text = str(simulation.get("output", "合成测试回复。"))
            if simulation.get("security_safe", True) is False or audit == "block":
                raw_output_text = "无法满足该请求。"
            secret_leak_detected += len(find_secret_hits(raw_output_text))
            output_text = raw_output_text
            output_text = redact_text(output_text)
            outputs.append({"stage": "provider_final", "text": output_text, "turn_id": turn_id})
            trace.emit(
                "output.cleaned",
                at=at + 0.08,
                source="FAKE_CLEANER",
                payload={"chars": len(output_text), "fingerprint": fingerprint_text(output_text)},
                session_id=turn.session_id,
                turn_id=turn_id,
                request_id=parent_request_id,
            )
            segment_count = max(1, int(simulation.get("segments", 1)))
            trace.emit(
                "output.segmented",
                at=at + 0.09,
                source="FAKE_SEGMENTATION",
                payload={"segments": segment_count},
                session_id=turn.session_id,
                turn_id=turn_id,
                request_id=parent_request_id,
            )
            delivery_count = max(0, int(simulation.get("deliveries", 1)))
            for _ in range(delivery_count):
                delivery_counter += 1
                delivery_id = f"{turn_id}-delivery-{delivery_counter:02d}"
                outputs.append(
                    {
                        "stage": "delivery",
                        "delivery_id": delivery_id,
                        "type": "text",
                        "text": output_text,
                        "turn_id": turn_id,
                    }
                )
                trace.emit(
                    "delivery.completed",
                    at=at + 0.1,
                    source="DELIVERY_SPY",
                    payload={"type": "text", "chars": len(output_text), "idempotency_key": delivery_id},
                    session_id=turn.session_id,
                    turn_id=turn_id,
                    request_id=parent_request_id,
                    delivery_id=delivery_id,
                )
            simulated_late_deliveries = max(0, int(simulation.get("late_deliveries", 0)))
            for late_index in range(simulated_late_deliveries):
                delivery_counter += 1
                late_delivery_count += 1
                trace.emit(
                    "delivery.completed",
                    at=at + 0.105 + late_index * 0.001,
                    source="DELIVERY_SPY",
                    payload={"type": "text", "chars": len(output_text), "late": True},
                    session_id=turn.session_id,
                    turn_id=turn_id,
                    request_id=parent_request_id,
                    delivery_id=f"{turn_id}-late-delivery-{delivery_counter:02d}",
                )
            if simulation.get("late_output"):
                trace.emit(
                    "delivery.suppressed",
                    at=at + 0.11,
                    source="DELIVERY_SPY",
                    payload={"reason": "cancelled_or_late_output"},
                    session_id=turn.session_id,
                    turn_id=turn_id,
                    request_id=parent_request_id,
                )
            coordinator.mark_stage(turn_id, TurnState.COMPLETED)
            trace.emit(
                "turn.completed",
                at=at + 0.12,
                source="SHADOW_COORDINATOR",
                payload={"delivery_count": delivery_count, "audit": audit},
                session_id=turn.session_id,
                turn_id=turn_id,
                request_id=turn_id,
            )

        for entry in timeline:
            if not isinstance(entry, dict):
                raise ValueError(f"{case_id} timeline entries must be objects")
            op = entry.get("op")
            at_raw = entry.get("at")
            if not isinstance(at_raw, (int, float)):
                raise ValueError(f"{case_id} timeline entry {op!r} requires numeric at")
            at = float(at_raw)
            if op == "event":
                raw_event = entry.get("event")
                if not isinstance(raw_event, dict):
                    raise ValueError(f"{case_id} event entry requires event object")
                event = _event_payload(raw_event)
                event["route"] = route
                text = str(event["raw_message"])
                input_record = {
                    "event_id": str(event["message_id"]),
                    "at": at,
                    "route": route,
                    "text": redact_text(text),
                    "chars": len(text),
                    "fingerprint": fingerprint_text(text),
                }
                inputs.append(input_record)
                trace.emit(
                    "ui.input.received",
                    at=at,
                    source="REPLAY_FIXTURE",
                    payload={"chars": len(text), "fingerprint": input_record["fingerprint"], "route": route},
                    event_id=input_record["event_id"],
                )
                terminal_count_before = len(coordinator.terminal_turns)
                result = coordinator.ingest_event(event, now=at)
                if result.request_id:
                    last_request_id = result.request_id
                trace.emit(
                    "onebot.event.normalized",
                    at=at,
                    source="SHADOW_COORDINATOR",
                    payload={"action": result.action, "fingerprint": result.fingerprint},
                    event_id=input_record["event_id"],
                    request_id=result.request_id,
                )
                if result.snapshot is not None:
                    record_turn(result.snapshot, action=result.action, at=at)
                for terminal in coordinator.terminal_turns[terminal_count_before:]:
                    if terminal.state is TurnState.CANCELLED:
                        record_turn(terminal, action="cancelled", at=at)
                continue
            if op == "flush":
                for snapshot in coordinator.flush_ready(now=at):
                    ready_turns += 1
                    record_turn(snapshot, action="ready", at=at)
                    if entry.get("dispatch", True):
                        execute_ready_turn(snapshot, at=at)
                continue
            if op == "mark_stage":
                state_name = entry.get("state")
                request_id = entry.get("request_id")
                if request_id == "$last":
                    request_id = last_request_id
                if not isinstance(state_name, str) or not isinstance(request_id, str):
                    raise ValueError(f"{case_id} mark_stage requires state and request_id")
                snapshot = coordinator.mark_stage(request_id, TurnState(state_name))
                record_turn(snapshot, action=state_name.lower(), at=at)
                continue
            raise ValueError(f"{case_id} has unsupported timeline operation {op!r}")

        expected = case.get("expected", {})
        if not isinstance(expected, dict):
            raise ValueError(f"{case_id} expected must be an object")
        delivered = [item for item in outputs if item.get("stage") == "delivery"]
        actual = {
            "turns_ready": ready_turns,
            "main_reply_requests": sum(1 for item in requests if item["role"] == "main_reply"),
            "deliveries": len(delivered),
            "tool_names": [item["name"] for item in tools],
            "tool_call_count": len(tools),
            "write_tool_calls": write_tool_calls,
            "duplicate_write_tool_attempts": duplicate_write_tool_attempts,
            "invalid_tool_call_ids": invalid_tool_call_ids,
            "cancelled_turns": cancelled_turns,
            "vlm_calls": int(simulation.get("vlm_calls", 0)),
            "image_summary_reused": int(simulation.get("image_summary_reused", 0)),
            "audit": str(simulation.get("audit", "allow")),
            "audit_before_delivery": audit_before_delivery,
            "late_delivery_count": late_delivery_count,
            "context_sections": len(context_sections),
            "context_chars": sum(section["chars"] for section in context_sections),
            "context_order": [section["name"] for section in context_sections],
            "secret_leak_detected": secret_leak_detected,
            "security_safe": all(
                "内部信息" not in item.get("text", "")
                and not find_secret_hits(item.get("text", ""))
                or item.get("stage") != "delivery"
                for item in outputs
            ) and secret_leak_detected == 0,
            "trace_dropped": trace.dropped_count,
        }
        validation_errors: list[str] = []
        for key, expected_value in expected.items():
            actual_value = actual.get(key)
            if actual_value != expected_value:
                validation_errors.append(f"{key}: expected {expected_value!r}, got {actual_value!r}")
        trace.emit(
            "run.completed",
            at=float(timeline[-1].get("at", 0)) + 0.2 if timeline else 0.2,
            source="HARNESS",
            payload={"passed": not validation_errors, "validation_errors": len(validation_errors), **actual},
        )
        return ReplayResult(
            run_id=resolved_run_id,
            case_id=case_id,
            title=title,
            trace=tuple(trace.to_dicts()),
            inputs=tuple(inputs),
            requests=tuple(requests),
            tools=tuple(tools),
            outputs=tuple(outputs),
            logs=tuple(logs),
            summary=actual,
            validation_errors=tuple(validation_errors),
        )

    def run_catalog(self, name: str = "p0_cases") -> tuple[ReplayResult, ...]:
        return tuple(self.run_case(case) for case in load_case_catalog(name))

    @staticmethod
    def require_passing(result: ReplayResult) -> None:
        if not result.passed:
            raise ReplayValidationError("; ".join(result.validation_errors))
