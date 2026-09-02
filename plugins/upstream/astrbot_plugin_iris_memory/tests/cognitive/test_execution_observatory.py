"""Bounded runtime execution observability and Observatory integration tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from iris_memory.cognitive.contracts import BehaviorLoopResult, BehaviorTrace, TriggerDecision
from iris_memory.cognitive.episode import Episode, EpisodeEventKind, EpisodeEventRef, EpisodeState, make_episode_event_ref_id
from iris_memory.cognitive.episode_store import InMemoryEpisodeStore
from iris_memory.cognitive.execution_observatory import ExecutionRecordObservatory
from iris_memory.cognitive.iris_adapter import CognitiveRuntime
from iris_memory.cognitive.outcome import OutcomeExplicitness, OutcomeKind, OutcomeObservation
from iris_memory.cognitive.review import EvidenceSourceType
from iris_memory.cognitive.review_service import ReviewInputSnapshot
from iris_memory.web.services.observatory_service import P1ObservatoryService


NOW = datetime(2026, 9, 2, 12, tzinfo=timezone.utc)


def _trace(event_id: str) -> BehaviorTrace:
    return BehaviorTrace(event_id, TriggerDecision(True, "test", 1), None, None, None, None)


def _host_ref(record) -> EpisodeEventRef:
    trace = record.trace
    return EpisodeEventRef(
        make_episode_event_ref_id(
            EpisodeEventKind.HOST_OUTPUT,
            source_event_id=trace.event_id,
            trace_id=trace.trace_id,
            execution_record_id=f"{trace.trace_id}:{record.revision}",
        ),
        EpisodeEventKind.HOST_OUTPUT,
        trace.event_id,
        trace.trace_id,
        f"{trace.trace_id}:{record.revision}",
        record.updated_at,
    )


def test_real_runtime_execution_is_recorded_only_after_completion():
    runtime = CognitiveRuntime(record_traces=False)
    result = BehaviorLoopResult(_trace("event:real"))
    record = runtime.observe_host_output(result, "observed output", legacy_fallthrough=True)
    assert runtime.execution_observatory.recent() == (record,)
    assert record.host_result.output_nonempty is True
    assert record.trace.trace_id


def test_registry_is_bounded_and_restart_is_empty():
    registry = ExecutionRecordObservatory(max_records=2)
    runtime = CognitiveRuntime(record_traces=False, execution_observatory=registry)
    first = runtime.observe_host_output(BehaviorLoopResult(_trace("event:1")), "one", legacy_fallthrough=True)
    second = runtime.observe_host_output(BehaviorLoopResult(_trace("event:2")), "two", legacy_fallthrough=True)
    third = runtime.observe_host_output(BehaviorLoopResult(_trace("event:3")), "three", legacy_fallthrough=True)
    assert registry.recent() == (second, third)
    assert first not in registry.recent()
    assert ExecutionRecordObservatory().recent() == ()


def test_episode_query_does_not_cross_wire_and_returns_all_matching_executions():
    registry = ExecutionRecordObservatory()
    runtime = CognitiveRuntime(record_traces=False, execution_observatory=registry)
    first = runtime.observe_host_output(BehaviorLoopResult(_trace("event:e1")), "one", legacy_fallthrough=True)
    second = runtime.observe_host_output(BehaviorLoopResult(_trace("event:e2")), "two", legacy_fallthrough=True)
    ep1 = Episode("episode:e1", "scope", EpisodeState.FINALIZED, "event:e1", NOW, NOW, event_refs=(_host_ref(first),), finalized_at=NOW)
    ep2 = Episode("episode:e2", "scope", EpisodeState.FINALIZED, "event:e2", NOW, NOW, event_refs=(_host_ref(second),), finalized_at=NOW)
    assert registry.find_for_episode(ep1) == ((_host_ref(first), first),)
    assert registry.find_for_episode(ep2) == ((_host_ref(second), second),)
    assert registry.find_for_episode(ep1)[0][1] is not second


def test_one_episode_can_query_multiple_execution_records_deterministically():
    registry = ExecutionRecordObservatory()
    runtime = CognitiveRuntime(record_traces=False, execution_observatory=registry)
    first = runtime.observe_host_output(BehaviorLoopResult(_trace("event:multi-1")), "one", legacy_fallthrough=True)
    second = runtime.observe_host_output(BehaviorLoopResult(_trace("event:multi-2")), "two", legacy_fallthrough=True)
    refs = (_host_ref(first), _host_ref(second))
    episode = Episode("episode:multi", "scope", EpisodeState.FINALIZED, "event:multi-1", NOW, NOW, event_refs=refs, finalized_at=NOW)
    assert tuple(record.trace.event_id for _ref, record in registry.find_for_episode(episode)) == ("event:multi-1", "event:multi-2")
    store = InMemoryEpisodeStore(); store.create_episode(episode)
    detail = P1ObservatoryService(store, execution_observatory=registry).episode_detail(episode.episode_id)
    assert [item["status"] for item in detail["attachments"]] == ["ATTACHED", "ATTACHED"]


def test_unattached_execution_fails_snapshot_validation():
    registry = ExecutionRecordObservatory()
    runtime = CognitiveRuntime(record_traces=False, execution_observatory=registry)
    record = runtime.observe_host_output(BehaviorLoopResult(_trace("event:attached")), "one", legacy_fallthrough=True)
    episode = Episode("episode:other", "scope", EpisodeState.FINALIZED, "event:other", NOW, NOW, finalized_at=NOW)
    unattached_ref = EpisodeEventRef("HOST_OUTPUT:unrelated", EpisodeEventKind.HOST_OUTPUT, "event:other", record.trace.trace_id, f"{record.trace.trace_id}:1", NOW)
    with pytest.raises(ValueError, match="not attached"):
        ReviewInputSnapshot(episode, (), {(EvidenceSourceType.HOST_RESULT, unattached_ref.ref_id): record})


def test_attached_real_record_enables_read_only_preview_without_production_store_write():
    registry = ExecutionRecordObservatory()
    runtime = CognitiveRuntime(record_traces=False, execution_observatory=registry)
    record = runtime.observe_host_output(BehaviorLoopResult(_trace("event:preview")), "one", legacy_fallthrough=True)
    ref = _host_ref(record)
    episode = Episode("episode:preview", "scope", EpisodeState.FINALIZED, "event:preview", NOW, NOW, event_refs=(ref,), finalized_at=NOW)
    outcome = OutcomeObservation("outcome:preview", episode.episode_id, OutcomeKind.EXPLICIT_CORRECTION, NOW, source_event_id="event:reply", explicitness=OutcomeExplicitness.EXPLICIT)
    store = InMemoryEpisodeStore(); store.create_episode(episode); store.record_outcome(outcome)
    service = P1ObservatoryService(store, execution_observatory=registry)
    before = store.get_episode(episode.episode_id)
    preview = service.preview_review(episode.episode_id)
    assert preview["persisted"] is False
    assert preview["run"]["status"] == "COMPLETED"
    assert preview["evidence_count"] == 0
    assert store.get_episode(episode.episode_id) == before
