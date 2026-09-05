"""P2l.1 tests for the bounded durable Episode lifecycle owner."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from iris_memory.cognitive.contracts import EntityReference
from iris_memory.cognitive.episode import (
    Episode,
    EpisodeEventKind,
    EpisodeEventRef,
    EpisodeState,
    make_episode_event_ref_id,
)
from iris_memory.cognitive.episode_lifecycle import EpisodeLifecycleOwnerV1
from iris_memory.cognitive.episode_store import AppendOnlyEpisodeStore
from iris_memory.cognitive.outcome import (
    OutcomeKind,
    OutcomeObservation,
    make_outcome_observation_id,
)

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
ACTOR = EntityReference("person:qq:1", "platform_uid", 1.0, ("qq:1",))


def _episode(*, state: EpisodeState = EpisodeState.OPEN, last_activity: datetime = NOW) -> Episode:
    return Episode(
        episode_id="episode:private:1:root",
        scope_id="private:1",
        state=state,
        root_event_id="root",
        opened_at=NOW - timedelta(minutes=30),
        last_activity_at=last_activity,
        participants=(ACTOR,),
        provenance=("test",),
    )


def _store(tmp_path: Path, episode: Episode) -> AppendOnlyEpisodeStore:
    store = AppendOnlyEpisodeStore(tmp_path / "episodes.jsonl")
    store.create_episode(episode)
    return store


def _owner(store: AppendOnlyEpisodeStore, calls: list[tuple[str, tuple]], *, satisfied=None) -> EpisodeLifecycleOwnerV1:
    def complete(episode: Episode, outcomes: tuple) -> None:
        calls.append((episode.episode_id, outcomes))

    return EpisodeLifecycleOwnerV1(
        store,
        complete_finalized=complete,
        completion_satisfied=satisfied or (lambda _episode, _outcomes: False),
    )


def _outcome(episode_id: str, source_event_id: str, at: datetime) -> OutcomeObservation:
    return OutcomeObservation(
        observation_id=make_outcome_observation_id(
            episode_id, OutcomeKind.EXPLICIT_CORRECTION, source_event_id=source_event_id
        ),
        target_episode_id=episode_id,
        kind=OutcomeKind.EXPLICIT_CORRECTION,
        observed_at=at,
        source_event_id=source_event_id,
    )


def test_open_recent_stays_open(tmp_path: Path) -> None:
    store = _store(tmp_path, _episode(last_activity=NOW - timedelta(minutes=14)))
    calls: list[tuple[str, tuple]] = []
    _owner(store, calls).scan_once(now=NOW)
    assert store.get_episode("episode:private:1:root").state is EpisodeState.OPEN
    assert calls == []


def test_open_inactive_soft_closes_without_advancing_genuine_activity(tmp_path: Path) -> None:
    activity = NOW - timedelta(minutes=15)
    store = _store(tmp_path, _episode(last_activity=activity))
    calls: list[tuple[str, tuple]] = []
    _owner(store, calls).scan_once(now=NOW)
    episode = store.get_episode("episode:private:1:root")
    assert episode.state is EpisodeState.SOFT_CLOSED
    assert episode.last_activity_at == activity
    assert episode.soft_closed_at == NOW


def test_recovery_restart_preserves_genuine_last_activity(tmp_path: Path) -> None:
    path = tmp_path / "episodes.jsonl"
    activity = NOW - timedelta(hours=2)
    original = AppendOnlyEpisodeStore(path)
    original.create_episode(_episode(last_activity=activity))
    recovered = AppendOnlyEpisodeStore(path)
    episode = recovered.get_episode("episode:private:1:root")
    assert episode.state is EpisodeState.INTERRUPTED
    assert episode.last_activity_at == activity


def test_interrupted_inactive_soft_closes(tmp_path: Path) -> None:
    store = _store(tmp_path, _episode(last_activity=NOW - timedelta(minutes=16)))
    store.transition_state(
        "episode:private:1:root", EpisodeState.INTERRUPTED,
        reason="recovery_restart", at=NOW - timedelta(minutes=1), preserve_last_activity=True,
    )
    calls: list[tuple[str, tuple]] = []
    _owner(store, calls).scan_once(now=NOW)
    assert store.get_episode("episode:private:1:root").state is EpisodeState.SOFT_CLOSED


def test_soft_closed_grace_finalizes_once_and_uses_durable_outcomes(tmp_path: Path) -> None:
    store = _store(tmp_path, _episode(last_activity=NOW - timedelta(minutes=20)))
    before = store.record_outcome(_outcome("episode:private:1:root", "before", NOW - timedelta(minutes=20)))
    store.transition_state(
        "episode:private:1:root", EpisodeState.SOFT_CLOSED,
        reason="episode_lifecycle_inactivity", at=NOW - timedelta(minutes=15), preserve_last_activity=True,
    )
    calls: list[tuple[str, tuple]] = []
    completed = False

    def complete(episode: Episode, outcomes: tuple) -> None:
        nonlocal completed
        calls.append((episode.episode_id, outcomes))
        completed = True

    owner = EpisodeLifecycleOwnerV1(
        store,
        complete_finalized=complete,
        completion_satisfied=lambda _episode, _outcomes: completed,
    )
    owner.scan_once(now=NOW)
    assert store.get_episode("episode:private:1:root").state is EpisodeState.FINALIZED
    assert calls == [("episode:private:1:root", (before,))]
    owner.scan_once(now=NOW + timedelta(minutes=1))
    assert len(calls) == 1


def test_late_genuine_activity_during_soft_close_extends_grace(tmp_path: Path) -> None:
    store = _store(tmp_path, _episode(last_activity=NOW - timedelta(minutes=20)))
    store.transition_state(
        "episode:private:1:root", EpisodeState.SOFT_CLOSED,
        reason="episode_lifecycle_inactivity", at=NOW - timedelta(minutes=14), preserve_last_activity=True,
    )
    activity = NOW - timedelta(minutes=4)
    store.append_event_ref(
        "episode:private:1:root",
        EpisodeEventRef(
            ref_id=make_episode_event_ref_id(EpisodeEventKind.EXPERIENCE, source_event_id="late-event"),
            kind=EpisodeEventKind.EXPERIENCE,
            source_event_id="late-event",
            observed_at=activity,
            actor_entity=ACTOR,
        ),
    )
    calls: list[tuple[str, tuple]] = []
    _owner(store, calls).scan_once(now=NOW)
    assert store.get_episode("episode:private:1:root").state is EpisodeState.SOFT_CLOSED
    assert calls == []


def test_post_finalization_backdated_outcome_is_excluded(tmp_path: Path) -> None:
    store = _store(tmp_path, _episode(last_activity=NOW - timedelta(minutes=20)))
    store.transition_state(
        "episode:private:1:root", EpisodeState.SOFT_CLOSED,
        reason="episode_lifecycle_inactivity", at=NOW - timedelta(minutes=15), preserve_last_activity=True,
    )
    calls: list[tuple[str, tuple]] = []
    _owner(store, calls).scan_once(now=NOW)
    late = store.record_outcome(_outcome("episode:private:1:root", "late", NOW - timedelta(days=365)))
    assert late not in store.get_finalized_outcomes("episode:private:1:root")


def test_restart_preserves_finalized_boundary_and_exact_outcome_set(tmp_path: Path) -> None:
    path = tmp_path / "episodes.jsonl"
    store = AppendOnlyEpisodeStore(path)
    store.create_episode(_episode(last_activity=NOW - timedelta(minutes=20)))
    before = store.record_outcome(_outcome("episode:private:1:root", "before", NOW - timedelta(minutes=20)))
    store.transition_state(
        "episode:private:1:root", EpisodeState.SOFT_CLOSED,
        reason="episode_lifecycle_inactivity", at=NOW - timedelta(minutes=15), preserve_last_activity=True,
    )
    store.transition_state(
        "episode:private:1:root", EpisodeState.FINALIZED,
        reason="episode_lifecycle_grace_elapsed", at=NOW, preserve_last_activity=True,
    )
    boundary = store.get_finalization_boundary("episode:private:1:root")
    replayed = AppendOnlyEpisodeStore(path)
    assert replayed.get_episode("episode:private:1:root").state is EpisodeState.FINALIZED
    assert replayed.get_finalization_boundary("episode:private:1:root") == boundary
    assert replayed.get_finalized_outcomes("episode:private:1:root") == (before,)


def test_failed_completion_retries_without_reopening_finalized_episode(tmp_path: Path) -> None:
    store = _store(tmp_path, _episode(last_activity=NOW - timedelta(minutes=20)))
    store.transition_state(
        "episode:private:1:root", EpisodeState.SOFT_CLOSED,
        reason="episode_lifecycle_inactivity", at=NOW - timedelta(minutes=15), preserve_last_activity=True,
    )
    attempts = 0

    def complete(_episode: Episode, _outcomes: tuple) -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("archive unavailable")

    owner = EpisodeLifecycleOwnerV1(
        store, complete_finalized=complete, completion_satisfied=lambda _episode, _outcomes: False
    )
    owner.scan_once(now=NOW)
    owner.scan_once(now=NOW + timedelta(minutes=1))
    assert attempts == 2
    assert store.get_episode("episode:private:1:root").state is EpisodeState.FINALIZED


def test_satisfied_finalized_episode_is_not_completed_again(tmp_path: Path) -> None:
    store = _store(tmp_path, _episode(last_activity=NOW - timedelta(minutes=20)))
    store.transition_state(
        "episode:private:1:root", EpisodeState.SOFT_CLOSED,
        reason="episode_lifecycle_inactivity", at=NOW - timedelta(minutes=15), preserve_last_activity=True,
    )
    store.transition_state(
        "episode:private:1:root", EpisodeState.FINALIZED,
        reason="episode_lifecycle_grace_elapsed", at=NOW, preserve_last_activity=True,
    )
    calls: list[tuple[str, tuple]] = []
    _owner(store, calls, satisfied=lambda _episode, _outcomes: True).scan_once(now=NOW)
    assert calls == []


def test_repeated_owner_start_creates_one_task(tmp_path: Path) -> None:
    async def exercise() -> None:
        store = _store(tmp_path, _episode())
        owner = _owner(store, [])
        await owner.start()
        first = owner.task
        await owner.start()
        assert owner.task is first
        await owner.shutdown()
        assert owner.task is None

    asyncio.run(exercise())


def test_config_default_keeps_auto_finalization_off() -> None:
    schema = Path(__file__).parents[2] / "_conf_schema.json"
    data = json.loads(schema.read_text(encoding="utf-8-sig"))
    assert data["episode_lifecycle"]["items"]["auto_finalize"]["default"] is False
