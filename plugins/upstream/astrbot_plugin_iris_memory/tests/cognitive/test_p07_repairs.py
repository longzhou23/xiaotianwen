"""P0.7 fact-integrity repair tests.

These are intentionally narrow regression tests for the P0.7 repair scope.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from iris_memory.cognitive.behavior import CognitiveBehaviorRuntime
from iris_memory.cognitive.contracts import (
    CanonicalExperience,
    EntityReference,
    Perspective,
    ResolvedEvent,
    RuntimeMode,
    to_json_safe,
)
from iris_memory.cognitive.iris_adapter import CognitiveRuntime
from iris_memory.cognitive.situation import SituationBuilder
from iris_memory.l1_buffer import L1Buffer
from iris_memory.l1_buffer.models import ContextMessage as L1ContextMessage


def _actor() -> EntityReference:
    return EntityReference("person:qq:1", "platform_uid", 1.0, ("qq:1",))


def _experience(
    event_id: str,
    content: str = "今晚几点观测？",
    *,
    session_id: str = "g1",
    at: datetime | None = None,
) -> CanonicalExperience:
    event = ResolvedEvent(
        event_id=event_id,
        source="qq",
        occurred_at=at or datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc),
        session_id=session_id,
        mode="private",
        content=content,
        actor=_actor(),
    )
    return CanonicalExperience(
        id=f"experience:{event_id}",
        event=event,
        subject=_actor(),
        perspective=Perspective.INTERPERSONAL,
        provenance=("test",),
    )


def test_json_safe_cognitive_metadata_roundtrip():
    from iris_memory.cognitive.contracts import IrisPreprocessResult

    meta = {"owner": "adapter", "resolved_entities": ["person:qq:2"], "nested": {"x": (1, 2)}}
    safe = to_json_safe(meta)
    msg = L1ContextMessage(
        role="user",
        content="x",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        token_count=1,
        source="u",
        metadata={"cognitive_runtime": safe},
    )
    payload = json.dumps(msg.to_dict(), ensure_ascii=False)
    assert payload


def test_experience_cache_is_bounded_and_recent_event_can_still_correlate(monkeypatch):
    runtime = CognitiveRuntime()
    monkeypatch.setattr(CognitiveRuntime, "_MAX_EXPERIENCES", 5)
    for i in range(20):
        runtime.run_behavior(_experience(f"qq:cache-{i}"), runtime_mode=RuntimeMode.SHADOW)
    assert len(runtime._experiences) <= 5
    last = runtime.run_behavior(_experience("qq:cache-last"), runtime_mode=RuntimeMode.SHADOW)
    record = runtime.observe_host_output(last, "host reply", legacy_fallthrough=True)
    assert record.host_result.output_nonempty is True
    # The last experience is still correlated so SELF action state updates.
    next_event = runtime.run_behavior(_experience("qq:cache-next", session_id="g1"), runtime_mode=RuntimeMode.SHADOW)
    assert next_event.trace.situation_lite.recent_self_action == "HOST_OUTPUT"


def test_dispatch_observation_is_idempotent():
    runtime = CognitiveRuntime()
    proposal = runtime.run_behavior(_experience("qq:dispatch-idem"), runtime_mode=RuntimeMode.SHADOW)
    host = runtime.observe_host_output(proposal, "host", legacy_fallthrough=True)
    first_dispatch = runtime.observe_dispatch(host)
    second_dispatch = runtime.observe_dispatch(first_dispatch)
    assert first_dispatch is second_dispatch
    assert first_dispatch.host_result.dispatch_observed is True
    assert first_dispatch.revision == 2
    assert first_dispatch.stage.value == "DISPATCH"


def test_compatibility_fact_fabrication_apis_are_removed():
    runtime = CognitiveRuntime()
    assert not hasattr(runtime, "complete_behavior")
    behavior_runtime = CognitiveBehaviorRuntime(self_entity="agent:xiaotianwen")
    # Kept only as an explicit fail-closed quarantine stub.
    with pytest.raises(Exception):
        behavior_runtime.complete_realization(
            behavior_runtime.run(_experience("qq:quarantine")), "text"
        )


def test_out_of_order_timestamp_does_not_reset_velocity_to_one():
    builder = SituationBuilder()
    base = datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc)
    first = builder.observe(_experience("qq:o1", "a", session_id="g1", at=base + timedelta(seconds=100)))
    second = builder.observe(_experience("qq:o2", "b", session_id="g1", at=base + timedelta(seconds=10)))
    # second has an earlier timestamp than first; P0.7 keeps the observation
    # clock monotonic and does not reset velocity to 1.
    assert second.message_velocity == 2
    assert second.last_activity_at == first.last_activity_at


def test_run_behavior_accepts_event_scoped_runtime_mode_snapshot():
    runtime = CognitiveRuntime(runtime_mode=RuntimeMode.GUARD)
    result = runtime.run_behavior(
        _experience("qq:snapshot"),
        runtime_mode=RuntimeMode.SHADOW,
    )
    assert result.trace.runtime_mode is RuntimeMode.SHADOW
    runtime.runtime_mode = RuntimeMode.GUARD
    assert result.trace.runtime_mode is RuntimeMode.SHADOW
    assert not runtime.should_guard_block(result)


@pytest.mark.asyncio
async def test_l1_to_l2_self_metadata_propagation(monkeypatch):
    from iris_memory.l1_buffer.buffer import get_config

    captured: dict[str, dict] = {}

    class FakeRetriever:
        def __init__(self, *args, **kwargs):
            pass

        async def add_from_summary(self, content, metadata, persona_id):
            captured["metadata"] = metadata
            return "mem:1"

    buffer = L1Buffer()
    buffer._component_manager = MagicMock()
    buffer._component_manager.get_component.return_value = SimpleNamespace(is_available=True)

    config = MagicMock()
    config.get = lambda key, default=None: True if key == "l2_memory.enable" else default
    monkeypatch.setattr("iris_memory.l1_buffer.buffer.get_config", lambda: config)
    monkeypatch.setattr("iris_memory.l2_memory.MemoryRetriever", FakeRetriever)

    messages = [
        L1ContextMessage(
            role="assistant",
            content="小天文曾经组织过观测",
            timestamp=datetime(2026, 9, 2, 10, 1, tzinfo=timezone.utc),
            token_count=5,
            source="assistant",
            metadata={
                "cognitive_runtime": {
                    "subject_entity": "agent:xiaotianwen",
                    "perspective": "autobiographical",
                    "source": "explicit_self_output",
                }
            },
            persona_id="default",
        )
    ]
    summary = '{"memories":[{"content":"小天文曾经组织过观测","confidence":"high"}]}'
    await buffer._write_summary_to_l2("g1", messages, summary)
    assert captured["metadata"]["cognitive_runtime"]["subject_entity"] == "agent:xiaotianwen"
    assert captured["metadata"]["cognitive_runtime"]["source"] == "aggregated_l1_summary"


@pytest.mark.asyncio
async def test_l1_to_l2_mixed_subject_is_not_forced_self(monkeypatch):
    from iris_memory.l1_buffer.buffer import get_config

    captured: dict[str, dict] = {}

    class FakeRetriever:
        def __init__(self, *args, **kwargs):
            pass

        async def add_from_summary(self, content, metadata, persona_id):
            captured["metadata"] = metadata
            return "mem:2"

    buffer = L1Buffer()
    buffer._component_manager = MagicMock()
    buffer._component_manager.get_component.return_value = SimpleNamespace(is_available=True)

    config = MagicMock()
    config.get = lambda key, default=None: True if key == "l2_memory.enable" else default
    monkeypatch.setattr("iris_memory.l1_buffer.buffer.get_config", lambda: config)
    monkeypatch.setattr("iris_memory.l2_memory.MemoryRetriever", FakeRetriever)

    messages = [
        L1ContextMessage(
            role="user",
            content="张三问过",
            timestamp=datetime(2026, 9, 2, 10, 1, tzinfo=timezone.utc),
            token_count=5,
            source="u1",
            metadata={"cognitive_runtime": {"subject_entity": "person:qq:u1"}},
            persona_id="default",
        ),
        L1ContextMessage(
            role="assistant",
            content="小天文回答过",
            timestamp=datetime(2026, 9, 2, 10, 2, tzinfo=timezone.utc),
            token_count=5,
            source="assistant",
            metadata={"cognitive_runtime": {"subject_entity": "agent:xiaotianwen"}},
            persona_id="default",
        ),
    ]
    summary = '{"memories":[{"content":"小天文曾经组织过观测","confidence":"high"}]}'
    await buffer._write_summary_to_l2("g1", messages, summary)
    assert captured["metadata"]["cognitive_runtime"]["subject_entity"] == "unresolved/mixed"


def test_to_json_safe_recurses_into_plain_lists():
    from iris_memory.cognitive.contracts import to_json_safe

    safe = to_json_safe({"a": [{"b": (1, 2)}]})
    assert safe == {"a": [{"b": [1, 2]}]}


def test_run_behavior_snapshots_mode_at_entry_even_without_explicit_arg(monkeypatch):
    from unittest.mock import patch

    rt = CognitiveRuntime(runtime_mode=RuntimeMode.SHADOW)
    orig = rt.behavior.run

    def evil_run(experience, legacy=None):
        rt.runtime_mode = RuntimeMode.GUARD
        return orig(experience, legacy)

    with patch.object(rt.behavior, "run", side_effect=evil_run):
        result = rt.run_behavior(_experience("qq:snapshot-entry"), runtime_mode=RuntimeMode.SHADOW)
        assert result.trace.runtime_mode is RuntimeMode.SHADOW
