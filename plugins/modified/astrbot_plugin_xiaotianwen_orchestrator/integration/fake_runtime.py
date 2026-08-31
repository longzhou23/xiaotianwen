"""Disposable Fake OneBot/AstrBot/Provider runtime for P1 contract tests.

The runtime exercises the same pure contracts as the future isolated plugin
instance.  It intentionally has no socket, subprocess, database or model
client.  ``ACTIVE`` here means the *fake* delivery owner is allowed to run;
it never enables the production Orchestrator.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..context import ContextAssembler
from ..contracts import ContextSection, TurnEnvelope
from ..contracts.validation import ContractValidationError, require_identifier
from ..ingress import (
    OrchestratorMode,
    PrimaryReplyOwnership,
    ShadowTurnCoordinator,
    TurnState,
    event_fingerprint,
    event_to_envelope,
)
from ..output import DeliveryCoordinator
from ..request_plan import RequestPlanner, UnifiedRequestPlan
from ..p2.provider_registry import ContextProviderRegistry, ProviderRegistration
from ..p2.security import SecurityBoundary
from ..p2.tools import ToolCall, ToolExecutor, ToolRegistry, ToolSpec
from ..contracts import ToolExecutionPolicy
from .observer import ObservationAdapter, RuntimeObservationStore


class FakeProviderError(RuntimeError):
    """An expected fake provider failure; the message is never logged."""


@dataclass(frozen=True, slots=True)
class FakeToolCall:
    call_id: str
    name: str = "synthetic_lookup"
    effect: str = "read"
    arguments: Mapping[str, object] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        object.__setattr__(self, "call_id", require_identifier(self.call_id, "call_id"))
        object.__setattr__(self, "name", require_identifier(self.name, "tool name"))
        object.__setattr__(self, "arguments", dict(self.arguments or {}))


@dataclass(frozen=True, slots=True)
class FakeProviderResponse:
    request_id: str
    role: str
    text: str
    finish_reason: str
    usage: Mapping[str, int]
    tool_calls: tuple[FakeToolCall, ...] = ()


class FakeProvider:
    """Strictly local provider fake with stream/tool/error templates."""

    _OUTCOMES = frozenset({"final", "stream", "tool", "timeout", "abort", "error"})

    def __init__(self, observer: ObservationAdapter, *, outcome: str = "final") -> None:
        if outcome not in self._OUTCOMES:
            raise ContractValidationError(f"unsupported fake provider outcome: {outcome}")
        self.observer = observer
        self.outcome = outcome
        self._counter = 0
        self.calls: list[dict[str, object]] = []

    def request(
        self,
        *,
        role: str,
        plan: UnifiedRequestPlan,
        parent_request_id: str | None = None,
        force_final: bool = False,
        timestamp: float | None = None,
    ) -> FakeProviderResponse:
        self._counter += 1
        request_id = f"request-{self._counter:04d}"
        outcome = "final" if force_final else self.outcome
        stream = outcome == "stream" or plan.streaming
        self.calls.append(
            {
                "request_id": request_id,
                "role": role,
                "parent_request_id": parent_request_id,
                "outcome": outcome,
                "stream": stream,
                "context_sections": len(plan.context.sections),
            }
        )
        self.observer.request_started(
            request_id,
            role=role,
            model=plan.model,
            message_count=len(plan.context.sections),
            stream=stream,
            prompt=plan.context.payload,
            parent_request_id=parent_request_id,
            timestamp=timestamp,
        )
        if outcome in {"timeout", "abort", "error"}:
            self.observer.request_failed(
                request_id,
                role=role,
                error_class={"timeout": "TimeoutError", "abort": "CancelledError", "error": "FakeProviderError"}[outcome],
                timestamp=timestamp,
            )
            raise FakeProviderError(outcome)

        usage = {"input_tokens": len(plan.context.payload), "output_tokens": 24, "total_tokens": len(plan.context.payload) + 24}
        tool_calls: tuple[FakeToolCall, ...] = ()
        finish_reason = "stop"
        text = "合成 Provider 回复。"
        if outcome == "tool":
            finish_reason = "tool_calls"
            tool_calls = (FakeToolCall(f"call-{self._counter:04d}"),)
            text = "我先查询一项合成资料。"
        if stream:
            for index, chunk in enumerate((text[:4], text[4:]), 1):
                if chunk:
                    self.observer.request_chunk(request_id, chunk=chunk, index=index, timestamp=timestamp)
        self.observer.request_completed(
            request_id,
            role=role,
            finish_reason=finish_reason,
            usage=usage,
            tool_call_count=len(tool_calls),
            parent_request_id=parent_request_id,
            timestamp=timestamp,
        )
        return FakeProviderResponse(request_id, role, text, finish_reason, usage, tool_calls)


class FakeOneBot:
    """Connection state only; event values are normalized at the boundary."""

    def __init__(self, observer: ObservationAdapter, *, capture_text: bool = False) -> None:
        self.observer = observer
        self.capture_text = capture_text
        self.connected = True

    def disconnect(self, *, timestamp: float | None = None) -> None:
        self.connected = False
        self.observer.log(level="WARNING", message="Fake OneBot disconnected", capture_text=self.capture_text, timestamp=timestamp)

    def reconnect(self, *, timestamp: float | None = None) -> None:
        self.connected = True
        self.observer.log(level="INFO", message="Fake OneBot reconnected", capture_text=self.capture_text, timestamp=timestamp)


class FakeAstrBotRuntime:
    """Run one disposable request path with explicit observations."""

    def __init__(
        self,
        *,
        run_id: str = "p1-fake-run",
        mode: OrchestratorMode | str = OrchestratorMode.ACTIVE,
        quiet_window_seconds: float = 3.0,
        capture_text: bool = True,
        provider_outcome: str = "final",
        registry: ContextProviderRegistry | None = None,
    ) -> None:
        self.store = RuntimeObservationStore(run_id)
        self.capture_text = capture_text
        self.observer = ObservationAdapter(self.store, source="FAKE_ASTRBOT")
        self.onebot = FakeOneBot(self.observer, capture_text=capture_text)
        self.provider = FakeProvider(self.observer, outcome=provider_outcome)
        self.coordinator = ShadowTurnCoordinator(enabled=True, quiet_window_seconds=quiet_window_seconds)
        resolved_mode = mode if isinstance(mode, OrchestratorMode) else OrchestratorMode.parse(mode)
        self.ownership = PrimaryReplyOwnership(resolved_mode)
        self.registry = registry or ContextProviderRegistry(assembler=ContextAssembler())
        if not self.registry.registrations():
            self.registry.register(ProviderRegistration("current_message", "current_message", self._current_message))
        self.planner = RequestPlanner(assembler=self.registry.assembler)
        self.delivery = DeliveryCoordinator()
        self.security = SecurityBoundary()
        self.tool_registry = ToolRegistry()
        self.tool_registry.register(
            ToolSpec(
                "synthetic_lookup",
                ToolExecutionPolicy("read", True, True, 5, 400),
                tool_result_chars=400,
            )
        )
        self.tool_executor = ToolExecutor(self.tool_registry, max_read_concurrency=3)
        self.last_request_id: str | None = None

    @staticmethod
    def _current_message(turn: TurnEnvelope, snapshots: Mapping[str, object]) -> ContextSection:
        content = turn.text.strip() or "[合成媒体输入]"
        return ContextSection(
            source="current_message",
            priority=100,
            content=content,
            max_chars=4_000,
            cache_scope="request",
            version="fake-current-message-v1",
            sensitive=True,
        )

    def submit_event(self, event: object, *, now: float) -> object:
        if not self.onebot.connected:
            self.store.emit(
                "onebot.event.dropped",
                source="FAKE_ONEBOT",
                status="not_connected",
                timestamp=now,
                payload={"reason": "fake_onebot_disconnected"},
                capture_mode="NOT_CONNECTED",
            )
            return None
        turn = event_to_envelope(event, received_at=now)
        self.observer.input_received(turn, capture_text=self.capture_text, timestamp=now)
        result = self.coordinator.ingest_envelope(
            turn,
            fingerprint=event_fingerprint(event, received_at=now),
            now=now,
        )
        self.observer.normalized_event(turn, action=result.action, timestamp=now)
        if result.accepted:
            self.observer.turn_stage(turn, stage="merged" if result.action == "merged" else "started", timestamp=now)
        if result.request_id:
            self.last_request_id = result.request_id
        return result

    def flush(self, *, now: float) -> tuple[str, ...]:
        completed: list[str] = []
        for snapshot in self.coordinator.flush_ready(now=now):
            turn = snapshot.turn
            self.observer.turn_stage(turn, stage="ready", timestamp=now)
            event_id = str(turn.metadata.get("message_id", "event-unknown"))
            decision = self.ownership.decide(turn, event_id)
            self.store.emit(
                "turn.ownership",
                source="SHADOW_COORDINATOR",
                timestamp=now,
                payload={"owner": decision.owner, "should_dispatch": decision.should_dispatch, "reason": decision.reason},
                session_id=turn.session_id,
                turn_id=turn.request_id,
                request_id=turn.request_id,
            )
            if not decision.should_dispatch:
                self.store.emit(
                    "turn.shadow_skipped",
                    source="SHADOW_COORDINATOR",
                    status="observed",
                    timestamp=now,
                    payload={"owner": decision.owner},
                    capture_mode="PARTIAL",
                    session_id=turn.session_id,
                    turn_id=turn.request_id,
                    request_id=turn.request_id,
                )
                continue
            self._dispatch(snapshot, now=now, owner=decision.owner)
            completed.append(turn.request_id)
        return tuple(completed)

    def _dispatch(self, snapshot: object, *, now: float, owner: str) -> None:
        turn = snapshot.turn
        self.coordinator.mark_stage(turn.request_id, TurnState.REQUESTING)
        self.observer.turn_stage(turn, stage="requesting", timestamp=now)
        collection, assembly = self.registry.assemble(turn)
        for section in collection.sections:
            self.observer.context_section(section, timestamp=now)
        self.observer.context_assembled(assembly, timestamp=now)
        plan, reused = self.planner.build(
            turn,
            collection.sections,
            model="fake-model",
            instruction_version="p1-fake-v1",
            tool_schema_hash="fake-tools-v1",
            delivery_owner=owner,
        )
        self.store.emit(
            "request.plan",
            source="FAKE_ASTRBOT",
            timestamp=now,
            payload={"context_reused": reused, "cache_family": plan.cache_family},
            request_id=turn.request_id,
        )
        try:
            response = self.provider.request(role="main_reply", plan=plan, timestamp=now)
        except FakeProviderError as exc:
            self.coordinator.mark_stage(turn.request_id, TurnState.CANCELLED)
            self.observer.turn_stage(turn, stage="cancelled", status="failed", timestamp=now)
            self.observer.log(level="ERROR", message=f"Fake provider failed: {type(exc).__name__}", capture_text=self.capture_text, request_id=turn.request_id, timestamp=now)
            return

        if response.tool_calls:
            self.coordinator.mark_stage(turn.request_id, TurnState.TOOL_LOOP)
            self.observer.turn_stage(turn, stage="tool_loop", timestamp=now)
            calls = tuple(
                ToolCall(call.call_id, call.name, dict(call.arguments))
                for call in response.tool_calls
            )

            async def handler(call: ToolCall) -> object:
                return {"lookup": "synthetic-result", "call_id": call.call_id}

            results = asyncio.run(self.tool_executor.execute(calls, handler))
            for result in results:
                self.observer.tool(
                    "completed" if result.status in {"completed", "deduplicated"} else "suppressed",
                    request_id=turn.request_id,
                    call_id=result.call_id,
                    name=result.name,
                    effect=result.effect,
                    status=result.status,
                    result_chars=len(result.result),
                    timestamp=now,
                )
            plan = self.planner.continue_after_tool(turn.request_id, call_id=response.tool_calls[0].call_id)
            try:
                response = self.provider.request(
                    role="tool_continuation",
                    plan=plan,
                    parent_request_id=response.request_id,
                    force_final=True,
                    timestamp=now,
                )
            except FakeProviderError:
                self.coordinator.mark_stage(turn.request_id, TurnState.CANCELLED)
                self.observer.turn_stage(turn, stage="cancelled", status="failed", timestamp=now)
                return

        self.coordinator.mark_stage(turn.request_id, TurnState.RESPONDING)
        self.observer.turn_stage(turn, stage="responding", timestamp=now)
        assessment = self.security.assess_input(turn.text)
        gated = self.security.gate_output(response.text, input_assessment=assessment, route=turn.route)
        self.observer.audit(request_id=response.request_id, decision=gated.action, labels=gated.labels, timestamp=now)
        self.observer.output(request_id=response.request_id, stage="cleaned", text=gated.text, capture_text=self.capture_text, timestamp=now)
        self.delivery.claim_owner(turn.request_id, owner)
        self.delivery.mark_audit_passed(turn.request_id)
        delivery_id = f"delivery-{response.request_id.split('-', 1)[-1]}"
        delivery = self.delivery.attempt(
            turn.request_id,
            owner=owner,
            delivery_id=delivery_id,
            requires_audit=True,
        )
        self.observer.delivery(
            request_id=turn.request_id,
            delivery_id=delivery_id,
            status="completed" if delivery.allowed else "suppressed",
            payload_type="text",
            text=gated.text,
            timestamp=now,
        )
        self.coordinator.mark_stage(turn.request_id, TurnState.COMPLETED)
        self.observer.turn_stage(turn, stage="completed", timestamp=now)

    def cancel(self, request_id: str, *, reason: str = "test_cancel", now: float = 0.0) -> None:
        self.delivery.cancel(request_id)
        self.store.emit(
            "turn.cancelled",
            source="FAKE_ASTRBOT",
            status="cancelled",
            timestamp=now,
            payload={"reason": reason},
            request_id=request_id,
        )

    def observations(self) -> tuple[dict[str, object], ...]:
        return tuple(record.to_dict() for record in self.store.snapshot())
