"""P1b.1 runtime integration repair tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from iris_memory.cognitive.contracts import (
    CanonicalExperience,
    EntityReference,
    Perspective,
    ResolvedEvent,
)
from iris_memory.cognitive.episode import EpisodeState
from iris_memory.cognitive.episode_shadow import EpisodeShadowObserver
from iris_memory.cognitive.episode_store import (
    AppendOnlyEpisodeStore,
    EpisodeLogCorruptionError,
    InMemoryEpisodeStore,
)
from iris_memory.cognitive.iris_adapter import CognitiveRuntime
from iris_memory.cognitive.outcome import OutcomeKind
from iris_memory.cognitive.outcome_collector import OutcomeCollector


_NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
_USER = EntityReference("person:qq:1", "platform_uid", 1.0, ("qq:1",))


def _exp(
    event_id: str,
    content: str,
    *,
    reply_event_id: str | None = None,
    mode: str = "private",
    session_id: str = "g1",
) -> CanonicalExperience:
    raw = {}
    if reply_event_id is not None:
        raw["reply_event_id"] = reply_event_id
    event = ResolvedEvent(
        event_id=event_id,
        source="qq",
        occurred_at=_NOW,
        session_id=session_id,
        mode=mode,
        content=content,
        actor=_USER,
        raw_metadata=raw,
    )
    return CanonicalExperience(
        id=f"experience:{event_id}",
        event=event,
        subject=_USER,
        perspective=Perspective.INTERPERSONAL,
        provenance=("test",),
    )


def test_outcome_collector_is_invoked_on_attached_feedback():
    store = InMemoryEpisodeStore()
    observer = EpisodeShadowObserver(store)
    runtime = CognitiveRuntime(episode_observer=observer)

    runtime.run_behavior(_exp("qq:q1", "今晚几点观测？"))
    ep = store.all_episodes()[0]

    runtime.run_behavior(
        _exp("qq:thanks", "谢谢", reply_event_id="qq:q1")
    )
    kinds = {o.kind for o in store.get_outcomes(ep.episode_id)}
    assert OutcomeKind.EXPLICIT_ACKNOWLEDGEMENT in kinds


def test_finalized_late_feedback_creates_current_episode_and_targets_old():
    store = InMemoryEpisodeStore()
    observer = EpisodeShadowObserver(store)
    runtime = CognitiveRuntime(episode_observer=observer)

    runtime.run_behavior(_exp("qq:old", "昨天那个望远镜可以吗？"))
    old = store.all_episodes()[0]
    old = store.transition_state(old.episode_id, EpisodeState.SOFT_CLOSED, reason="idle")
    old = store.transition_state(old.episode_id, EpisodeState.FINALIZED, reason="grace")
    old_refs = old.event_refs

    runtime.run_behavior(
        _exp("qq:today", "你昨天说的那个不对", reply_event_id="qq:old")
    )

    after = store.get_episode(old.episode_id)
    assert after is not None
    assert after.state is EpisodeState.FINALIZED
    assert after.event_refs == old_refs

    episodes = [ep for ep in store.all_episodes() if ep.episode_id != old.episode_id]
    assert len(episodes) == 1
    kinds = {o.kind for o in store.get_outcomes(old.episode_id)}
    assert OutcomeKind.EXPLICIT_CORRECTION in kinds


def test_orphan_outcome_is_rejected():
    store = InMemoryEpisodeStore()
    from iris_memory.cognitive.outcome import (
        OutcomeObservation,
        make_outcome_observation_id,
    )

    outcome = OutcomeObservation(
        observation_id=make_outcome_observation_id(
            "ep:missing",
            OutcomeKind.EXPLICIT_CORRECTION,
            source_event_id="qq:1",
        ),
        target_episode_id="ep:missing",
        kind=OutcomeKind.EXPLICIT_CORRECTION,
        observed_at=_NOW,
        source_event_id="qq:1",
    )
    with pytest.raises(ValueError):
        store.record_outcome(outcome)


def test_mid_log_corruption_is_not_silently_skipped(tmp_path):
    from iris_memory.cognitive.episode import (
        Episode,
        EpisodeEventKind,
        EpisodeEventRef,
        EpisodeState,
        make_episode_id,
    )

    path = tmp_path / "episodes.jsonl"
    store = AppendOnlyEpisodeStore(path)
    ep = Episode(
        episode_id=make_episode_id("g1", "qq:1"),
        scope_id="g1",
        state=EpisodeState.OPEN,
        root_event_id="qq:1",
        opened_at=_NOW,
        last_activity_at=_NOW,
    )
    store.create_episode(ep)
    store.append_event_ref(
        ep.episode_id,
        EpisodeEventRef(
            ref_id="EXPERIENCE:qq:1",
            kind=EpisodeEventKind.EXPERIENCE,
            source_event_id="qq:1",
            observed_at=_NOW,
        ),
    )
    # Corrupt middle line, followed by another valid operation.
    with path.open("a", encoding="utf-8") as f:
        f.write("{bad-json}\n")
    # Trigger a second valid operation line after corruption.
    store.create_episode(
        Episode(
            episode_id=make_episode_id("g1", "qq:2"),
            scope_id="g1",
            state=EpisodeState.OPEN,
            root_event_id="qq:2",
            opened_at=_NOW,
            last_activity_at=_NOW,
        )
    )
    with pytest.raises(EpisodeLogCorruptionError):
        AppendOnlyEpisodeStore(path)


def test_main_episode_observer_wiring_helper(tmp_path, monkeypatch):
    import main
    from iris_memory.cognitive.iris_adapter import get_cognitive_runtime, reset_cognitive_runtime

    reset_cognitive_runtime()
    plugin = object.__new__(main.IrisMemoryPlugin)
    plugin.data_dir = str(tmp_path)
    plugin._episode_store = None
    plugin._episode_observer = None
    plugin._init_episode_shadow_observer()
    assert plugin._episode_store is not None
    assert plugin._episode_observer is not None
    assert get_cognitive_runtime().episode_observer is not None
    # Avoid cross-test pollution.
    get_cognitive_runtime().episode_observer = None
    reset_cognitive_runtime()
