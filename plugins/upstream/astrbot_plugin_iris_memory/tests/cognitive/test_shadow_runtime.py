"""P0.5 Shadow/Guard semantics, host observation, replay and isolation tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from iris_memory.cognitive.contracts import (
    CanonicalExperience,
    DeliveryStatus,
    DivergenceType,
    EntityReference,
    ExitReason,
    GroundingEnforcement,
    OutputProducer,
    OutputState,
    Perspective,
    ResolvedEvent,
    RuntimeMode,
)
from iris_memory.cognitive.iris_adapter import CognitiveRuntime
from iris_memory.cognitive.replay import LocalHistoricalReplayRunner


_NOW = datetime(2026, 9, 2, 12, tzinfo=timezone.utc)
_FIXTURE = Path(__file__).parent / "fixtures" / "p05_historical_shadow_replay.json"


def _experience(
    content: str,
    *,
    event_id: str,
    session_id: str,
    actor_id: str = "person:qq:1",
    mode: str = "private",
    at: datetime = _NOW,
) -> CanonicalExperience:
    actor = EntityReference(actor_id, "test_uid", 1.0, (actor_id,))
    event = ResolvedEvent(
        event_id=event_id,
        source="qq",
        occurred_at=at,
        session_id=session_id,
        mode=mode,
        content=content,
        actor=actor,
    )
    return CanonicalExperience(
        id=f"experience:{event_id}",
        event=event,
        subject=actor,
        perspective=Perspective.INTERPERSONAL,
        provenance=("P0.5 test",),
    )


def test_shadow_is_default_and_runtime_end_does_not_claim_host_silence():
    runtime = CognitiveRuntime()
    result = runtime.run_behavior(
        _experience("小黄鱼馄饨", event_id="shadow:no-intent", session_id="group:a", mode="casual_group_chat")
    )

    assert runtime.runtime_mode is RuntimeMode.SHADOW
    assert result.trace.runtime_mode is RuntimeMode.SHADOW
    assert result.trace.exit_reason is ExitReason.TRIGGER_NO
    assert not runtime.should_guard_block(result)

    record = runtime.observe_host_output(result, "Legacy 仍然回复了", legacy_fallthrough=True)
    assert record.host_result.output_nonempty is True
    assert record.comparison.divergence is DivergenceType.LEGACY_REPLY_COGNITIVE_SILENCE
    next_event = runtime.run_behavior(
        _experience("后续", event_id="shadow:after-host", session_id="group:a", mode="casual_group_chat", at=_NOW + timedelta(seconds=1))
    )
    assert next_event.trace.situation_lite.self_recently_spoke is None
    assert next_event.trace.situation_lite.recent_self_action == "HOST_OUTPUT"
    assert next_event.trace.situation_lite.last_self_action_at == record.updated_at


def test_guard_has_only_the_frozen_blocking_exit_set():
    guarded = CognitiveRuntime(runtime_mode=RuntimeMode.GUARD)
    no_intent = guarded.run_behavior(
        _experience("小黄鱼馄饨", event_id="guard:no-intent", session_id="private:1")
    )
    assert no_intent.trace.exit_reason is ExitReason.NO_INTENT
    assert guarded.should_guard_block(no_intent)
    block = guarded.record_guard_block(no_intent)
    assert block.host_result.legacy_fallthrough is False
    assert block.host_result.output_generated is False

    trigger_no = guarded.run_behavior(
        _experience("普通群聊", event_id="guard:trigger-no", session_id="group:a", mode="casual_group_chat")
    )
    assert trigger_no.trace.exit_reason is ExitReason.TRIGGER_NO
    assert not guarded.should_guard_block(trigger_no)

    authoritative = CognitiveRuntime(runtime_mode=RuntimeMode.AUTHORITATIVE)
    assert authoritative.runtime_mode is RuntimeMode.AUTHORITATIVE
    authoritative_result = authoritative.run_behavior(
        _experience("小黄鱼馄饨", event_id="authoritative:no-intent", session_id="private:2")
    )
    # A result keeps its event-scoped mode snapshot even when inspected elsewhere.
    assert not authoritative.should_guard_block(authoritative_result)


def test_realizer_output_ready_is_not_platform_delivery_and_empty_is_failure():
    runtime = CognitiveRuntime()
    proposed = runtime.run_behavior(
        _experience("今晚几点观测？", event_id="realize:1", session_id="private:1")
    )
    assert proposed.trace.proposed_output_state is OutputState.OUTPUT_PROPOSED
    assert proposed.trace.grounding.enforcement is GroundingEnforcement.PROMPT_CONSTRAINED

    ready = runtime.observe_host_output(proposed, "目前未验证，建议查看官方公告。", legacy_fallthrough=True)
    assert ready.trace.exit_reason is None
    assert ready.trace.proposed_output_state is OutputState.OUTPUT_PROPOSED
    assert ready.host_result.output_nonempty
    assert ready.host_result.output_state is OutputState.OUTPUT_READY
    assert ready.host_result.producer is OutputProducer.LEGACY_HOST
    assert ready.host_result.applied_enforcement is GroundingEnforcement.NOT_APPLIED
    assert ready.host_result.delivery_status is DeliveryStatus.UNOBSERVED
    assert not ready.host_result.dispatch_observed

    dispatched = runtime.observe_dispatch(ready)
    assert dispatched.host_result.dispatch_observed
    assert dispatched.host_result.delivery_status is DeliveryStatus.UNOBSERVED
    assert dispatched.host_result.delivery_status is not DeliveryStatus.DELIVERED

    failed = runtime.observe_host_output(proposed, "", legacy_fallthrough=True)
    assert failed.trace.exit_reason is None
    assert failed.host_result.output_state is OutputState.NO_OUTPUT
    assert failed.host_result.delivery_status is DeliveryStatus.UNOBSERVED


def test_shadow_proposal_cannot_fake_self_recently_spoke_before_host_output():
    runtime = CognitiveRuntime()
    proposal = runtime.run_behavior(
        _experience("今晚几点观测？", event_id="self:proposal", session_id="private:1")
    )
    assert proposal.trace.situation_lite.self_recently_spoke is False

    still_shadow = runtime.run_behavior(
        _experience("下一条", event_id="self:before-host", session_id="private:1", at=_NOW + timedelta(seconds=1))
    )
    assert still_shadow.trace.situation_lite.self_recently_spoke is False

    host_record = runtime.observe_host_output(proposal, "我先查官方公告。", legacy_fallthrough=True)
    after_host = runtime.run_behavior(
        _experience("再问", event_id="self:after-host", session_id="private:1", at=_NOW + timedelta(seconds=2))
    )
    assert after_host.trace.situation_lite.self_recently_spoke is None
    assert after_host.trace.situation_lite.last_self_action_at == host_record.updated_at


def test_interleaved_conversations_remain_isolated():
    runtime = CognitiveRuntime()
    a1 = runtime.run_behavior(_experience("A1", event_id="a1", session_id="group:A", actor_id="person:qq:a", mode="casual_group_chat"))
    b1 = runtime.run_behavior(_experience("B1", event_id="b1", session_id="group:B", actor_id="person:qq:b", mode="casual_group_chat"))
    a2 = runtime.run_behavior(_experience("A2", event_id="a2", session_id="group:A", actor_id="person:qq:a", mode="casual_group_chat", at=_NOW + timedelta(seconds=1)))
    b2 = runtime.run_behavior(_experience("B2", event_id="b2", session_id="group:B", actor_id="person:qq:b", mode="casual_group_chat", at=_NOW + timedelta(seconds=1)))
    private = runtime.run_behavior(_experience("C1", event_id="c1", session_id="private:C", actor_id="person:qq:c"))

    assert a1.trace.situation_lite.message_velocity == 1
    assert b1.trace.situation_lite.message_velocity == 1
    assert a2.trace.situation_lite.message_velocity == 2
    assert b2.trace.situation_lite.message_velocity == 2
    assert a2.trace.situation_lite.active_entities == ("person:qq:a",)
    assert b2.trace.situation_lite.active_entities == ("person:qq:b",)
    assert private.trace.situation_lite.message_velocity == 1
    assert private.trace.situation_lite.scope_id == "private:C"


def test_local_historical_replay_is_deterministic_and_compares_legacy_output():
    first = LocalHistoricalReplayRunner().run_file(_FIXTURE)
    second = LocalHistoricalReplayRunner().run_file(_FIXTURE)

    assert first == second
    assert first[0]["cognitive"]["terminal_result"] == ExitReason.TRIGGER_NO.value
    assert first[0]["comparison"]["divergence"] == DivergenceType.LEGACY_REPLY_COGNITIVE_SILENCE.value
    assert first[1]["comparison"]["divergence"] == DivergenceType.MATCH_SILENCE.value
    assert first[2]["cognitive"]["grounding"]["status"] == "DEGRADED"
    assert first[2]["cognitive"]["grounding"]["enforcement"] == "PROMPT_CONSTRAINED"
    assert first[2]["comparison"]["divergence"] == DivergenceType.MATCH_REPLY.value
    assert first[3]["comparison"]["divergence"] == DivergenceType.LEGACY_SILENCE_COGNITIVE_REPLY.value


def test_shadow_comparison_keeps_immutable_proposal_for_all_reply_combinations():
    runtime = CognitiveRuntime()
    proposal = runtime.run_behavior(
        _experience("今晚几点观测？", event_id="matrix:proposal", session_id="private:matrix")
    )
    replied = runtime.observe_host_output(proposal, "Legacy reply", legacy_fallthrough=True)
    silent = runtime.observe_host_silence(proposal, legacy_fallthrough=False)

    assert proposal.trace.proposed_output_state is OutputState.OUTPUT_PROPOSED
    assert replied.trace is proposal.trace
    assert replied.comparison.divergence is DivergenceType.MATCH_REPLY
    assert silent.comparison.divergence is DivergenceType.LEGACY_SILENCE_COGNITIVE_REPLY

    no_proposal = runtime.run_behavior(
        _experience("普通群聊", event_id="matrix:silence", session_id="group:matrix", mode="casual_group_chat")
    )
    legacy_reply = runtime.observe_host_output(no_proposal, "Legacy reply", legacy_fallthrough=True)
    legacy_silence = runtime.observe_host_silence(no_proposal, legacy_fallthrough=False)
    assert legacy_reply.comparison.divergence is DivergenceType.LEGACY_REPLY_COGNITIVE_SILENCE
    assert legacy_silence.comparison.divergence is DivergenceType.MATCH_SILENCE


def test_shadow_host_output_is_not_cognitive_realization_and_mode_snapshot_is_stable():
    runtime = CognitiveRuntime()
    proposal = runtime.run_behavior(
        _experience("今晚几点观测？", event_id="shadow:producer", session_id="private:producer")
    )
    runtime.runtime_mode = RuntimeMode.GUARD
    assert proposal.trace.runtime_mode is RuntimeMode.SHADOW
    assert not runtime.should_guard_block(proposal)

    record = runtime.observe_host_output(proposal, "Legacy reply", legacy_fallthrough=True)
    assert record.host_result.producer is OutputProducer.LEGACY_HOST
    assert record.host_result.applied_enforcement is GroundingEnforcement.NOT_APPLIED
    next_event = runtime.run_behavior(
        _experience("下一条", event_id="shadow:producer-next", session_id="private:producer", at=_NOW + timedelta(seconds=1))
    )
    assert next_event.trace.situation_lite.recent_self_action == "HOST_OUTPUT"


def test_trace_lifecycle_has_stable_identity_stage_and_revision():
    from iris_memory.core.run_log import get_run_log_manager, reset_run_log_manager

    reset_run_log_manager()
    runtime = CognitiveRuntime()
    proposal = runtime.run_behavior(
        _experience("今晚几点观测？", event_id="trace:lifecycle", session_id="private:trace")
    )
    host = runtime.observe_host_output(proposal, "Legacy reply", legacy_fallthrough=True)
    runtime.observe_dispatch(host)
    entries = [entry for entry in get_run_log_manager().get_entries("proactive") if entry["detail"].get("event_id") == "trace:lifecycle"]
    stages = {(entry["detail"]["stage"], entry["detail"]["revision"]) for entry in entries}

    assert {entry["detail"]["trace_id"] for entry in entries} == {proposal.trace.trace_id}
    assert stages == {("PROPOSAL", 0), ("HOST_OUTPUT", 1), ("DISPATCH", 2)}


def test_trace_sink_failure_is_observable_not_silently_swallowed(monkeypatch, caplog):
    import iris_memory.core

    def broken_sink():
        raise RuntimeError("diagnostic sink unavailable")

    monkeypatch.setattr(iris_memory.core, "get_run_log_manager", broken_sink)
    with caplog.at_level("WARNING"):
        CognitiveRuntime().run_behavior(
            _experience("今晚几点观测？", event_id="trace:sink-error", session_id="private:trace-error")
        )
    assert "trace diagnostic write failed" in caplog.text
