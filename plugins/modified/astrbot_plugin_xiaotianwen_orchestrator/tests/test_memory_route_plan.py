from __future__ import annotations

import asyncio
import json

import pytest

from astrbot_plugin_xiaotianwen_orchestrator.context import (
    AsyncSingleFlightCache,
    ContextAssembler,
    MemoryBudgetPolicy,
    MemoryQueryKey,
    deduplicate_relationship_sections,
    is_low_information,
)
from astrbot_plugin_xiaotianwen_orchestrator.contracts import (
    ContextSection,
    ContractValidationError,
    TurnEnvelope,
)
from astrbot_plugin_xiaotianwen_orchestrator.decision import (
    RouteMetrics,
    RoutePolicyTable,
    parse_binary_decision,
)
from astrbot_plugin_xiaotianwen_orchestrator.request_plan import RequestPlanner


def _section(source: str, content: str, priority: int = 50) -> ContextSection:
    return ContextSection(source, priority, content, 4_000, "request", "test-v1", True)


def _turn(request_id: str = "request-1", *, route: str = "chat") -> TurnEnvelope:
    return TurnEnvelope(
        request_id=request_id,
        session_id="private:test-user",
        route=route,
        trigger="message",
        sender_id="test-user",
        text="请继续分析",
        reply_to=None,
        media=(),
        received_at=1,
        batch_started_at=1,
    )


def test_l2_query_cache_is_versioned_short_ttl_and_single_flight() -> None:
    """Keep the P0 suite stdlib + pytest; no asyncio pytest plugin is required."""

    async def exercise() -> tuple[int, tuple[str, bool], tuple[str, bool], tuple[str, bool], tuple[str, bool]]:
        cache: AsyncSingleFlightCache[str] = AsyncSingleFlightCache()
        key = MemoryQueryKey.build("  同一个 查询 ", "memory-v1", "provider-v1")
        calls = 0
        ready = asyncio.Event()

        async def loader() -> str:
            nonlocal calls
            calls += 1
            await ready.wait()
            return "synthetic-result"

        first = asyncio.create_task(cache.get_or_load(key, loader, now=1, ttl_seconds=5))
        second = asyncio.create_task(cache.get_or_load(key, loader, now=1, ttl_seconds=5))
        await asyncio.sleep(0)
        ready.set()
        first_result, second_result = await asyncio.gather(first, second)
        cached = await cache.get_or_load(key, loader, now=2, ttl_seconds=5)
        changed_version = await cache.get_or_load(
            MemoryQueryKey.build("同一个 查询", "memory-v2", "provider-v1"),
            loader,
            now=2,
            ttl_seconds=5,
        )
        return calls, first_result, second_result, cached, changed_version

    calls, first_result, second_result, cached, changed_version = asyncio.run(exercise())

    assert calls == 2
    assert first_result[0] == second_result[0] == "synthetic-result"
    assert sorted((first_result[1], second_result[1])) == [False, True]
    assert cached == ("synthetic-result", True)
    assert changed_version[1] is False


def test_low_information_waits_for_merge_and_private_budget_is_larger() -> None:
    budgets = MemoryBudgetPolicy()

    assert is_low_information("@bot 嗯") is True
    assert is_low_information("请分析这张星图") is False
    assert budgets.assembly_policy("private:test-user").total_budget_chars > budgets.assembly_policy("group:test-group").total_budget_chars


def test_iris_relationship_sections_are_authoritative() -> None:
    sections = (
        _section("relationship_summary", "generic"),
        _section("iris_profile", "authoritative-profile", 70),
        _section("iris_affection", "authoritative-affection", 80),
    )

    result = deduplicate_relationship_sections(sections)

    assert [section.source for section in result] == ["iris_profile", "iris_affection"]


def test_request_plan_reuses_context_for_tools_and_has_stable_route_cache_family() -> None:
    builds = 0

    class CountingAssembler(ContextAssembler):
        def assemble(self, sections, *, route="chat"):
            nonlocal builds
            builds += 1
            return super().assemble(sections, route=route)

    planner = RequestPlanner(assembler=CountingAssembler())
    sections = (_section("persona", "stable-persona", 10), _section("current_message", "dynamic", 100))
    plan, reused = planner.build(
        _turn(),
        sections,
        model="fake-model",
        instruction_version="persona-v1",
        tool_schema_hash="tools-v1",
    )
    continuation = planner.continue_after_tool("request-1", call_id="call-1")
    plan_again, reused_again = planner.build(
        _turn(),
        tuple(reversed(sections)),
        model="fake-model",
        instruction_version="persona-v1",
        tool_schema_hash="tools-v1",
    )

    assert reused is False
    assert reused_again is True
    assert builds == 1
    assert continuation.context.fingerprint == plan.context.fingerprint == plan_again.context.fingerprint
    assert len(plan.cache_family) == 64
    assert planner.model_rounds("request-1") == 1
    assert "stable-persona" not in json.dumps(plan.structural_summary(), ensure_ascii=False)


def test_route_policy_keeps_streaming_off_and_decision_outputs_short() -> None:
    table = RoutePolicyTable()
    decision = table.for_route("decision").tuning
    vision = table.for_route("vision").tuning
    parsed = parse_binary_decision("yes: relevant to the current conversation")

    assert decision.reasoning_effort == "none"
    assert decision.max_output_tokens <= 192
    assert decision.streaming is False
    assert vision.max_output_tokens < table.for_route("chat").tuning.max_output_tokens
    assert parsed.allowed is True

    metrics = RouteMetrics()
    for latency in (10, 20, 30, 40, 50):
        metrics.record("decision", latency_ms=latency, input_tokens=2, output_tokens=1, quality_passed=latency < 50)
    summary = metrics.summary("decision")
    assert summary["quality_pass_rate"] == 0.8
    assert summary["p95_ms"] == 50


def test_memory_refresh_is_explicit_and_allowed_only_once() -> None:
    planner = RequestPlanner()
    turn = _turn()
    first, _ = planner.build(
        turn,
        (_section("current_message", "first", 100),),
        model="fake-model",
        instruction_version="v1",
        tool_schema_hash="tools-v1",
    )
    refreshed, reused = planner.build(
        turn,
        (_section("current_message", "refreshed", 100),),
        model="fake-model",
        instruction_version="v1",
        tool_schema_hash="tools-v1",
        memory_refresh=True,
    )

    assert reused is False
    assert first.context.fingerprint != refreshed.context.fingerprint
    with pytest.raises(ContractValidationError, match="at most once"):
        planner.build(
            turn,
            (_section("current_message", "again", 100),),
            model="fake-model",
            instruction_version="v1",
            tool_schema_hash="tools-v1",
            memory_refresh=True,
        )
