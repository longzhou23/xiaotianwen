"""Unified, model-call-free request plan shared by main and tool rounds."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .context import ContextAssembler, ContextAssemblyResult, TurnContextMemo
from .contracts import ContextSection, ContractValidationError, TurnEnvelope
from .contracts.validation import require_non_empty_string, sha256_text
from .decision import RoutePolicyTable


@dataclass(frozen=True, slots=True)
class UnifiedRequestPlan:
    request_id: str
    session_id: str
    route: str
    model: str
    reasoning_effort: str
    max_output_tokens: int
    streaming: bool
    cache_family: str
    context: ContextAssemblyResult
    media_ids: tuple[str, ...]
    delivery_owner: str

    def structural_summary(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "route": self.route,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "max_output_tokens": self.max_output_tokens,
            "streaming": self.streaming,
            "cache_family": self.cache_family,
            "context": self.context.structural_summary(),
            "media_ids": list(self.media_ids),
            "delivery_owner": self.delivery_owner,
        }


class RequestPlanner:
    """Assemble context once and retain the plan for AstrBot tool continuation."""

    def __init__(
        self,
        *,
        assembler: ContextAssembler | None = None,
        route_policy: RoutePolicyTable | None = None,
        max_requests: int = 1_000,
    ) -> None:
        self.assembler = assembler or ContextAssembler()
        self.route_policy = route_policy or RoutePolicyTable()
        self.context_memo = TurnContextMemo(max_requests=max_requests)
        self._plans: dict[str, UnifiedRequestPlan] = {}
        self._rounds: dict[str, int] = {}
        self.max_requests = max_requests

    def build(
        self,
        turn: TurnEnvelope,
        sections: Iterable[ContextSection],
        *,
        model: str,
        instruction_version: str,
        tool_schema_hash: str,
        delivery_owner: str = "xiaotianwen_orchestrator",
        memory_refresh: bool = False,
    ) -> tuple[UnifiedRequestPlan, bool]:
        if not isinstance(turn, TurnEnvelope):
            raise ContractValidationError("planner requires TurnEnvelope")
        materialized = tuple(sections)

        def assemble() -> ContextAssemblyResult:
            return self.assembler.assemble(materialized, route=turn.route)

        context, reused = self.context_memo.get_or_build(
            turn.request_id,
            assemble,
            memory_refresh=memory_refresh,
        )
        tuning = self.route_policy.for_route(turn.route).tuning
        normalized_model = require_non_empty_string(model, "model")
        instruction = require_non_empty_string(instruction_version, "instruction_version")
        tools = require_non_empty_string(tool_schema_hash, "tool_schema_hash")
        cache_family = sha256_text(f"{normalized_model}\n{turn.route}\n{instruction}\n{tools}")
        plan = UnifiedRequestPlan(
            request_id=turn.request_id,
            session_id=turn.session_id,
            route=turn.route,
            model=normalized_model,
            reasoning_effort=tuning.reasoning_effort,
            max_output_tokens=tuning.max_output_tokens,
            streaming=tuning.streaming,
            cache_family=cache_family,
            context=context,
            media_ids=tuple(item.media_id for item in turn.media),
            delivery_owner=require_non_empty_string(delivery_owner, "delivery_owner"),
        )
        self._plans[turn.request_id] = plan
        self._rounds.setdefault(turn.request_id, 0)
        while len(self._plans) > self.max_requests:
            oldest = next(iter(self._plans))
            self._plans.pop(oldest, None)
            self._rounds.pop(oldest, None)
        return plan, reused

    def continue_after_tool(self, request_id: str, *, call_id: str) -> UnifiedRequestPlan:
        require_non_empty_string(call_id, "call_id")
        plan = self._plans.get(request_id)
        if plan is None:
            raise ContractValidationError("unknown request_id for tool continuation")
        self._rounds[request_id] = self._rounds.get(request_id, 0) + 1
        return plan

    def model_rounds(self, request_id: str) -> int:
        return self._rounds.get(request_id, 0)
