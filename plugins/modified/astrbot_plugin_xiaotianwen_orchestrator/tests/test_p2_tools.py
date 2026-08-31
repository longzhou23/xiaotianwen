from __future__ import annotations

import asyncio

from astrbot_plugin_xiaotianwen_orchestrator.contracts import ToolExecutionPolicy
from astrbot_plugin_xiaotianwen_orchestrator.p2 import (
    ToolCall,
    ToolExecutor,
    ToolRegistry,
    ToolSpec,
    trim_tool_result,
)


def test_read_single_flight_is_bounded_and_result_order_is_preserved() -> None:
    async def scenario() -> tuple[tuple[object, ...], ToolExecutor, list[str]]:
        registry = ToolRegistry()
        registry.register(ToolSpec("lookup", ToolExecutionPolicy("read", True, True, 60, 100), tool_result_chars=100))
        registry.register(ToolSpec("write_state", ToolExecutionPolicy("write", False, False, 0, 100), tool_result_chars=100))
        executor = ToolExecutor(registry, max_read_concurrency=3)
        active = 0
        seen: list[str] = []

        async def handler(call: ToolCall) -> object:
            nonlocal active
            active += 1
            seen.append(str(call.arguments.get("key", call.arguments.get("value", ""))))
            await asyncio.sleep(0.002)
            active -= 1
            return {"key": call.arguments.get("key", ""), "value": "x" * 200}

        calls = (
            ToolCall("call-001", "lookup", {"key": "same"}),
            ToolCall("call-002", "lookup", {"key": "same"}),
            ToolCall("call-003", "lookup", {"key": "other"}),
            ToolCall("call-004", "lookup", {"key": "third"}),
            ToolCall("call-005", "write_state", {"value": "once"}),
            ToolCall("call-006", "write_state", {"value": "once"}),
        )
        results = await executor.execute(calls, handler, provider_limit=32)
        return tuple(result.structural_summary() for result in results), executor, seen

    summaries, executor, seen = asyncio.run(scenario())
    assert [item["index"] for item in summaries] == list(range(6))
    assert summaries[0]["status"] == "completed"
    assert summaries[1]["status"] == "deduplicated"
    assert summaries[4]["status"] == "completed"
    assert summaries[5]["status"] == "duplicate_suppressed"
    assert seen.count("same") == 1
    assert executor.max_observed_read_concurrency <= 3
    assert all(item["result_chars"] <= 32 for item in summaries)


def test_same_send_parameters_are_deduplicated_but_different_media_are_allowed() -> None:
    async def scenario() -> tuple[object, ...]:
        registry = ToolRegistry()
        registry.register(ToolSpec("send_meme", ToolExecutionPolicy("send", False, False, 0, 100)))
        executor = ToolExecutor(registry)
        calls = (
            ToolCall("call-011", "send_meme", {"media_id": "meme-1"}),
            ToolCall("call-012", "send_meme", {"media_id": "meme-1"}),
            ToolCall("call-013", "send_meme", {"media_id": "meme-2"}),
        )
        results = await executor.execute(calls, lambda call: {"sent": call.arguments["media_id"]})
        return tuple(result.structural_summary() for result in results)

    results = asyncio.run(scenario())
    assert [item["status"] for item in results] == ["completed", "duplicate_suppressed", "completed"]


def test_unknown_tool_is_conservative_and_tool_trim_precedes_provider_cap() -> None:
    registry = ToolRegistry()
    assert registry.spec_for("unknown_tool").policy.effect == "write"
    assert trim_tool_result({"text": "x" * 200}, tool_limit=80, provider_limit=20)
    assert len(trim_tool_result("x" * 200, tool_limit=80, provider_limit=20)) == 20
