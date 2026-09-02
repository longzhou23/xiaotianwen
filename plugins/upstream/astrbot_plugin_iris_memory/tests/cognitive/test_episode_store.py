"""EpisodeStore tests: in-memory, append-only replay, recovery, idempotency."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from iris_memory.cognitive.episode import (
    Episode,
    EpisodeEventKind,
    EpisodeEventRef,
    EpisodeState,
    make_episode_event_ref_id,
    make_episode_id,
)
from iris_memory.cognitive.episode_store import (
    _outcome_to_dict,
    AppendOnlyEpisodeStore,
    EpisodeLogCorruptionError,
    InMemoryEpisodeStore,
)
from iris_memory.cognitive.outcome import (
    OutcomeKind,
    OutcomeObservation,
    make_outcome_observation_id,
)
from iris_memory.cognitive.contracts import EntityReference


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
