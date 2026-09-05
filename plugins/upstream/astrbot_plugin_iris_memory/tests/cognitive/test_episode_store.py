"""EpisodeStore tests: in-memory, append-only replay, recovery, idempotency."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier, Thread

import pytest

from iris_memory.cognitive.contracts import EntityReference
from iris_memory.cognitive.episode import (
    Episode,
    EpisodeEventKind,
    EpisodeEventRef,
    EpisodeState,
    make_episode_event_ref_id,
    make_episode_id,
)
from iris_memory.cognitive.episode_store import (
    AppendOnlyEpisodeStore,
    EpisodeLogCorruptionError,
    EpisodePersistenceError,
    InMemoryEpisodeStore,
    _outcome_to_dict,
)
from iris_memory.cognitive.outcome import (
    OutcomeKind,
    OutcomeObservation,
    make_outcome_observation_id,
)

_NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
_ACTOR = EntityReference("person:qq:1", "platform_uid", 1.0, ("qq:1",))


def _ep() -> Episode:
    return Episode(
        episode_id=make_episode_id("g1", "qq:1"),
        scope_id="g1",
        state=EpisodeState.OPEN,
        root_event_id="qq:1",
        opened_at=_NOW,
        last_activity_at=_NOW,
        participants=(_ACTOR,),
    )


def _ref(source: str = "qq:1") -> EpisodeEventRef:
    return EpisodeEventRef(
        ref_id=make_episode_event_ref_id(
            EpisodeEventKind.EXPERIENCE,
            source_event_id=source,
        ),
        kind=EpisodeEventKind.EXPERIENCE,
        source_event_id=source,
        observed_at=_NOW,
        actor_entity=_ACTOR,
    )


def _outcome(episode_id: str, source_event_id: str, *, observed_at: datetime = _NOW) -> OutcomeObservation:
    return OutcomeObservation(
        observation_id=make_outcome_observation_id(
            episode_id, OutcomeKind.EXPLICIT_CORRECTION, source_event_id=source_event_id
        ),
        target_episode_id=episode_id,
        kind=OutcomeKind.EXPLICIT_CORRECTION,
        observed_at=observed_at,
        source_event_id=source_event_id,
    )


def test_in_memory_idempotent_append_and_terminal_state():
    store = InMemoryEpisodeStore()
    ep = store.create_episode(_ep())
    ep = store.append_event_ref(ep.episode_id, _ref())
    again = store.append_event_ref(ep.episode_id, _ref())
    assert len(again.event_refs) == 1
    ep = store.transition_state(ep.episode_id, EpisodeState.SOFT_CLOSED, reason="idle")
    ep = store.transition_state(ep.episode_id, EpisodeState.FINALIZED, reason="policy")
    assert ep.state is EpisodeState.FINALIZED


def test_append_only_replay_restores_state_and_outcomes(tmp_path: Path):
    path = tmp_path / "episodes.jsonl"
    store = AppendOnlyEpisodeStore(path)
    ep = store.create_episode(_ep())
    ep = store.append_event_ref(ep.episode_id, _ref())
    outcome = OutcomeObservation(
        observation_id=make_outcome_observation_id(
            ep.episode_id,
            OutcomeKind.EXPLICIT_ACKNOWLEDGEMENT,
            source_event_id="qq:2",
        ),
        target_episode_id=ep.episode_id,
        kind=OutcomeKind.EXPLICIT_ACKNOWLEDGEMENT,
        observed_at=_NOW,
        source_event_id="qq:2",
    )
    store.record_outcome(outcome)
    ep = store.transition_state(ep.episode_id, EpisodeState.SOFT_CLOSED, reason="idle")
    ep = store.transition_state(ep.episode_id, EpisodeState.FINALIZED, reason="grace")

    replay = AppendOnlyEpisodeStore(path)
    restored = replay.get_episode(ep.episode_id)
    assert restored is not None
    assert restored.state is EpisodeState.FINALIZED
    assert len(restored.event_refs) == 1
    assert len(replay.get_outcomes(ep.episode_id)) == 1


def test_outcome_append_failure_does_not_publish_or_survive_restart(tmp_path: Path, monkeypatch):
    path = tmp_path / "episodes.jsonl"
    store = AppendOnlyEpisodeStore(path)
    ep = store.create_episode(_ep())
    durable_end = path.stat().st_size
    real_fsync = store._fsync
    calls = 0

    def fail_first_sync(file_handle: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected fsync failure")
        real_fsync(file_handle)

    monkeypatch.setattr(store, "_fsync", fail_first_sync)
    with pytest.raises(EpisodePersistenceError):
        store.record_outcome(_outcome(ep.episode_id, "qq:outcome-failed"))

    assert store.get_outcomes(ep.episode_id) == ()
    assert path.stat().st_size == durable_end
    replay = AppendOnlyEpisodeStore(path)
    assert replay.get_outcomes(ep.episode_id) == ()


def test_finalization_append_failure_does_not_publish_boundary_or_state(tmp_path: Path, monkeypatch):
    path = tmp_path / "episodes.jsonl"
    store = AppendOnlyEpisodeStore(path)
    ep = store.create_episode(_ep())
    ep = store.transition_state(ep.episode_id, EpisodeState.SOFT_CLOSED, reason="idle")
    real_fsync = store._fsync
    calls = 0

    def fail_first_sync(file_handle: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected fsync failure")
        real_fsync(file_handle)

    monkeypatch.setattr(store, "_fsync", fail_first_sync)
    with pytest.raises(EpisodePersistenceError):
        store.transition_state(ep.episode_id, EpisodeState.FINALIZED, reason="grace")

    assert store.get_episode(ep.episode_id).state is EpisodeState.SOFT_CLOSED
    assert store.get_finalization_boundary(ep.episode_id) is None
    replay = AppendOnlyEpisodeStore(path)
    assert replay.get_episode(ep.episode_id).state is EpisodeState.SOFT_CLOSED
    assert replay.get_finalization_boundary(ep.episode_id) is None


def test_episode_creation_failure_does_not_publish_volatile_episode(tmp_path: Path, monkeypatch):
    path = tmp_path / "episodes.jsonl"
    store = AppendOnlyEpisodeStore(path)
    real_fsync = store._fsync
    calls = 0

    def fail_first_sync(file_handle: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected fsync failure")
        real_fsync(file_handle)

    monkeypatch.setattr(store, "_fsync", fail_first_sync)
    with pytest.raises(EpisodePersistenceError):
        store.create_episode(_ep())

    assert store.get_episode(_ep().episode_id) is None
    assert AppendOnlyEpisodeStore(path).get_episode(_ep().episode_id) is None


def test_journal_positions_define_finalized_outcome_set_and_restart_is_stable(tmp_path: Path):
    path = tmp_path / "episodes.jsonl"
    store = AppendOnlyEpisodeStore(path)
    ep = store.create_episode(_ep())
    before = store.record_outcome(_outcome(ep.episode_id, "qq:before"))
    ep = store.transition_state(ep.episode_id, EpisodeState.SOFT_CLOSED, reason="idle")
    ep = store.transition_state(ep.episode_id, EpisodeState.FINALIZED, reason="grace")
    boundary = store.get_finalization_boundary(ep.episode_id)

    assert boundary is not None
    assert store.get_finalized_outcomes(ep.episode_id) == (before,)
    assert store._outcome_positions[before.observation_id] < boundary

    replay = AppendOnlyEpisodeStore(path)
    assert replay.get_finalization_boundary(ep.episode_id) == boundary
    assert replay.get_finalized_outcomes(ep.episode_id) == (before,)


def test_post_finalization_backdated_outcome_is_excluded_by_journal_position(tmp_path: Path):
    path = tmp_path / "episodes.jsonl"
    store = AppendOnlyEpisodeStore(path)
    ep = store.create_episode(_ep())
    ep = store.transition_state(ep.episode_id, EpisodeState.SOFT_CLOSED, reason="idle")
    ep = store.transition_state(ep.episode_id, EpisodeState.FINALIZED, reason="grace")
    late = store.record_outcome(
        _outcome(ep.episode_id, "qq:late", observed_at=_NOW - timedelta(days=365))
    )

    boundary = store.get_finalization_boundary(ep.episode_id)
    assert boundary is not None
    assert store._outcome_positions[late.observation_id] > boundary
    assert store.get_finalized_outcomes(ep.episode_id) == ()


def test_concurrent_outcome_and_finalization_use_one_journal_winner(tmp_path: Path):
    store = AppendOnlyEpisodeStore(tmp_path / "episodes.jsonl")
    ep = store.create_episode(_ep())
    ep = store.transition_state(ep.episode_id, EpisodeState.SOFT_CLOSED, reason="idle")
    outcome = _outcome(ep.episode_id, "qq:concurrent")
    barrier = Barrier(3)
    failures: list[Exception] = []

    def record_outcome() -> None:
        try:
            barrier.wait()
            store.record_outcome(outcome)
        except Exception as exc:  # noqa: BLE001 - asserted by the parent thread
            failures.append(exc)

    def finalize() -> None:
        try:
            barrier.wait()
            store.transition_state(ep.episode_id, EpisodeState.FINALIZED, reason="grace")
        except Exception as exc:  # noqa: BLE001 - asserted by the parent thread
            failures.append(exc)

    outcome_thread = Thread(target=record_outcome)
    finalization_thread = Thread(target=finalize)
    outcome_thread.start()
    finalization_thread.start()
    barrier.wait()
    outcome_thread.join()
    finalization_thread.join()

    assert failures == []
    boundary = store.get_finalization_boundary(ep.episode_id)
    assert boundary is not None
    is_included = outcome in store.get_finalized_outcomes(ep.episode_id)
    assert is_included is (store._outcome_positions[outcome.observation_id] < boundary)


def test_uncertain_tail_poisoning_fails_closed(tmp_path: Path, monkeypatch):
    path = tmp_path / "episodes.jsonl"
    store = AppendOnlyEpisodeStore(path)
    ep = store.create_episode(_ep())

    def fail_every_sync(_file_handle: object) -> None:
        raise OSError("injected fsync failure")

    monkeypatch.setattr(store, "_fsync", fail_every_sync)
    with pytest.raises(EpisodePersistenceError):
        store.record_outcome(_outcome(ep.episode_id, "qq:uncertain-tail"))
    with pytest.raises(EpisodePersistenceError):
        store.transition_state(ep.episode_id, EpisodeState.SOFT_CLOSED, reason="idle")


def test_illegal_repeat_finalization_cannot_select_newer_boundary(tmp_path: Path):
    store = AppendOnlyEpisodeStore(tmp_path / "episodes.jsonl")
    ep = store.create_episode(_ep())
    ep = store.transition_state(ep.episode_id, EpisodeState.SOFT_CLOSED, reason="idle")
    ep = store.transition_state(ep.episode_id, EpisodeState.FINALIZED, reason="grace")
    boundary = store.get_finalization_boundary(ep.episode_id)

    with pytest.raises(ValueError):
        store.transition_state(ep.episode_id, EpisodeState.FINALIZED, reason="again")
    assert store.get_finalization_boundary(ep.episode_id) == boundary


def test_restart_marks_open_as_interrupted(tmp_path: Path):
    path = tmp_path / "episodes.jsonl"
    store = AppendOnlyEpisodeStore(path)
    ep = store.create_episode(_ep())

    # Simulate restart by constructing a new store over the same file.
    replay = AppendOnlyEpisodeStore(path)
    recovered = replay.get_episode(ep.episode_id)
    assert recovered is not None
    assert recovered.state is EpisodeState.INTERRUPTED


def test_duplicate_replay_does_not_duplicate_refs(tmp_path: Path):
    path = tmp_path / "episodes.jsonl"
    store = AppendOnlyEpisodeStore(path)
    ep = store.create_episode(_ep())
    store.append_event_ref(ep.episode_id, _ref())
    store.append_event_ref(ep.episode_id, _ref())  # no-op

    replay = AppendOnlyEpisodeStore(path)
    restored = replay.get_episode(ep.episode_id)
    assert restored is not None
    assert len(restored.event_refs) == 1


def test_corrupt_tail_does_not_destroy_previous_valid_log(tmp_path: Path):
    path = tmp_path / "episodes.jsonl"
    store = AppendOnlyEpisodeStore(path)
    ep = store.create_episode(_ep())
    store.append_event_ref(ep.episode_id, _ref())
    # True crash tail: truncated JSON with no record terminator.
    with path.open("a", encoding="utf-8") as f:
        f.write('{"schema_version":"1","operation_kind":"EPIS')

    replay = AppendOnlyEpisodeStore(path)
    restored = replay.get_episode(ep.episode_id)
    assert restored is not None
    assert len(restored.event_refs) == 1
    # Recovered file must be clean for future appends.
    assert path.read_text(encoding="utf-8").endswith("\n")


def test_active_index_eviction_is_not_finalized():
    store = InMemoryEpisodeStore()
    ep = store.create_episode(_ep())
    # Operational routing eviction must not change semantic lifecycle state.
    evicted = store.evict_from_active_index(ep.episode_id)
    assert evicted is not None
    assert evicted.state is EpisodeState.OPEN
    assert store.get_episode(ep.episode_id) is not None
    assert store.find_active_by_scope("g1") == ()


def test_complete_garbage_final_line_with_newline_raises(tmp_path: Path):
    path = tmp_path / "episodes.jsonl"
    store = AppendOnlyEpisodeStore(path)
    store.create_episode(_ep())
    with path.open("a", encoding="utf-8") as f:
        f.write("THIS IS COMPLETE GARBAGE\n")
    with pytest.raises(EpisodeLogCorruptionError):
        AppendOnlyEpisodeStore(path)


def test_garbage_no_newline_raises_when_not_json_start(tmp_path: Path):
    path = tmp_path / "episodes.jsonl"
    store = AppendOnlyEpisodeStore(path)
    store.create_episode(_ep())
    with path.open("a", encoding="utf-8") as f:
        f.write("THIS IS COMPLETE GARBAGE")
    with pytest.raises(EpisodeLogCorruptionError):
        AppendOnlyEpisodeStore(path)


def test_unknown_operation_kind_raises(tmp_path: Path):
    path = tmp_path / "episodes.jsonl"
    store = AppendOnlyEpisodeStore(path)
    store.create_episode(_ep())
    record = {
        "schema_version": 1,
        "operation_kind": "MADE_UP",
        "episode_id": "ep",
        "payload": {},
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    with pytest.raises(EpisodeLogCorruptionError):
        AppendOnlyEpisodeStore(path)


def test_schema_invalid_final_json_raises(tmp_path: Path):
    path = tmp_path / "episodes.jsonl"
    store = AppendOnlyEpisodeStore(path)
    store.create_episode(_ep())
    # Valid JSON but missing required payload.
    record = {
        "schema_version": 1,
        "operation_kind": "EPISODE_CREATED",
        "episode_id": "ep",
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    with pytest.raises(EpisodeLogCorruptionError):
        AppendOnlyEpisodeStore(path)


def test_unsupported_schema_version_raises(tmp_path: Path):
    path = tmp_path / "episodes.jsonl"
    store = AppendOnlyEpisodeStore(path)
    store.create_episode(_ep())
    record = {
        "schema_version": 999,
        "operation_kind": "EPISODE_CREATED",
        "episode_id": "ep",
        "payload": {},
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    with pytest.raises(EpisodeLogCorruptionError):
        AppendOnlyEpisodeStore(path)


def test_orphan_outcome_replay_raises(tmp_path: Path):
    path = tmp_path / "episodes.jsonl"
    store = AppendOnlyEpisodeStore(path)
    store.create_episode(_ep())
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
    record = {
        "schema_version": 1,
        "operation_kind": "OUTCOME_RECORDED",
        "episode_id": outcome.target_episode_id,
        "payload": _outcome_to_dict(outcome),
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    with pytest.raises(EpisodeLogCorruptionError):
        AppendOnlyEpisodeStore(path)


def test_truncated_tail_recovery_then_append_then_restart(tmp_path: Path):
    path = tmp_path / "episodes.jsonl"
    store = AppendOnlyEpisodeStore(path)
    ep = store.create_episode(_ep())
    store.append_event_ref(ep.episode_id, _ref())
    with path.open("a", encoding="utf-8") as f:
        f.write('{"schema_version":"1","operation_kind":"EPIS')
    recovered = AppendOnlyEpisodeStore(path)
    assert recovered.get_episode(ep.episode_id) is not None
    # Append a new valid operation after recovery.
    ep2 = Episode(
        episode_id=make_episode_id("g1", "qq:2"),
        scope_id="g1",
        state=EpisodeState.OPEN,
        root_event_id="qq:2",
        opened_at=_NOW,
        last_activity_at=_NOW,
    )
    recovered.create_episode(ep2)
    # Second restart must replay both cleanly.
    final = AppendOnlyEpisodeStore(path)
    assert final.get_episode(ep.episode_id) is not None
    assert final.get_episode(ep2.episode_id) is not None


@pytest.mark.parametrize("fragment", [
    '{"a":',
    '{"a":1',
    '{"a":[1,2',
    '{"a":{"b":1',
    '{"a":"hello',
])
def test_recoverable_truncated_json_tails(tmp_path: Path, fragment: str):
    path = tmp_path / "episodes.jsonl"
    store = AppendOnlyEpisodeStore(path)
    store.create_episode(_ep())
    with path.open("a", encoding="utf-8") as f:
        f.write(fragment)
    recovered = AppendOnlyEpisodeStore(path)
    assert recovered.get_episode(_ep().episode_id) is not None


@pytest.mark.parametrize("fragment", [
    '{"foo": BAD}',
    '{"operation_kind": BANANA}',
    '{"schema_version":"1", trailing-garbage}',
    '{"a":"\\q"}',
    '{"a": tru',
    '{foo: 1}',
    '{"a": @}',
])
def test_ambiguous_or_invalid_json_tails_are_corruption(tmp_path: Path, fragment: str):
    path = tmp_path / "episodes.jsonl"
    store = AppendOnlyEpisodeStore(path)
    store.create_episode(_ep())
    with path.open("a", encoding="utf-8") as f:
        f.write(fragment)
    with pytest.raises(EpisodeLogCorruptionError):
        AppendOnlyEpisodeStore(path)


@pytest.mark.parametrize("fragment", [
    '{"a":',
    '{"a":1',
])
def test_incomplete_json_with_newline_is_corruption(tmp_path: Path, fragment: str):
    path = tmp_path / "episodes.jsonl"
    store = AppendOnlyEpisodeStore(path)
    store.create_episode(_ep())
    with path.open("a", encoding="utf-8") as f:
        f.write(fragment + "\n")
    with pytest.raises(EpisodeLogCorruptionError):
        AppendOnlyEpisodeStore(path)
