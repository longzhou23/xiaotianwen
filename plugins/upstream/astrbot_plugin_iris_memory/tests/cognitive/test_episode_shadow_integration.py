"""Shadow Episode integration tests through CognitiveRuntime."""

from __future__ import annotations

from datetime import datetime, timezone

from iris_memory.cognitive.contracts import (
    CanonicalExperience,
    EntityReference,
    Perspective,
    ResolvedEvent,
)
from iris_memory.cognitive.episode import EpisodeEventKind
from iris_memory.cognitive.episode_shadow import EpisodeShadowObserver
from iris_memory.cognitive.episode_store import InMemoryEpisodeStore
from iris_memory.cognitive.iris_adapter import CognitiveRuntime


_NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
_USER = EntityReference("person:qq:1", "platform_uid", 1.0, ("qq:1",))


def _exp(event_id: str, content: str, mode: str = "private", session_id: str = "g1") -> CanonicalExperience:
    event = ResolvedEvent(
        event_id=event_id,
        source="qq",
        occurred_at=_NOW,
        session_id=session_id,
        mode=mode,
        content=content,
        actor=_USER,
    )
    return CanonicalExperience(
        id=f"experience:{event_id}",
        event=event,
        subject=_USER,
        perspective=Perspective.INTERPERSONAL,
        provenance=("test",),
    )


def test_shadow_observer_records_proposal_host_dispatch_refs():
    store = InMemoryEpisodeStore()
    observer = EpisodeShadowObserver(store)
    runtime = CognitiveRuntime(episode_observer=observer)
    proposal = runtime.run_behavior(_exp("qq:1", "今晚几点观测？"))

    episodes = store.all_episodes()
    assert len(episodes) == 1
    ep = episodes[0]
    kinds = {r.kind for r in ep.event_refs}
    assert EpisodeEventKind.EXPERIENCE in kinds
    assert EpisodeEventKind.COGNITIVE_PROPOSAL in kinds

    host = runtime.observe_host_output(proposal, "我看看官方公告。", legacy_fallthrough=True)
    after_host = store.get_episode(ep.episode_id)
    assert after_host is not None
    assert EpisodeEventKind.HOST_OUTPUT in {r.kind for r in after_host.event_refs}

    runtime.observe_dispatch(host)
    after_dispatch = store.get_episode(ep.episode_id)
    assert after_dispatch is not None
    assert EpisodeEventKind.DISPATCH in {r.kind for r in after_dispatch.event_refs}


def test_ambient_group_message_does_not_create_episode():
    store = InMemoryEpisodeStore()
    observer = EpisodeShadowObserver(store)
    runtime = CognitiveRuntime(episode_observer=observer)
    runtime.run_behavior(_exp("qq:ambient", "普通群聊", mode="casual_group_chat", session_id="g1"))
    assert store.all_episodes() == ()
