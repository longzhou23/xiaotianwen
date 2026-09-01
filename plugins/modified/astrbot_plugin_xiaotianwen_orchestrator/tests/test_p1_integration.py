from __future__ import annotations

import json

import pytest

from astrbot_plugin_xiaotianwen_orchestrator.contracts import TurnEnvelope
from astrbot_plugin_xiaotianwen_orchestrator.ingress import OrchestratorMode, event_to_envelope
from astrbot_plugin_xiaotianwen_orchestrator.integration import (
    AstrBotObservationAdapter,
    FakeAstrBotRuntime,
    ObservationAdapter,
    RuntimeObservationStore,
)


def _event(message_id: str = "m-001") -> dict[str, object]:
    return {
        "message_id": message_id,
        "user_id": "u-001",
        "group_id": "g-001",
        "raw_message": "看一下这张图",
        "reply": {"message_id": "m-000"},
        "message": [
            {"type": "image", "data": {"image_id": "img-001", "file": "/synthetic/img-001.bin"}},
            {"type": "text", "data": {"text": "看一下这张图"}},
            {"type": "forward", "data": {"id": "forward-001"}},
        ],
    }


def test_explicit_astrbot_adapter_records_shape_without_prompt_body() -> None:
    store = RuntimeObservationStore("p1-adapter-test")
    adapter = AstrBotObservationAdapter(ObservationAdapter(store, source="ASTRBOT_ADAPTER"))

    class Request:
        contexts = [{"role": "user", "content": "private prompt"}]
        prompt = "system prompt api_key=do-not-record"
        stream = True
        tools = [{"name": "read"}]

    class Usage:
        input_tokens = 12
        output_tokens = 4
        total_tokens = 16

    class Response:
        finish_reason = "stop"
        usage = Usage()
        tool_calls = []

    adapter.provider_request(Request(), request_id="request-0001", role="main_reply", model="fake-model")
    adapter.provider_response(Response(), request_id="request-0001", role="main_reply")
    adapter.log("INFO", "provider api_key=do-not-record", request_id="request-0001")

    rendered = json.dumps(store.to_dicts(), ensure_ascii=False)
    assert "private prompt" not in rendered
    assert "do-not-record" not in rendered
    assert "prompt_chars" in rendered
    assert "input_tokens" in rendered


def test_astrbot_427_entities_are_adapted_without_using_prompt_bodies() -> None:
    """Exercise the fields used by the installed AstrBot 4.27.x entities."""

    pytest.importorskip(
        "astrbot",
        reason="requires an installed AstrBot runtime; exercised again in the Azure container gate",
    )
    from astrbot.core.agent.tool import FunctionTool
    from astrbot.core.provider.entities import LLMResponse, ProviderRequest, TokenUsage
    from astrbot.core.provider.entities import ToolSet

    store = RuntimeObservationStore("p1-astrbot-entities")
    adapter = AstrBotObservationAdapter(ObservationAdapter(store, source="ASTRBOT_ADAPTER"))
    function_tool = FunctionTool(
        name="synthetic_lookup",
        description="Synthetic test tool",
        parameters={"type": "object", "properties": {}},
    )
    request = ProviderRequest(
        prompt="private prompt api_key=do-not-record",
        contexts=[{"role": "user", "content": "private context"}],
        func_tool=ToolSet(tools=[function_tool]),
        model="astrbot-4.27-test",
    )
    response = LLMResponse(
        "assistant",
        tools_call_args=[{"query": "private"}],
        tools_call_name=["synthetic_lookup"],
        tools_call_ids=["call-actual-0001"],
        usage=TokenUsage(input_other=10, input_cached=2, output=4),
    )

    adapter.provider_request(request, request_id="request-actual", role="main_reply", stream=True)
    adapter.provider_response(response, request_id="request-actual", role="main_reply")

    records = store.to_dicts()
    rendered = json.dumps(records, ensure_ascii=False)
    started = next(item for item in records if item["kind"] == "request.started")
    completed = next(item for item in records if item["kind"] == "request.completed")
    assert started["payload"]["model"] == "astrbot-4.27-test"
    assert started["payload"]["tool_count"] == 1
    assert completed["payload"]["finish_reason"] == "tool_calls"
    assert completed["payload"]["tool_call_count"] == 1
    assert completed["payload"]["usage"] == {"input_other": 10, "input_cached": 2, "output": 4}
    assert "private prompt" not in rendered
    assert "private context" not in rendered
    assert "private" not in rendered


def test_fake_runtime_exercises_stream_usage_and_delivery_with_correlations() -> None:
    runtime = FakeAstrBotRuntime(run_id="p1-runtime-test", provider_outcome="stream")
    result = runtime.submit_event(_event(), now=100.0)
    completed = runtime.flush(now=103.1)

    assert result is not None
    assert len(completed) == 1
    assert [call["role"] for call in runtime.provider.calls] == ["main_reply"]
    observations = runtime.observations()
    kinds = [item["kind"] for item in observations]
    assert "ui.input.received" in kinds
    assert "request.chunk" in kinds
    assert "request.completed" in kinds
    assert "context.assembled" in kinds
    assert "audit.completed" in kinds
    assert "delivery.completed" in kinds
    assert all(item["schema_version"] == 2 for item in observations)
    assert all(item["run_id"] == "p1-runtime-test" for item in observations)
    assert all(item["request_id"] or item["kind"] in {"ui.input.received", "onebot.event.normalized", "turn.started", "turn.merged", "turn.ready", "turn.ownership", "turn.responding", "turn.completed", "context.section.added", "context.assembled", "log.emitted"} for item in observations)


def test_fake_runtime_tool_continuation_keeps_parent_request_and_call_id() -> None:
    runtime = FakeAstrBotRuntime(run_id="p1-tool-test", provider_outcome="tool")
    runtime.submit_event(_event(), now=10.0)
    runtime.flush(now=13.0)

    calls = runtime.provider.calls
    assert [call["role"] for call in calls] == ["main_reply", "tool_continuation"]
    assert calls[1]["parent_request_id"] == calls[0]["request_id"]
    tool_events = [item for item in runtime.observations() if item["kind"] == "tool.completed"]
    assert len(tool_events) == 1
    assert tool_events[0]["call_id"] == "call-0001"


def test_shadow_mode_and_disconnected_onebot_have_no_provider_or_delivery() -> None:
    shadow = FakeAstrBotRuntime(run_id="p1-shadow-test", mode=OrchestratorMode.SHADOW)
    shadow.submit_event(_event(), now=1.0)
    shadow.flush(now=4.0)
    assert shadow.provider.calls == []
    assert not any(item["kind"] == "request.started" for item in shadow.observations())
    assert any(item["kind"] == "turn.shadow_skipped" for item in shadow.observations())

    disconnected = FakeAstrBotRuntime(run_id="p1-disconnected-test")
    disconnected.onebot.disconnect(timestamp=1.0)
    disconnected.submit_event(_event("m-002"), now=2.0)
    records = disconnected.observations()
    assert any(item["kind"] == "onebot.event.dropped" and item["capture_mode"] == "NOT_CONNECTED" for item in records)
    assert not any(item["kind"] == "request.started" for item in records)


def test_ingress_contract_keeps_reply_and_media_structure() -> None:
    turn = event_to_envelope(_event(), received_at=5.0)
    assert isinstance(turn, TurnEnvelope)
    assert turn.reply_to == "m-000"
    assert [item.media_id for item in turn.media] == ["img-001"]
    assert turn.media[0].order == 0


def test_observation_log_is_structural_by_default_and_store_prunes_by_time() -> None:
    store = RuntimeObservationStore("p1-retention", retention_seconds=5)
    adapter = ObservationAdapter(store, source="FAKE_ASTRBOT")
    adapter.log(level="INFO", message="Authorization: should-not-be-stored", timestamp=0.0)
    adapter.log(level="INFO", message="safe synthetic state", timestamp=3.0)
    adapter.log(level="INFO", message="new synthetic state", timestamp=6.0)

    rendered = json.dumps(store.to_dicts(), ensure_ascii=False)
    assert "should-not-be-stored" not in rendered
    assert all("display" not in item["payload"] for item in store.to_dicts())
    assert len(store.snapshot()) == 2
    assert store.dropped_count == 1
