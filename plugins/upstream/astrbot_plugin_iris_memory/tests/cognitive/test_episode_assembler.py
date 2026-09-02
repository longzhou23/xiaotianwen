"""EpisodeAssembler deterministic routing tests."""

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
    EpisodeState,
    make_episode_id,
)
from iris_memory.cognitive.episode_assembler import EpisodeAssembler


_NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
_SELF = "agent:xiaotianwen"
_USER1 = EntityReference("person:qq:1", "platform_uid", 1.0, ("qq:1",))
_USER2 = EntityReference("person:qq:2", "platform_uid", 1.0, ("qq:2",))
_SELF_REF = EntityReference(_SELF, "event_self_uid", 1.0, ("uid",))


def _experience(
    event_id: str,
    *,
    session_id: str = "g1",
    mode: str = "casual_group_chat",
    content: str = "hello",
    actor: EntityReference | None = _USER1,
    mentioned=(),
    reply_to=None,
    reply_event_id: str | None = None,
) -> CanonicalExperience:
    raw_metadata = {}
    if reply_event_id is not None:
        raw_metadata["reply_event_id"] = reply_event_id
    event = ResolvedEvent(
        event_id=event_id,
        source="qq",
        occurred_at=_NOW,
        session_id=session_id,
        mode=mode,
        content=content,
        actor=actor,
        mentioned_entities=mentioned,
        reply_to=reply_to,
        raw_metadata=raw_metadata,
    )
    return CanonicalExperience(
        id=f"experience:{event_id}",
        event=event,
        subject=actor,
        perspective=Perspective.INTERPERSONAL,
        provenance=("test",),
    )


def _episode(episode_id: str, scope: str = "g1", topic_hint: str | None = None, actor=_USER1) -> Episode:
    return Episode(
        episode_id=episode_id,
        scope_id=scope,
        state=EpisodeState.OPEN,
        root_event_id="qq:root",
        opened_at=_NOW,
        last_activity_at=_NOW,
        participants=(actor,),
        topic_hint=topic_hint,
    )


def test_ambient_group_message_is_no_episode():
    assembler = EpisodeAssembler(_SELF)
    decision = assembler.decide(_experience("qq:ambient"))
    assert decision.action is BoundaryAction.NO_EPISODE


def test_direct_self_question_is_new():
    assembler = EpisodeAssembler(_SELF)
    decision = assembler.decide(
        _experience(
            "qq:private",
            mode="private",
            content="今晚几点观测？",
        )
    )
    assert decision.action is BoundaryAction.NEW


def test_reply_to_member_event_attaches_exact_episode():
    assembler = EpisodeAssembler(_SELF)
    ep = _episode(make_episode_id("g1", "qq:1"), topic_hint="望远镜")
    decision = assembler.decide(
        _experience(
            "qq:2",
            reply_event_id="qq:1",
            content="继续",
        ),
        active_episodes=(ep,),
        event_to_episode={"qq:1": ep.episode_id},
    )
    assert decision.action is BoundaryAction.ATTACH
    assert decision.episode_id == ep.episode_id


def test_self_mention_without_continuity_prefers_new_over_old_attach():
    assembler = EpisodeAssembler(_SELF)
    old = _episode(make_episode_id("g1", "qq:old"), topic_hint="旧话题")
    decision = assembler.decide(
        _experience(
            "qq:new",
            mentioned=(_SELF_REF,),
            content="小天文，现在有个新问题",
        ),
        active_episodes=(old,),
        event_to_episode={},
    )
    assert decision.action is BoundaryAction.NEW


def test_two_interleaved_reply_chains_attach_to_own_episode():
    assembler = EpisodeAssembler(_SELF)
    ep1 = _episode(make_episode_id("g1", "qq:t1"), topic_hint="望远镜")
    ep2 = _episode(make_episode_id("g1", "qq:t2"), topic_hint="天气")
    index = {"qq:t1": ep1.episode_id, "qq:t2": ep2.episode_id}
    d1 = assembler.decide(
        _experience("qq:a2", reply_event_id="qq:t1", content="继续望远镜"),
        active_episodes=(ep1, ep2),
        event_to_episode=index,
    )
    d2 = assembler.decide(
        _experience("qq:b2", reply_event_id="qq:t2", content="继续天气"),
        active_episodes=(ep1, ep2),
        event_to_episode=index,
    )
    assert d1.action is BoundaryAction.ATTACH and d1.episode_id == ep1.episode_id
    assert d2.action is BoundaryAction.ATTACH and d2.episode_id == ep2.episode_id


def test_ambiguous_topic_match_does_not_false_attach():
    assembler = EpisodeAssembler(_SELF)
    ep = _episode(make_episode_id("g1", "qq:root"), topic_hint="望远镜")
    # no exact reply, no direct SELF, but same actor and weak topic overlap
    decision = assembler.decide(
        _experience("qq:ambig", content="望远镜还有别的推荐吗"),
        active_episodes=(ep,),
    )
    # Conservative: this is not an exact structural link, so NO_EPISODE is safe.
    assert decision.action in (BoundaryAction.NO_EPISODE, BoundaryAction.NEW)
