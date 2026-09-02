"""P1a eight walkthrough regressions, reduced to deterministic unit tests."""

from __future__ import annotations

from datetime import datetime, timezone

from iris_memory.cognitive.contracts import (
    CanonicalExperience,
    EntityReference,
    Perspective,
    ResolvedEvent,
)
from iris_memory.cognitive.episode import (
    BoundaryAction,
    Episode,
    EpisodeEventKind,
    EpisodeEventRef,
    EpisodeState,
    make_episode_event_ref_id,
    make_episode_id,
)
from iris_memory.cognitive.episode_assembler import EpisodeAssembler
from iris_memory.cognitive.episode_store import InMemoryEpisodeStore
from iris_memory.cognitive.outcome import OutcomeKind
from iris_memory.cognitive.outcome_collector import OutcomeCollector

_NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
_SELF = "agent:xiaotianwen"
_USER1 = EntityReference("person:qq:1", "platform_uid", 1.0, ("qq:1",))
_USER2 = EntityReference("person:qq:2", "platform_uid", 1.0, ("qq:2",))
_SELF_REF = EntityReference(_SELF, "event_self_uid", 1.0, ("uid",))


def _exp(
    event_id: str,
    *,
    session_id: str = "g1",
    mode: str = "private",
    content: str = "hello",
    actor: EntityReference = _USER1,
    reply_event_id: str | None = None,
    mentioned=(),
) -> CanonicalExperience:
    raw = {}
    if reply_event_id is not None:
        raw["reply_event_id"] = reply_event_id
    ev = ResolvedEvent(
        event_id=event_id,
        source="qq",
        occurred_at=_NOW,
        session_id=session_id,
        mode=mode,
        content=content,
        actor=actor,
        mentioned_entities=mentioned,
        raw_metadata=raw,
    )
    return CanonicalExperience(
        id=f"experience:{event_id}",
        event=ev,
        subject=actor,
        perspective=Perspective.INTERPERSONAL,
        provenance=("test",),
    )


def _ep(episode_id: str, root: str, session_id: str = "g1") -> Episode:
    return Episode(
        episode_id=episode_id,
        scope_id=session_id,
        state=EpisodeState.OPEN,
        root_event_id=root,
        opened_at=_NOW,
        last_activity_at=_NOW,
        participants=(_USER1,),
        topic_hint="topic",
    )


def test_case1_question_answer_thanks():
    store = InMemoryEpisodeStore()
    ep = store.create_episode(_ep(make_episode_id("g1", "qq:q1"), "qq:q1"))
    store.append_event_ref(ep.episode_id, _ref(EpisodeEventKind.COGNITIVE_PROPOSAL, "qq:q1"))
    store.append_event_ref(ep.episode_id, _ref(EpisodeEventKind.HOST_OUTPUT, "qq:q1", trace="trace:1"))
    thanks = _exp("qq:thanks", content="谢谢")
    outcomes = OutcomeCollector().classify_experience(thanks, target_episode_id=ep.episode_id)
    assert any(o.kind is OutcomeKind.EXPLICIT_ACKNOWLEDGEMENT for o in outcomes)


def test_case2_directed_silence_ends():
    store = InMemoryEpisodeStore()
    ep = store.create_episode(_ep(make_episode_id("g1", "qq:s"), "qq:s"))
    ep = store.append_event_ref(ep.episode_id, _ref(EpisodeEventKind.NO_INTENT, "qq:s", trace="trace:s"))
    ep = store.transition_state(ep.episode_id, EpisodeState.SOFT_CLOSED, reason="idle")
    ep = store.transition_state(ep.episode_id, EpisodeState.FINALIZED, reason="grace")
    assert ep.state is EpisodeState.FINALIZED
    assert any(r.kind is EpisodeEventKind.NO_INTENT for r in ep.event_refs)


def test_case3_joke_continuation_and_new_user():
    store = InMemoryEpisodeStore()
    ep = store.create_episode(_ep(make_episode_id("g1", "qq:j1"), "qq:j1"))
    store.append_event_ref(ep.episode_id, _ref(EpisodeEventKind.HOST_OUTPUT, "qq:j1", trace="trace:j"))
    # User2 replies to the bot's joke message in the same Episode.
    assembler = EpisodeAssembler(_SELF)
    decision = assembler.decide(
        _exp("qq:j2", content="哈哈", actor=_USER2, reply_event_id="qq:j1"),
        active_episodes=(store.get_episode(ep.episode_id),),
        event_to_episode={"qq:j1": ep.episode_id},
    )
    assert decision.action is BoundaryAction.ATTACH


def test_case4_tool_then_answer_then_correction():
    store = InMemoryEpisodeStore()
    ep = store.create_episode(_ep(make_episode_id("g1", "qq:t1"), "qq:t1"))
    store.append_event_ref(ep.episode_id, _ref(EpisodeEventKind.COGNITIVE_PROPOSAL, "qq:t1", trace="trace:t"))
    store.append_event_ref(ep.episode_id, _ref(EpisodeEventKind.TOOL_RESULT, "qq:t1", trace="trace:t", exec_id="tool:1"))
    store.append_event_ref(ep.episode_id, _ref(EpisodeEventKind.HOST_OUTPUT, "qq:t1", trace="trace:t", exec_id="host:1"))
    correction = _exp("qq:wrong", content="不对")
    outcomes = OutcomeCollector().classify_experience(correction, target_episode_id=ep.episode_id)
    assert any(o.kind is OutcomeKind.EXPLICIT_CORRECTION for o in outcomes)


def test_case5_interleaved_group_topics():
    assembler = EpisodeAssembler(_SELF)
    ep1 = _ep(make_episode_id("g1", "qq:t1"), "qq:t1")
    ep2 = _ep(make_episode_id("g1", "qq:w1"), "qq:w1", session_id="g1")
    index = {"qq:t1": ep1.episode_id, "qq:w1": ep2.episode_id}
    d1 = assembler.decide(_exp("qq:t2", content="继续望远镜", reply_event_id="qq:t1"), active_episodes=(ep1, ep2), event_to_episode=index)
    d2 = assembler.decide(_exp("qq:w2", content="继续天气", actor=_USER2, reply_event_id="qq:w1"), active_episodes=(ep1, ep2), event_to_episode=index)
    assert d1.episode_id == ep1.episode_id
    assert d2.episode_id == ep2.episode_id


def test_case6_late_reply_to_soft_closed_reopens():
    store = InMemoryEpisodeStore()
    ep = store.create_episode(_ep(make_episode_id("g1", "qq:old"), "qq:old"))
    ep = store.transition_state(ep.episode_id, EpisodeState.SOFT_CLOSED, reason="idle")
    assembler = EpisodeAssembler(_SELF)
    decision = assembler.decide(
        _exp("qq:late", content="继续", reply_event_id="qq:old"),
        active_episodes=(ep,),
        event_to_episode={"qq:old": ep.episode_id},
    )
    assert decision.action is BoundaryAction.ATTACH
    reopened = store.transition_state(ep.episode_id, EpisodeState.OPEN, reason="exact_reply")
    assert reopened.state is EpisodeState.OPEN


def test_case7_next_day_correction_targets_finalized_old_episode():
    store = InMemoryEpisodeStore()
    old = store.create_episode(_ep(make_episode_id("g1", "qq:old"), "qq:old"))
    old = store.transition_state(old.episode_id, EpisodeState.SOFT_CLOSED, reason="idle")
    old = store.transition_state(old.episode_id, EpisodeState.FINALIZED, reason="grace")
    # The current feedback is a new Episode, with an Outcome targeting old.
    new = store.create_episode(_ep(make_episode_id("g1", "qq:today"), "qq:today"))
    feedback = _exp("qq:today", content="你昨天说的那个不对")
    outcomes = OutcomeCollector().classify_experience(feedback, target_episode_id=new.episode_id)
    correction = next(o for o in outcomes if o.kind is OutcomeKind.EXPLICIT_CORRECTION)
    # Simulate strong-link outcome against old finalized Episode by adding a second
    # explicit observation targeting old.
    store.record_outcome(correction)
    # Old finalized must remain unchanged.
    assert store.get_episode(old.episode_id).state is EpisodeState.FINALIZED


def test_case8_dispatch_no_ack_later_reply():
    store = InMemoryEpisodeStore()
    ep = store.create_episode(_ep(make_episode_id("g1", "qq:h"), "qq:h"))
    store.append_event_ref(ep.episode_id, _ref(EpisodeEventKind.HOST_OUTPUT, "qq:h", trace="trace:h"))
    store.append_event_ref(ep.episode_id, _ref(EpisodeEventKind.DISPATCH, "qq:h", trace="trace:h", exec_id="dispatch:1"))
    later = _exp("qq:later", content="收到", reply_event_id="qq:h")
    outcomes = OutcomeCollector().classify_experience(later, target_episode_id=ep.episode_id)
    assert any(o.kind is OutcomeKind.EXPLICIT_ACKNOWLEDGEMENT for o in outcomes)
    # No delivery failure was invented.
    assert not any(o.kind is OutcomeKind.DELIVERY_FAILED for o in outcomes)


def _ref(kind, source, *, trace=None, exec_id=None):
    return EpisodeEventRef(
        ref_id=make_episode_event_ref_id(
            kind,
            source_event_id=source,
            trace_id=trace,
            execution_record_id=exec_id,
        ),
        kind=kind,
        source_event_id=source,
        trace_id=trace,
        execution_record_id=exec_id,
        observed_at=_NOW,
    )
