"""P0 back-half contract, replay and fail-closed behavior tests."""

from __future__ import annotations

import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from iris_memory.cognitive.behavior import CognitiveBehaviorRuntime, GroundingGuard, ParticipationController
from iris_memory.cognitive.contracts import (
    CanonicalExperience,
    EntityReference,
    ExitReason,
    GroundingEnforcement,
    GroundingStatus,
    Intent,
    LegacyProactiveSignals,
    OutputProducer,
    OutputState,
    ParticipationDecision,
    SocialAction,
    IntentDomain,
)
from iris_memory.cognitive.legacy_proactive import LegacyIrisProactiveSignalAdapter
from iris_memory.cognitive.situation import SituationBuilder
from iris_memory.cognitive.iris_adapter import CognitiveRuntime


_NOW = datetime(2026, 9, 2, 12, tzinfo=timezone.utc)
_ACTOR = EntityReference("person:qq:10001", "platform_uid", 1.0, ("qq:10001",))
_SELF = EntityReference("agent:xiaotianwen", "self_binding", 1.0, ("configured SELF",))
_REPLAY = Path(__file__).parent / "fixtures" / "p0_behavior_replay.json"


def _experience(
    content: str,
    *,
    mode: str = "private",
    event_id: str = "qq:behavior-1",
    mentioned=(),
    reply_to=None,
    at: datetime = _NOW,
) -> CanonicalExperience:
    from iris_memory.cognitive.contracts import Perspective, ResolvedEvent

    event = ResolvedEvent(
        event_id=event_id,
        source="qq",
        occurred_at=at,
        session_id="group:behavior" if mode != "private" else "private:10001",
        mode=mode,
        content=content,
        actor=_ACTOR,
        mentioned_entities=mentioned,
        reply_to=reply_to,
    )
    return CanonicalExperience(
        id=f"experience:{event_id}",
        event=event,
        subject=_ACTOR,
        perspective=Perspective.INTERPERSONAL,
        provenance=("synthetic replay",),
    )


def test_situation_lite_updates_for_every_event_and_full_is_explicit_read_only():
    builder = SituationBuilder()
    first = builder.observe(_experience("第一条", event_id="qq:lite-1", mode="casual_group_chat"))
    second = builder.observe(
        _experience(
            "第二条",
            event_id="qq:lite-2",
            mode="casual_group_chat",
            reply_to=_SELF,
            at=_NOW + timedelta(seconds=10),
        )
    )

    assert first.message_velocity == 1
    assert second.message_velocity == 2
    assert second.reply_chain == ("agent:xiaotianwen",)
    assert second.active_entities == ("person:qq:10001", "agent:xiaotianwen")
    full = builder.build_full(_experience("第二条", event_id="qq:lite-2", mode="casual_group_chat"), second)
    assert full.runtime_memory_view == ()
    assert dict(full.committed_affect) == {}
    with pytest.raises(TypeError):
        full.committed_relationship["invented"] = True


def test_situation_same_scope_concurrent_updates_are_atomic_and_recency_is_not_permanent():
    builder = SituationBuilder()

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(builder.observe, _experience(f"并发{i}", event_id=f"qq:concurrent-{i}", at=_NOW))
            for i in range(16)
        ]
        for future in futures:
            future.result()

    final = builder.observe(_experience("收尾", event_id="qq:concurrent-final", at=_NOW))
    assert final.message_velocity == 17
    assert final.self_recently_spoke is False
    builder.record_self_action(_experience("动作", event_id="qq:action", at=_NOW), "HOST_OUTPUT", occurred_at=_NOW)
    after_action = builder.observe(_experience("后续", event_id="qq:after-action", at=_NOW))
    assert after_action.last_self_action_at == _NOW
    assert after_action.self_recently_spoke is None


def test_legacy_adapter_is_read_only_and_maps_each_frozen_signal():
    data = SimpleNamespace(
        state=SimpleNamespace(value="cooldown"),
        willingness="high",
        msg_count=7,
        backoff_level=2,
        consecutive_replies=3,
    )
    state = SimpleNamespace(_groups={"g1": data})

    class Event:
        def get_group_id(self):
            return "g1"

        def get_extra(self, key):
            return {
                "iris_decision": {
                    "motive": "follow_up",
                    "skip_signal": True,
                    "drifted": True,
                    "post_evaluation_signal": True,
                }
            }.get(key)

    signals = LegacyIrisProactiveSignalAdapter(state).read(Event())

    assert signals.activation_signal == "follow_up"
    assert signals.willingness == "high"
    assert dict(signals.threshold) == {"message_count": 7, "backoff_level": 2}
    assert signals.cooldown and signals.skip_signal and signals.topic_drift_signal
    assert signals.post_evaluation_signal and signals.consecutive_reply_penalty == 3
    assert state._groups["g1"] is data


def test_legacy_adapter_uses_owner_lock_for_consistent_async_snapshot():
    data = SimpleNamespace(
        state=SimpleNamespace(value="cooldown"), willingness="high", msg_count=7,
        backoff_level=2, consecutive_replies=3,
    )

    class Lock:
        entered = False

        async def __aenter__(self):
            self.entered = True

        async def __aexit__(self, *_args):
            return False

    lock = Lock()
    state = SimpleNamespace(_groups={"g1": data}, get_lock=lambda _group: lock)

    class Event:
        def get_group_id(self):
            return "g1"

        def get_extra(self, _key):
            return None

    signals = asyncio.run(LegacyIrisProactiveSignalAdapter(state).read_consistent(Event()))
    assert lock.entered
    assert signals.cooldown and signals.consecutive_reply_penalty == 3


def test_legacy_threshold_only_modifies_trigger_score_not_explicit_self_activation():
    runtime = CognitiveBehaviorRuntime(self_entity="agent:xiaotianwen")
    experience = _experience("请回应", mode="casual_group_chat", mentioned=(_SELF,))
    lite = runtime.observe(experience)
    from iris_memory.cognitive.contracts import TriggerSnapshot

    decision = runtime.trigger.evaluate_snapshot(
        TriggerSnapshot({}, experience, lite, LegacyProactiveSignals(
            willingness="high", threshold={"backoff_level": 2}
        ))
    )
    assert decision.should_start_loop
    assert decision.score == 1
    assert "threshold backoff modifier=2" in decision.reason


@pytest.mark.parametrize(
    ("signals", "decision", "exit_reason"),
    [
        (LegacyProactiveSignals(), ParticipationDecision.PARTICIPATE, None),
        (LegacyProactiveSignals(cooldown=True), ParticipationDecision.WAIT, ExitReason.WAIT_SELECTED),
        (LegacyProactiveSignals(skip_signal=True), ParticipationDecision.SILENCE, ExitReason.SILENCE_SELECTED),
        (LegacyProactiveSignals(consecutive_reply_penalty=3), ParticipationDecision.SILENCE, ExitReason.NO_PARTICIPATION),
    ],
)
def test_participation_has_only_explicit_fail_closed_outcomes(signals, decision, exit_reason):
    runtime = CognitiveBehaviorRuntime(self_entity="agent:xiaotianwen")
    experience = _experience("今晚几点观测？")
    lite = runtime.observe(experience)
    from iris_memory.cognitive.contracts import TriggerSnapshot

    result = ParticipationController().decide(
        snapshot=TriggerSnapshot({}, experience, lite, signals)
    )
    assert result.decision is decision
    assert result.exit_reason is exit_reason


@pytest.mark.parametrize("content", ["小黄鱼馄饨", "聪明脾气又好", "你心里有别人了😭"])
def test_replay_non_questions_never_invent_individual_or_relationship_facts(content):
    runtime = CognitiveBehaviorRuntime(self_entity="agent:xiaotianwen")
    result = runtime.run(_experience(content))

    assert result.trace.intent is not None
    assert result.trace.intent.action is None
    assert result.trace.exit_reason is ExitReason.NO_INTENT
    assert result.realizer_request is None


def test_question_keeps_information_intent_when_grounding_is_degraded():
    runtime = CognitiveBehaviorRuntime(self_entity="agent:xiaotianwen")
    result = runtime.run(_experience("今晚几点观测？"))

    assert result.trace.intent is not None
    assert result.trace.intent.action is SocialAction.INFORM
    assert result.trace.grounding is not None
    assert result.trace.grounding.status is GroundingStatus.DEGRADED
    assert "unverified relationship claim" in result.trace.grounding.blocked_claims
    # Proposal is not a send.  Host lifecycle observes output separately.
    assert result.trace.exit_reason is None
    assert result.realizer_request is not None
    assert "observation_schedule" == result.trace.grounding.required_tool


def test_grounding_has_success_and_insufficient_paths_without_persona_evidence():
    guard = GroundingGuard()
    acknowledge = Intent(
        SocialAction.ACKNOWLEDGE, _ACTOR, "event-local acknowledgement", ("event",), 1.0
    )
    unsupported = Intent(SocialAction.SHARE, _ACTOR, "needs external fact", ("event",), 0.5)

    assert guard.assess(acknowledge).status is GroundingStatus.SUFFICIENT
    failed = guard.assess(unsupported)
    assert failed.status is GroundingStatus.INSUFFICIENT
    assert failed.required_tool == "verified_source"
    assert "persona style as factual evidence" in failed.blocked_claims


@pytest.mark.parametrize(
    ("content", "domain", "required_tool"),
    [
        ("今晚几点观测？", IntentDomain.OBSERVATION_SCHEDULE, "observation_schedule"),
        ("这个怎么安装？", IntentDomain.INSTALLATION_PROCEDURE, None),
        ("你是谁？", IntentDomain.SELF_IDENTITY, None),
        ("哪种望远镜适合？", IntentDomain.RECOMMENDATION, None),
    ],
)
def test_question_domains_do_not_all_collapse_into_observation_schedule(content, domain, required_tool):
    result = CognitiveBehaviorRuntime(self_entity="agent:xiaotianwen").run(_experience(content))
    assert result.trace.intent.action is SocialAction.INFORM
    assert result.trace.intent.domain is domain
    assert result.trace.grounding.required_tool == required_tool


def test_exit_trace_handles_trigger_no_and_host_facts_do_not_rewrite_proposal():
    runtime = CognitiveRuntime()
    no_trigger = runtime.run_behavior(
        _experience("普通群聊", mode="casual_group_chat"),
        runtime_mode=runtime.runtime_mode,
    )
    assert no_trigger.trace.exit_reason is ExitReason.TRIGGER_NO

    insufficient = runtime.run_behavior(
        _experience("今晚几点观测？"),
        runtime_mode=runtime.runtime_mode,
    )
    failed_host = runtime.observe_host_output(insufficient, "", legacy_fallthrough=True)
    assert failed_host.host_result.output_state is OutputState.NO_OUTPUT
    assert failed_host.trace is insufficient.trace
    assert failed_host.trace.proposed_output_state is OutputState.OUTPUT_PROPOSED
    assert failed_host.trace.exit_reason is None

    successful = runtime.observe_host_output(
        insufficient, "暂时无法确认，建议查看官方观测公告。", legacy_fallthrough=True
    )
    assert successful.host_result.output_state is OutputState.OUTPUT_READY
    assert successful.host_result.producer is OutputProducer.LEGACY_HOST
    assert successful.host_result.applied_enforcement is GroundingEnforcement.NOT_APPLIED
    assert successful.trace is insufficient.trace
    assert successful.trace.proposed_output_state is OutputState.OUTPUT_PROPOSED

    next_result = runtime.run_behavior(
        _experience("后续", event_id="qq:behavior-2", at=_NOW + timedelta(seconds=5)),
        runtime_mode=runtime.runtime_mode,
    )
    assert next_result.trace.situation_lite.self_recently_spoke is None
    assert next_result.trace.situation_lite.recent_self_action == "HOST_OUTPUT"


def test_synthetic_p0_replay_fixture_covers_terminal_behavior_and_legacy_self_boundary():
    cases = json.loads(_REPLAY.read_text(encoding="utf-8"))["cases"]
    behavior = CognitiveBehaviorRuntime(self_entity="agent:xiaotianwen")
    for index, case in enumerate(cases[:4]):
        result = behavior.run(_experience(case["content"], event_id=f"qq:replay-{index}"))
        actual_exit = result.trace.exit_reason.value if result.trace.exit_reason else "ACTION_PROPOSED"
        assert actual_exit == case["expected_exit"]
        if "expected_action" in case:
            assert result.trace.intent.action.value == case["expected_action"]
            assert result.trace.grounding.status.value == case["expected_grounding"]

    adapter = CognitiveRuntime().post_adapter
    for index, case in enumerate(cases[4:]):
        metadata = (
            {"cognitive_runtime": {"subject_entity": "agent:xiaotianwen"}}
            if case["metadata_confirmed_self"]
            else {}
        )
        view = adapter.project_memory(
            memory_id=f"legacy-self:{index}", content=case["content"], metadata=metadata
        )
        assert view.perspective.value == case["expected_perspective"]


def test_unpunctuated_question_marker_is_conservative_information_intent():
    result = CognitiveBehaviorRuntime(self_entity="agent:xiaotianwen").run(
        _experience("何时有流星雨")
    )
    assert result.trace.intent is not None
    assert result.trace.intent.action is SocialAction.INFORM
    assert result.trace.intent.domain is IntentDomain.GENERAL_INFORMATION
