"""EpisodeStore implementations: protocol, in-memory, append-only JSONL.

The append-only store is Cognitive behavior-history storage, not Iris Memory.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import ClassVar, Protocol, runtime_checkable
from uuid import uuid4

from .contracts import EntityReference
from .episode import (
    Episode,
    EpisodeEventKind,
    EpisodeEventRef,
    EpisodeState,
)
from .outcome import OutcomeObservation

logger = logging.getLogger(__name__)


class EpisodeLogCorruptionError(RuntimeError):
    """Raised when the Episode JSONL log has unrecoverable mid-log corruption."""


class EpisodePersistenceError(RuntimeError):
    """Raised when an Episode authority operation cannot reach durability."""

_LEGAL_TRANSITIONS: dict[EpisodeState, set[EpisodeState]] = {
    EpisodeState.OPEN: {EpisodeState.SOFT_CLOSED, EpisodeState.INTERRUPTED},
    EpisodeState.SOFT_CLOSED: {EpisodeState.OPEN, EpisodeState.FINALIZED},
    EpisodeState.FINALIZED: set(),
    EpisodeState.INTERRUPTED: {EpisodeState.OPEN, EpisodeState.SOFT_CLOSED, EpisodeState.FINALIZED},
}


@runtime_checkable
class EpisodeStore(Protocol):
    def create_episode(self, episode: Episode) -> Episode: ...
    def get_episode(self, episode_id: str) -> Episode | None: ...
    def append_event_ref(self, episode_id: str, ref: EpisodeEventRef) -> Episode: ...
    def transition_state(
        self,
        episode_id: str,
        new_state: EpisodeState,
        *,
        reason: str,
        at: datetime | None = None,
        preserve_last_activity: bool = False,
    ) -> Episode: ...
    def find_active_by_scope(self, scope_id: str) -> tuple[Episode, ...]: ...
    def find_episode_by_event_ref(self, ref_id: str) -> Episode | None: ...
    def find_episode_by_trace_id(self, trace_id: str) -> Episode | None: ...
    def find_episode_by_source_event_id(self, source_event_id: str) -> Episode | None: ...
    def record_outcome(self, outcome: OutcomeObservation) -> OutcomeObservation: ...
    def get_outcomes(self, episode_id: str | None = None) -> tuple[OutcomeObservation, ...]: ...
    def all_episodes(self) -> tuple[Episode, ...]: ...


def _entity_to_dict(entity: EntityReference) -> dict:
    return {
        "entity_id": entity.entity_id,
        "source": entity.source,
        "confidence": entity.confidence,
        "evidence": list(entity.evidence),
    }


def _entity_from_dict(data: dict) -> EntityReference:
    return EntityReference(
        entity_id=data["entity_id"],
        source=data["source"],
        confidence=float(data["confidence"]),
        evidence=tuple(data.get("evidence") or ()),
    )


def _ref_to_dict(ref: EpisodeEventRef) -> dict:
    return {
        "ref_id": ref.ref_id,
        "kind": ref.kind.value,
        "source_event_id": ref.source_event_id,
        "trace_id": ref.trace_id,
        "execution_record_id": ref.execution_record_id,
        "observed_at": ref.observed_at.isoformat() if ref.observed_at else None,
        "actor_entity": _entity_to_dict(ref.actor_entity) if ref.actor_entity else None,
    }


def _ref_from_dict(data: dict) -> EpisodeEventRef:
    actor = _entity_from_dict(data["actor_entity"]) if data.get("actor_entity") else None
    return EpisodeEventRef(
        ref_id=data["ref_id"],
        kind=EpisodeEventKind(data["kind"]),
        source_event_id=data.get("source_event_id"),
        trace_id=data.get("trace_id"),
        execution_record_id=data.get("execution_record_id"),
        observed_at=datetime.fromisoformat(data["observed_at"]) if data.get("observed_at") else None,
        actor_entity=actor,
    )


def _episode_to_dict(ep: Episode) -> dict:
    return {
        "episode_id": ep.episode_id,
        "scope_id": ep.scope_id,
        "state": ep.state.value,
        "root_event_id": ep.root_event_id,
        "opened_at": ep.opened_at.isoformat(),
        "last_activity_at": ep.last_activity_at.isoformat(),
        "participants": [_entity_to_dict(p) for p in ep.participants],
        "topic_hint": ep.topic_hint,
        "event_refs": [_ref_to_dict(r) for r in ep.event_refs],
        "unresolved_refs": list(ep.unresolved_refs),
        "soft_closed_at": ep.soft_closed_at.isoformat() if ep.soft_closed_at else None,
        "finalized_at": ep.finalized_at.isoformat() if ep.finalized_at else None,
        "revision": ep.revision,
        "provenance": list(ep.provenance),
    }


def _episode_from_dict(data: dict) -> Episode:
    return Episode(
        episode_id=data["episode_id"],
        scope_id=data["scope_id"],
        state=EpisodeState(data["state"]),
        root_event_id=data["root_event_id"],
        opened_at=datetime.fromisoformat(data["opened_at"]),
        last_activity_at=datetime.fromisoformat(data["last_activity_at"]),
        participants=tuple(_entity_from_dict(p) for p in data.get("participants") or ()),
        topic_hint=data.get("topic_hint"),
        event_refs=tuple(_ref_from_dict(r) for r in data.get("event_refs") or ()),
        unresolved_refs=tuple(data.get("unresolved_refs") or ()),
        soft_closed_at=datetime.fromisoformat(data["soft_closed_at"]) if data.get("soft_closed_at") else None,
        finalized_at=datetime.fromisoformat(data["finalized_at"]) if data.get("finalized_at") else None,
        revision=int(data.get("revision") or 0),
        provenance=tuple(data.get("provenance") or ()),
    )


def _outcome_to_dict(outcome: OutcomeObservation) -> dict:
    return {
        "observation_id": outcome.observation_id,
        "target_episode_id": outcome.target_episode_id,
        "kind": outcome.kind.value,
        "observed_at": outcome.observed_at.isoformat(),
        "source_event_id": outcome.source_event_id,
        "source_ref_id": outcome.source_ref_id,
        "actor_entity": _entity_to_dict(outcome.actor_entity) if outcome.actor_entity else None,
        "target_entity": _entity_to_dict(outcome.target_entity) if outcome.target_entity else None,
        "explicitness": outcome.explicitness.value,
        "confidence": outcome.confidence,
        "evidence": list(outcome.evidence),
        "producer": outcome.producer,
        "provenance": list(outcome.provenance),
    }


def _outcome_from_dict(data: dict) -> OutcomeObservation:
    from .outcome import OutcomeExplicitness, OutcomeKind

    actor = _entity_from_dict(data["actor_entity"]) if data.get("actor_entity") else None
    target = _entity_from_dict(data["target_entity"]) if data.get("target_entity") else None
    return OutcomeObservation(
        observation_id=data["observation_id"],
        target_episode_id=data["target_episode_id"],
        kind=OutcomeKind(data["kind"]),
        observed_at=datetime.fromisoformat(data["observed_at"]),
        source_event_id=data.get("source_event_id"),
        source_ref_id=data.get("source_ref_id"),
        actor_entity=actor,
        target_entity=target,
        explicitness=OutcomeExplicitness(data.get("explicitness") or "STRUCTURAL"),
        confidence=float(data.get("confidence") or 1.0),
        evidence=tuple(data.get("evidence") or ()),
        producer=data.get("producer") or "deterministic_outcome_collector",
        provenance=tuple(data.get("provenance") or ()),
    )


class InMemoryEpisodeStore:
    """Unit-test and lightweight runtime store."""

    def __init__(self) -> None:
        self._episodes: dict[str, Episode] = {}
        self._outcomes: dict[str, OutcomeObservation] = {}
        self._ref_index: dict[str, str] = {}
        self._trace_index: dict[str, str] = {}
        self._source_event_index: dict[str, str] = {}
        self._outcome_dedupe: dict[str, str] = {}
        self._active_ids: set[str] = set()

    def _index_episode(self, ep: Episode) -> None:
        self._episodes[ep.episode_id] = ep
        if ep.state in (EpisodeState.OPEN, EpisodeState.SOFT_CLOSED):
            self._active_ids.add(ep.episode_id)
        else:
            self._active_ids.discard(ep.episode_id)
        for ref in ep.event_refs:
            self._ref_index[ref.ref_id] = ep.episode_id
            if ref.trace_id:
                self._trace_index[ref.trace_id] = ep.episode_id
            if ref.source_event_id:
                self._source_event_index[ref.source_event_id] = ep.episode_id

    def _candidate_create_episode(self, episode: Episode) -> tuple[Episode, bool]:
        existing = self._episodes.get(episode.episode_id)
        return (existing, False) if existing is not None else (episode, True)

    def _candidate_append_event_ref(
        self, episode_id: str, ref: EpisodeEventRef
    ) -> tuple[Episode, bool]:
        ep = self._episodes.get(episode_id)
        if ep is None:
            raise KeyError(episode_id)
        if ep.state is EpisodeState.FINALIZED:
            raise ValueError("cannot append event to FINALIZED Episode")
        if any(current.ref_id == ref.ref_id for current in ep.event_refs):
            return ep, False
        participants = list(ep.participants)
        if ref.actor_entity and all(p.entity_id != ref.actor_entity.entity_id for p in participants):
            participants.append(ref.actor_entity)
        return replace(
            ep,
            last_activity_at=max(ep.last_activity_at, ref.observed_at or ep.last_activity_at),
            participants=tuple(participants),
            event_refs=ep.event_refs + (ref,),
            revision=ep.revision + 1,
            provenance=ep.provenance + (f"event_attached:{ref.ref_id}",),
        ), True

    def _candidate_transition_state(
        self,
        episode_id: str,
        new_state: EpisodeState,
        *,
        reason: str,
        at: datetime | None = None,
        preserve_last_activity: bool = False,
    ) -> Episode:
        ep = self._episodes.get(episode_id)
        if ep is None:
            raise KeyError(episode_id)
        if new_state not in _LEGAL_TRANSITIONS[ep.state]:
            raise ValueError(f"illegal transition {ep.state.value} -> {new_state.value}")
        now = at or datetime.now().astimezone()
        return replace(
            ep,
            state=new_state,
            # State transitions are not necessarily genuine interaction.  The
            # lifecycle owner and restart recovery preserve this factual clock
            # so a durable inactivity/grace calculation cannot be reset by its
            # own administrative transition.
            last_activity_at=(ep.last_activity_at if preserve_last_activity else max(ep.last_activity_at, now)),
            soft_closed_at=now if new_state is EpisodeState.SOFT_CLOSED else ep.soft_closed_at,
            finalized_at=now if new_state is EpisodeState.FINALIZED else ep.finalized_at,
            revision=ep.revision + 1,
            provenance=ep.provenance + (f"state_transition:{reason}",),
        )

    def _candidate_record_outcome(
        self, outcome: OutcomeObservation
    ) -> tuple[OutcomeObservation, bool]:
        if self._episodes.get(outcome.target_episode_id) is None:
            raise ValueError(
                f"cannot record outcome for unknown target episode: {outcome.target_episode_id}"
            )
        existing_id = self._outcome_dedupe.get(outcome.dedupe_key)
        if existing_id:
            return self._outcomes[existing_id], False
        return outcome, True

    def _publish_outcome(self, outcome: OutcomeObservation) -> None:
        self._outcomes[outcome.observation_id] = outcome
        self._outcome_dedupe[outcome.dedupe_key] = outcome.observation_id

    def create_episode(self, episode: Episode) -> Episode:
        candidate, is_new = self._candidate_create_episode(episode)
        if is_new:
            self._index_episode(candidate)
        return candidate

    def get_episode(self, episode_id: str) -> Episode | None:
        return self._episodes.get(episode_id)

    def append_event_ref(self, episode_id: str, ref: EpisodeEventRef) -> Episode:
        candidate, is_new = self._candidate_append_event_ref(episode_id, ref)
        if is_new:
            self._index_episode(candidate)
        return candidate

    def transition_state(
        self,
        episode_id: str,
        new_state: EpisodeState,
        *,
        reason: str,
        at: datetime | None = None,
        preserve_last_activity: bool = False,
    ) -> Episode:
        candidate = self._candidate_transition_state(
            episode_id, new_state, reason=reason, at=at,
            preserve_last_activity=preserve_last_activity,
        )
        self._index_episode(candidate)
        return candidate

    def find_active_by_scope(self, scope_id: str) -> tuple[Episode, ...]:
        return tuple(
            ep for ep in self._episodes.values()
            if ep.scope_id == scope_id and ep.episode_id in self._active_ids
        )

    def evict_from_active_index(self, episode_id: str) -> Episode | None:
        """Remove an Episode from the routing index only; never finalize it."""
        self._active_ids.discard(episode_id)
        return self._episodes.get(episode_id)

    def find_episode_by_event_ref(self, ref_id: str) -> Episode | None:
        episode_id = self._ref_index.get(ref_id)
        return self._episodes.get(episode_id) if episode_id else None

    def find_episode_by_trace_id(self, trace_id: str) -> Episode | None:
        episode_id = self._trace_index.get(trace_id)
        return self._episodes.get(episode_id) if episode_id else None

    def find_episode_by_source_event_id(self, source_event_id: str) -> Episode | None:
        episode_id = self._source_event_index.get(source_event_id)
        return self._episodes.get(episode_id) if episode_id else None

    def record_outcome(self, outcome: OutcomeObservation) -> OutcomeObservation:
        candidate, is_new = self._candidate_record_outcome(outcome)
        if is_new:
            self._publish_outcome(candidate)
        return candidate

    def get_outcomes(self, episode_id: str | None = None) -> tuple[OutcomeObservation, ...]:
        outcomes = list(self._outcomes.values())
        if episode_id is not None:
            outcomes = [o for o in outcomes if o.target_episode_id == episode_id]
        return tuple(sorted(outcomes, key=lambda o: o.observed_at))

    def all_episodes(self) -> tuple[Episode, ...]:
        return tuple(self._episodes.values())


class AppendOnlyEpisodeStore(InMemoryEpisodeStore):
    """Minimal append-only JSONL-backed EpisodeStore.

    The file stores full immutable snapshots as append records.  Replay uses the
    latest snapshot for each Episode and deduplicates outcomes by stable key.
    """

    SCHEMA_VERSION = 1

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self._journal_lock = RLock()
        self._poisoned: EpisodePersistenceError | None = None
        self._journal_position = 0
        self._outcome_positions: dict[str, int] = {}
        self._finalization_positions: dict[str, int] = {}
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._replay()
        self._ensure_newline_boundary()
        self._recover_open_episodes()

    def _ensure_newline_boundary(self) -> None:
        """Add a record terminator after a valid final JSON record without newline."""
        try:
            if not self._path.exists():
                return
            data = self._path.read_text(encoding="utf-8")
            if data and not data.endswith(("\n", "\r")):
                with self._path.open("a", encoding="utf-8") as f:
                    f.write("\n")
        except Exception:
            logger.exception("Failed to normalize Episode log newline boundary")

    def _ensure_writable(self) -> None:
        if self._poisoned is not None:
            raise EpisodePersistenceError("Episode journal is unavailable after a prior uncertain write") from self._poisoned

    def _fsync(self, file_handle: object) -> None:
        os.fsync(file_handle.fileno())  # type: ignore[union-attr]

    def _restore_offset(self, offset: int) -> bool:
        try:
            with self._path.open("r+b") as file_handle:
                file_handle.truncate(offset)
                file_handle.flush()
                self._fsync(file_handle)
            return True
        except Exception:
            logger.exception("Episode journal rollback failed at offset %d", offset)
            return False

    def _append_log(self, operation_kind: str, episode_id: str, payload: dict) -> None:
        """Append one authority record and return only after its durable commit."""
        self._ensure_writable()
        record = {
            "schema_version": self.SCHEMA_VERSION,
            "operation_id": uuid4().hex,
            "operation_kind": operation_kind,
            "recorded_at": datetime.now().astimezone().isoformat(),
            "episode_id": episode_id,
            "payload": payload,
        }
        serialized = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        offset = self._path.stat().st_size if self._path.exists() else 0
        try:
            with self._path.open("ab") as file_handle:
                file_handle.write(serialized)
                file_handle.flush()
                self._fsync(file_handle)
        except Exception as exc:
            if not self._restore_offset(offset):
                self._poisoned = EpisodePersistenceError("Episode journal rollback could not be proven")
            raise EpisodePersistenceError(
                f"Episode journal append failed for {operation_kind}"
            ) from exc

    def _publish_committed_episode(
        self, operation_kind: str, episode: Episode
    ) -> None:
        position = self._journal_position + 1
        self._index_episode(episode)
        self._journal_position = position
        if episode.state is EpisodeState.FINALIZED:
            self._finalization_positions.setdefault(episode.episode_id, position)

    def _publish_committed_outcome(self, outcome: OutcomeObservation) -> None:
        position = self._journal_position + 1
        self._publish_outcome(outcome)
        self._journal_position = position
        self._outcome_positions[outcome.observation_id] = position

    _EPISODE_SNAPSHOT_OPS: ClassVar[frozenset[str]] = frozenset({
        "EPISODE_CREATED",
        "EVENT_ATTACHED",
        "STATE_TRANSITIONED",
        "RECOVERY_TRANSITIONED",
    })

    @staticmethod
    def _is_recoverable_truncated_json_fragment(fragment: str, exc: json.JSONDecodeError) -> bool:
        """Conservative classifier for a possible interrupted final JSON write.

        Recovery is allowed only when the stdlib parser stopped at EOF because
        the document is incomplete, or when the only defect is an unterminated
        string reaching EOF.  Syntax errors before EOF are treated as corruption.
        """
        if not fragment.lstrip().startswith("{"):
            return False
        # JSONDecodeError.pos is relative to the string passed to json.loads.
        if exc.pos == len(fragment):
            return True
        # Python stdlib reports the opening quote position for unterminated
        # strings; EOF position alone is therefore not sufficient for this case.
        return exc.msg == "Unterminated string starting at"

    def _replay(self) -> None:
        if not self._path.exists():
            return
        try:
            data = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        if not data:
            return

        # Keep raw physical fragments so we can preserve exact valid history and
        # truncate only a confirmed incomplete trailing write.
        physical_lines = data.splitlines(keepends=True)
        non_empty_indices = [i for i, line in enumerate(physical_lines) if line.strip()]
        if not non_empty_indices:
            return

        valid_raw: list[str] = []
        recovered_truncated_tail = False
        for position, index in enumerate(non_empty_indices):
            raw_line = physical_lines[index]
            line = raw_line.strip()
            line_no = index + 1
            is_last_non_empty = position == len(non_empty_indices) - 1
            has_newline = raw_line.endswith(("\n", "\r"))

            try:
                record = json.loads(line)
                self._apply_replay_record(record, line_no, position + 1)
                valid_raw.append(raw_line)
            except EpisodeLogCorruptionError:
                raise
            except json.JSONDecodeError as exc:
                if (
                    is_last_non_empty
                    and not has_newline
                    and self._is_recoverable_truncated_json_fragment(line, exc)
                ):
                    recovered_truncated_tail = True
                    logger.warning(
                        "Recovering truncated trailing Episode log at %s:%d: %s",
                        self._path,
                        line_no,
                        exc,
                    )
                    break
                remaining = [l for l in physical_lines[index + 1:] if l.strip()]
                if remaining:
                    raise EpisodeLogCorruptionError(
                        f"mid-log corruption at {self._path}:{line_no}: {exc}"
                    ) from exc
                raise EpisodeLogCorruptionError(
                    f"invalid complete record at {self._path}:{line_no}: {exc}"
                ) from exc
            except Exception as exc:
                remaining = [l for l in physical_lines[index + 1:] if l.strip()]
                if remaining:
                    raise EpisodeLogCorruptionError(
                        f"mid-log corruption at {self._path}:{line_no}: {exc}"
                    ) from exc
                raise EpisodeLogCorruptionError(
                    f"invalid complete record at {self._path}:{line_no}: {exc}"
                ) from exc

        if recovered_truncated_tail:
            # Remove the incomplete tail so later appends start from a clean
            # record boundary.  Prior valid history is preserved byte-for-byte.
            self._path.write_text("".join(valid_raw), encoding="utf-8")
            logger.info("Truncated incomplete Episode tail at %s", self._path)

    def _apply_replay_record(self, record: dict, line_no: int, journal_position: int) -> None:
        if not isinstance(record, dict):
            raise EpisodeLogCorruptionError(
                f"replay record at line {line_no} is not an object"
            )
        schema_version = record.get("schema_version")
        if schema_version != self.SCHEMA_VERSION:
            raise EpisodeLogCorruptionError(
                f"unsupported schema_version at line {line_no}: {schema_version!r}"
            )
        op = record.get("operation_kind")
        payload = record.get("payload")
        if not isinstance(payload, dict):
            raise EpisodeLogCorruptionError(
                f"missing payload for {op!r} at line {line_no}"
            )
        if op in self._EPISODE_SNAPSHOT_OPS:
            ep = _episode_from_dict(payload)
            self._index_episode(ep)
            if ep.state is EpisodeState.FINALIZED:
                self._finalization_positions.setdefault(ep.episode_id, journal_position)
            self._journal_position = journal_position
            return
        if op == "OUTCOME_RECORDED":
            outcome = _outcome_from_dict(payload)
            if self._episodes.get(outcome.target_episode_id) is None:
                raise EpisodeLogCorruptionError(
                    f"orphan outcome at line {line_no}: {outcome.target_episode_id}"
                )
            self._outcomes[outcome.observation_id] = outcome
            self._outcome_dedupe[outcome.dedupe_key] = outcome.observation_id
            self._outcome_positions[outcome.observation_id] = journal_position
            self._journal_position = journal_position
            return
        raise EpisodeLogCorruptionError(
            f"unknown operation_kind at line {line_no}: {op!r}"
        )

    def _recover_open_episodes(self) -> None:
        for ep in list(self._episodes.values()):
            if ep.state is EpisodeState.OPEN:
                self.transition_state(
                    ep.episode_id,
                    EpisodeState.INTERRUPTED,
                    reason="recovery_restart",
                    preserve_last_activity=True,
                )

    def create_episode(self, episode: Episode) -> Episode:
        with self._journal_lock:
            self._ensure_writable()
            candidate, is_new = self._candidate_create_episode(episode)
            if not is_new:
                return candidate
            self._append_log("EPISODE_CREATED", candidate.episode_id, _episode_to_dict(candidate))
            try:
                self._publish_committed_episode("EPISODE_CREATED", candidate)
            except Exception as exc:
                self._poisoned = EpisodePersistenceError("Episode journal committed but in-memory publish failed")
                raise EpisodePersistenceError("Episode creation committed but could not be published") from exc
            return candidate

    def append_event_ref(self, episode_id: str, ref: EpisodeEventRef) -> Episode:
        with self._journal_lock:
            self._ensure_writable()
            candidate, is_new = self._candidate_append_event_ref(episode_id, ref)
            if not is_new:
                return candidate
            self._append_log("EVENT_ATTACHED", candidate.episode_id, _episode_to_dict(candidate))
            try:
                self._publish_committed_episode("EVENT_ATTACHED", candidate)
            except Exception as exc:
                self._poisoned = EpisodePersistenceError("Episode journal committed but in-memory publish failed")
                raise EpisodePersistenceError("Episode event committed but could not be published") from exc
            return candidate

    def transition_state(
        self,
        episode_id: str,
        new_state: EpisodeState,
        *,
        reason: str,
        at: datetime | None = None,
        preserve_last_activity: bool = False,
    ) -> Episode:
        with self._journal_lock:
            self._ensure_writable()
            candidate = self._candidate_transition_state(
                episode_id, new_state, reason=reason, at=at,
                preserve_last_activity=preserve_last_activity,
            )
            self._append_log("STATE_TRANSITIONED", candidate.episode_id, _episode_to_dict(candidate))
            try:
                self._publish_committed_episode("STATE_TRANSITIONED", candidate)
            except Exception as exc:
                self._poisoned = EpisodePersistenceError("Episode journal committed but in-memory publish failed")
                raise EpisodePersistenceError("Episode transition committed but could not be published") from exc
            return candidate

    def record_outcome(self, outcome: OutcomeObservation) -> OutcomeObservation:
        with self._journal_lock:
            self._ensure_writable()
            candidate, is_new = self._candidate_record_outcome(outcome)
            if not is_new:
                return candidate
            self._append_log("OUTCOME_RECORDED", candidate.target_episode_id, _outcome_to_dict(candidate))
            try:
                self._publish_committed_outcome(candidate)
            except Exception as exc:
                self._poisoned = EpisodePersistenceError("Episode journal committed but in-memory publish failed")
                raise EpisodePersistenceError("Outcome committed but could not be published") from exc
            return candidate

    def get_finalization_boundary(self, episode_id: str) -> int | None:
        """Return the first durable FINALIZED journal position for an Episode."""
        with self._journal_lock:
            return self._finalization_positions.get(episode_id)

    def get_finalized_outcomes(self, episode_id: str) -> tuple[OutcomeObservation, ...]:
        """Return Outcomes committed strictly before the durable FINALIZED boundary."""
        with self._journal_lock:
            boundary = self._finalization_positions.get(episode_id)
            if boundary is None:
                return ()
            return tuple(
                outcome
                for outcome_id, outcome in self._outcomes.items()
                if outcome.target_episode_id == episode_id
                and self._outcome_positions.get(outcome_id, boundary) < boundary
            )
