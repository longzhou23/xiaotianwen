"""P2r.0.3b factual capture integration tests."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from iris_memory.cognitive import reply_link_capture as capture_module
from iris_memory.cognitive.contracts import (
    BehaviorExecutionRecord,
    BehaviorTrace,
    DivergenceType,
    GroundingEnforcement,
    HostResult,
    OutputProducer,
    OutputState,
    ShadowComparison,
    TraceStage,
    TriggerDecision,
)
from iris_memory.cognitive.episode import (
    Episode,
    EpisodeEventKind,
    EpisodeEventRef,
    EpisodeState,
)
from iris_memory.cognitive.episode_shadow import EpisodeShadowObserver
from iris_memory.cognitive.episode_store import InMemoryEpisodeStore
from iris_memory.cognitive.iris_adapter import CognitiveRuntime
from iris_memory.cognitive.reply_link_authority import P2r0Store


class _Operation:
    def __init__(self, index: int, *, kind: str = "MESSAGE", status: str = "SEND_SUCCEEDED_WITH_IDENTITY", message_id: str | None = None):
        self.schema_version = "astrbot.platform-send-receipt.v1"
        self.operation_index = index
        self.operation_kind = SimpleNamespace(value=kind)
        self.status = SimpleNamespace(value=status)
        self.platform_id = "napcat-instance-1"
        self.account_id = "bot-1"
        self.conversation_id = "group-1"
        self.platform_message_id = message_id or str(100 + index)


class _Result:
    schema_version = "astrbot.host-send-result.v1"

    def __init__(self, operations):
        self.operations = tuple(operations)


class _Reply:
    def __init__(self, message_id: str | int):
        self.id = message_id


class _Event:
    def __init__(self, current, *, messages=None, message_id="in-1", group_id="group-1"):
        self._current = current
        self._messages = list(messages or [])
        self.message_obj = SimpleNamespace(message_id=message_id)
        self._group_id = group_id

    def get_extra(self, key):
        return self._current if key == "iris_cognitive_execution_record" else None

    def get_messages(self):
        return self._messages

    def get_platform_id(self):
        return "napcat-instance-1"

    def get_self_id(self):
        return "bot-1"

    def get_group_id(self):
        return self._group_id

    def get_sender_id(self):
        return "user-1"


def _service(tmp_path: Path):
    episode_store = InMemoryEpisodeStore()
    observer = EpisodeShadowObserver(episode_store)
    runtime = CognitiveRuntime(record_traces=False, episode_observer=observer)
    trace = BehaviorTrace(
        event_id="napcat:in-1",
        trigger=TriggerDecision(True, "test", 1),
        participation=None,
        intent=None,
        grounding=None,
        exit_reason=None,
    )
    object.__setattr__(trace, "trace_id", "trace:capture")
    host = HostResult(
        legacy_fallthrough=True,
        output_generated=True,
        output_nonempty=True,
        dispatch_observed=False,
        output_state=OutputState.OUTPUT_READY,
        producer=OutputProducer.LEGACY_HOST,
        applied_enforcement=GroundingEnforcement.NOT_APPLIED,
    )
    comparison = ShadowComparison(True, True, None, True, True, DivergenceType.MATCH_REPLY)
    rev1 = BehaviorExecutionRecord(trace, host, comparison, TraceStage.HOST_OUTPUT, 1)
    rev2 = BehaviorExecutionRecord(trace, replace(host, dispatch_observed=True), comparison, TraceStage.DISPATCH, 2)
    runtime.execution_observatory.record(rev1)
    runtime.execution_observatory.record(rev2)
    episode_store.create_episode(Episode(
        episode_id="episode:capture",
        scope_id="group-1",
        state=EpisodeState.OPEN,
        root_event_id=trace.event_id,
        opened_at=rev1.updated_at,
        last_activity_at=rev1.updated_at,
        event_refs=(EpisodeEventRef(
            ref_id="HOST_OUTPUT:napcat:in-1:trace:capture:trace:capture:1",
            kind=EpisodeEventKind.HOST_OUTPUT,
            source_event_id=trace.event_id,
            trace_id=trace.trace_id,
            execution_record_id="trace:capture:1",
            observed_at=rev1.updated_at,
        ),),
    ))
    runtime.pre_adapter = SimpleNamespace(
        attached=lambda _event: SimpleNamespace(experience=SimpleNamespace(event=SimpleNamespace(event_id=trace.event_id))),
        attach=lambda _event: SimpleNamespace(experience=SimpleNamespace(event=SimpleNamespace(event_id=trace.event_id))),
    )
    service = capture_module.P2r0CaptureService(P2r0Store(tmp_path / "facts.jsonl"), runtime)
    return service, rev2


@pytest.fixture
def fake_h0(monkeypatch):
    monkeypatch.setattr(capture_module, "_host_receipt_types", lambda: (_Result,))
    monkeypatch.setattr(capture_module, "_reply_type", lambda: _Reply)


def test_host_capture_one_eligible_operation_and_replay(tmp_path, fake_h0):
    service, current = _service(tmp_path)
    result = service.capture_host_send_result(_Event(current), _Result([_Operation(0)]))
    assert result.host_facts == 1
    assert len(service.store.host_output_facts) == 1
    reopened = P2r0Store(tmp_path / "facts.jsonl")
    assert len(reopened.host_output_facts) == 1


@pytest.mark.parametrize("kind,status", [
    ("FORWARD_MESSAGE", "SEND_SUCCEEDED_WITH_IDENTITY"),
    ("OTHER", "SEND_SUCCEEDED_WITH_IDENTITY"),
    ("MESSAGE", "SEND_FAILED"),
    ("MESSAGE", "SEND_SUCCEEDED_IDENTITY_UNAVAILABLE"),
    ("MESSAGE", "SEND_SUCCEEDED_IDENTITY_NAMESPACE_INCOMPLETE"),
])
def test_host_capture_rejects_ineligible_operations(tmp_path, fake_h0, kind, status):
    service, current = _service(tmp_path)
    result = service.capture_host_send_result(_Event(current), _Result([_Operation(0, kind=kind, status=status)]))
    assert result.host_facts == 0
    assert service.store.host_output_facts == ()


def test_host_capture_preserves_segmented_message_operations(tmp_path, fake_h0):
    service, current = _service(tmp_path)
    result = service.capture_host_send_result(_Event(current), _Result([_Operation(0), _Operation(1)]))
    assert result.host_facts == 2
    assert {fact.operation_index for fact in service.store.host_output_facts} == {0, 1}


def test_host_capture_fails_closed_without_rev1_or_with_generic_platform(tmp_path, fake_h0):
    service, current = _service(tmp_path)
    service._runtime.execution_observatory.clear()
    assert service.capture_host_send_result(_Event(current), _Result([_Operation(0)])).host_facts == 0

    service, current = _service(tmp_path / "generic")
    operation = _Operation(0)
    operation.platform_id = "qq"
    assert service.capture_host_send_result(_Event(current), _Result([operation])).host_facts == 0


def test_host_capture_rejects_dispatch_record_as_current_lineage(tmp_path, fake_h0):
    service, current = _service(tmp_path)
    event = _Event(replace(current, revision=3), messages=[])
    assert service.capture_host_send_result(event, _Result([_Operation(0)])).host_facts == 0


def _replace_capture_episode_ref(service, ref: EpisodeEventRef) -> None:
    episode_store = service._runtime.episode_observer.store
    episode = episode_store.get_episode("episode:capture")
    assert episode is not None
    # Keep the trace index intact while replacing the immutable Episode value.
    episode_store._episodes[episode.episode_id] = replace(episode, event_refs=(ref,))


def test_streaming_host_capture_accepts_authoritative_rev1_without_dispatch(tmp_path, fake_h0):
    service, _current = _service(tmp_path)
    rev1 = next(record for record in service._runtime.execution_observatory.recent()
                if record.stage is TraceStage.HOST_OUTPUT)
    service._runtime.execution_observatory.clear()
    service._runtime.execution_observatory.record(rev1)

    result = service.capture_host_send_result(_Event(rev1), _Result([_Operation(0)]))

    assert result.host_facts == 1
    assert service.store.host_output_facts[0].dispatch_execution_record_id is None


def test_streaming_host_capture_rejects_missing_rev1_authority(tmp_path, fake_h0):
    service, _current = _service(tmp_path)
    rev1 = next(record for record in service._runtime.execution_observatory.recent()
                if record.stage is TraceStage.HOST_OUTPUT)
    service._runtime.execution_observatory.clear()
    assert service.capture_host_send_result(_Event(rev1), _Result([_Operation(0)])).host_facts == 0


@pytest.mark.parametrize("mutator", [
    pytest.param(lambda ref: replace(ref, execution_record_id=None), id="missing_execution_id"),
    pytest.param(lambda ref: replace(ref, kind=EpisodeEventKind.COGNITIVE_PROPOSAL), id="wrong_kind"),
    pytest.param(lambda ref: replace(ref, trace_id="trace:wrong"), id="wrong_trace"),
    pytest.param(lambda ref: replace(ref, source_event_id="event:wrong"), id="wrong_event"),
    pytest.param(lambda ref: replace(ref, execution_record_id="trace:capture:99"), id="wrong_revision"),
])
def test_streaming_host_capture_rejects_invalid_episode_attachment(tmp_path, fake_h0, mutator):
    service, _current = _service(tmp_path)
    rev1 = next(record for record in service._runtime.execution_observatory.recent()
                if record.stage is TraceStage.HOST_OUTPUT)
    ref = service._runtime.episode_observer.store.get_episode("episode:capture").event_refs[0]
    _replace_capture_episode_ref(service, mutator(ref))

    assert service.capture_host_send_result(_Event(rev1), _Result([_Operation(0)])).host_facts == 0


def test_streaming_host_capture_rejects_non_host_revision(tmp_path, fake_h0):
    service, current = _service(tmp_path)
    dispatch_stage = replace(current, stage=TraceStage.DISPATCH, revision=1)
    proposal_stage = replace(current, stage=TraceStage.PROPOSAL, revision=0)
    assert service.capture_host_send_result(_Event(dispatch_stage), _Result([_Operation(0)])).host_facts == 0
    assert service.capture_host_send_result(_Event(proposal_stage), _Result([_Operation(0)])).host_facts == 0


def test_streaming_host_capture_rejects_synthetic_rev1_not_in_registry(tmp_path, fake_h0):
    service, _current = _service(tmp_path)
    rev1 = next(record for record in service._runtime.execution_observatory.recent()
                if record.stage is TraceStage.HOST_OUTPUT)
    service._runtime.execution_observatory.clear()
    synthetic = replace(rev1, updated_at=rev1.updated_at)
    assert service.capture_host_send_result(_Event(synthetic), _Result([_Operation(0)])).host_facts == 0


def test_streaming_multi_operation_and_idempotence(tmp_path, fake_h0):
    service, _current = _service(tmp_path)
    rev1 = next(record for record in service._runtime.execution_observatory.recent()
                if record.stage is TraceStage.HOST_OUTPUT)
    service._runtime.execution_observatory.clear()
    service._runtime.execution_observatory.record(rev1)
    result = _Result([_Operation(0), _Operation(1, status="SEND_FAILED"), _Operation(2)])

    first = service.capture_host_send_result(_Event(rev1), result)
    second = service.capture_host_send_result(_Event(rev1), result)

    assert first.host_facts == 2
    assert second.host_facts == 2
    assert {fact.operation_index for fact in service.store.host_output_facts} == {0, 2}


def _add_streaming_episode(service, base_record: BehaviorExecutionRecord, *, trace_id: str, event_id: str, episode_id: str):
    trace = replace(base_record.trace, event_id=event_id)
    object.__setattr__(trace, "trace_id", trace_id)
    record = replace(base_record, trace=trace, stage=TraceStage.HOST_OUTPUT, revision=1)
    service._runtime.execution_observatory.record(record)
    episode_store = service._runtime.episode_observer.store
    episode_store.create_episode(Episode(
        episode_id=episode_id,
        scope_id="group-1",
        state=EpisodeState.OPEN,
        root_event_id=event_id,
        opened_at=record.updated_at,
        last_activity_at=record.updated_at,
        event_refs=(EpisodeEventRef(
            ref_id=f"HOST_OUTPUT:{event_id}:{trace_id}:1",
            kind=EpisodeEventKind.HOST_OUTPUT,
            source_event_id=event_id,
            trace_id=trace_id,
            execution_record_id=f"{trace_id}:1",
            observed_at=record.updated_at,
        ),),
    ))
    return record


def test_streaming_interleaved_receipts_select_exact_trace(tmp_path, fake_h0):
    service, _current = _service(tmp_path)
    record_a = next(record for record in service._runtime.execution_observatory.recent()
                    if record.stage is TraceStage.HOST_OUTPUT)
    service._runtime.execution_observatory.clear()
    service._runtime.execution_observatory.record(record_a)
    record_b = _add_streaming_episode(
        service, record_a, trace_id="trace:other", event_id="napcat:in-other", episode_id="episode:other"
    )

    assert service.capture_host_send_result(_Event(record_a), _Result([_Operation(0)])).host_facts == 1
    assert service.capture_host_send_result(_Event(record_b), _Result([_Operation(1)])).host_facts == 1
    assert {fact.source_event_id for fact in service.store.host_output_facts} == {
        "napcat:in-1", "napcat:in-other"
    }


def test_streaming_capture_failure_isolated_from_send(tmp_path, fake_h0):
    service, _current = _service(tmp_path)
    record = next(item for item in service._runtime.execution_observatory.recent()
                  if item.stage is TraceStage.HOST_OUTPUT)
    service._runtime.execution_observatory.clear()
    service._runtime.execution_observatory.record(record)

    calls = 0

    def fail(_fact):
        nonlocal calls
        calls += 1
        raise OSError("streaming capture write failed")

    service.store.record_host_output_fact = fail
    result = service.capture_host_send_result(_Event(record), _Result([_Operation(0)]))
    assert result.host_facts == 0
    assert calls == 1


def test_inbound_capture_deduplicates_same_reply_and_rejects_ambiguity(tmp_path, fake_h0):
    service, current = _service(tmp_path)
    duplicate = service.capture_inbound(_Event(current, messages=[_Reply(42), _Reply("42")]))
    assert duplicate.inbound_facts == 1
    ambiguous = service.capture_inbound(_Event(current, messages=[_Reply(42), _Reply(43)], message_id="in-2"))
    assert ambiguous.ambiguous_reply is True
    assert len(service.store.inbound_reply_facts) == 1


def test_inbound_capture_without_reply_is_empty(tmp_path, fake_h0):
    service, current = _service(tmp_path)
    assert service.capture_inbound(_Event(current)).inbound_facts == 0


def test_inbound_capture_conflicting_target_is_fail_closed(tmp_path, fake_h0):
    service, current = _service(tmp_path)
    assert service.capture_inbound(_Event(current, messages=[_Reply(42)])).inbound_facts == 1
    assert service.capture_inbound(_Event(current, messages=[_Reply(43)])).inbound_facts == 0
    assert len(service.store.inbound_reply_facts) == 1


def test_host_capture_persistence_failure_does_not_repeat_send(tmp_path, fake_h0):
    service, current = _service(tmp_path)
    calls = 0

    def fail(_fact):
        nonlocal calls
        calls += 1
        raise OSError("capture write failed")

    service.store.record_host_output_fact = fail
    result = service.capture_host_send_result(_Event(current), _Result([_Operation(0)]))
    assert result.host_facts == 0
    assert calls == 1


def test_inbound_capture_uses_group_or_private_conversation(tmp_path, fake_h0):
    service, current = _service(tmp_path)
    group = service.capture_inbound(_Event(current, messages=[_Reply(9)]))
    assert group.inbound_facts == 1
    service, current = _service(tmp_path / "private")
    private_event = _Event(current, messages=[_Reply(10)], message_id="in-private", group_id="")
    private = service.capture_inbound(private_event)
    assert private.inbound_facts == 1
    facts = service.store.inbound_reply_facts
    assert any(f.source_platform_message_identity.conversation_id == "user-1" for f in facts)


def test_capture_without_store_is_not_constructible(tmp_path):
    runtime = CognitiveRuntime(record_traces=False)
    with pytest.raises(TypeError):
        capture_module.P2r0CaptureService(object(), runtime)
