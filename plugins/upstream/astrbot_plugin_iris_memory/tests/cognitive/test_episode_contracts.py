"""P1b Episode/Outcome contract tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from iris_memory.cognitive.episode import (
    BoundaryAction,
    Episode,
    EpisodeBoundaryDecision,
    EpisodeEventKind,
    EpisodeEventRef,
    EpisodeState,
    make_episode_event_ref_id,
    make_episode_id,
)
from iris_memory.cognitive.episode_store import InMemoryEpisodeStore
from iris_memory.cognitive.outcome import (
    OutcomeExplicitness,
    OutcomeKind,
    OutcomeObservation,
    make_outcome_observation_id,
)
from iris_memory.cognitive.contracts import EntityReference


_NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
_ACTOR = EntityReference("person:qq:1", "platform_uid", 1.0, ("qq:1",))


def _episode() -> Episode:
    return Episode(
        episode_id=make_episode_id("g1", "qq:1"),
        scope_id="g1",
        state=EpisodeState.OPEN,
        root_event_id="qq:1",
        opened_at=_NOW,
        last_activity_at=_NOW,
        participants=(_ACTOR,),
        provenance=("test",),
    )


def test_episode_has_no_reward_score_or_quality_fields():
    ep = _episode()
    for forbidden in ("reward", "score", "quality", "review", "learning"):
        assert not hasattr(ep, forbidden)


def test_outcome_has_no_reward_quality_or_sentiment_fields():
    outcome = OutcomeObservation(
        observation_id=make_outcome_observation_id(
            _episode().episode_id,
            OutcomeKind.EXPLICIT_ACKNOWLEDGEMENT,
            source_event_id="qq:2",
        ),
        target_episode_id=_episode().episode_id,
        kind=OutcomeKind.EXPLICIT_ACKNOWLEDGEMENT,
        observed_at=_NOW,
        source_event_id="qq:2",
        explicitness=OutcomeExplicitness.EXPLICIT,
    )
    for forbidden in ("reward", "score", "quality", "sentiment", "positive", "negative"):
        assert not hasattr(outcome, forbidden)


def test_outcome_source_event_id_may_be_none():
    outcome = OutcomeObservation(
        observation_id=make_outcome_observation_id(_episode().episode_id, OutcomeKind.OBSERVATION_WINDOW_ELAPSED),
        target_episode_id=_episode().episode_id,
        kind=OutcomeKind.OBSERVATION_WINDOW_ELAPSED,
        observed_at=_NOW,
        source_event_id=None,
        explicitness=OutcomeExplicitness.ABSENCE,
    )
    assert outcome.source_event_id is None


def test_episode_event_ref_deterministic_id_and_immutability():
    ref = EpisodeEventRef(
        ref_id=make_episode_event_ref_id(
            EpisodeEventKind.EXPERIENCE,
            source_event_id="qq:1",
        ),
        kind=EpisodeEventKind.EXPERIENCE,
        source_event_id="qq:1",
        observed_at=_NOW,
        actor_entity=_ACTOR,
    )
    same = EpisodeEventRef(
        ref_id=make_episode_event_ref_id(
            EpisodeEventKind.EXPERIENCE,
            source_event_id="qq:1",
        ),
        kind=EpisodeEventKind.EXPERIENCE,
        source_event_id="qq:1",
        observed_at=_NOW,
        actor_entity=_ACTOR,
    )
    assert ref.ref_id == same.ref_id
    with pytest.raises(Exception):
        ref.observed_at = _NOW  # frozen


def test_finalized_is_terminal_and_revision_monotonic():
    store = InMemoryEpisodeStore()
    ep = store.create_episode(_episode())
    ep = store.transition_state(ep.episode_id, EpisodeState.SOFT_CLOSED, reason="idle")
    ep = store.transition_state(ep.episode_id, EpisodeState.FINALIZED, reason="grace")
    assert ep.revision == 2
    with pytest.raises(ValueError):
        store.transition_state(ep.episode_id, EpisodeState.OPEN, reason="late")
    with pytest.raises(ValueError):
        store.append_event_ref(
            ep.episode_id,
            EpisodeEventRef(
                ref_id="experience:late",
                kind=EpisodeEventKind.EXPERIENCE,
                source_event_id="qq:late",
                observed_at=_NOW,
            ),
        )


def test_old_snapshot_unchanged_after_append():
    store = InMemoryEpisodeStore()
    ep = store.create_episode(_episode())
    old = ep
    ref = EpisodeEventRef(
        ref_id=make_episode_event_ref_id(
            EpisodeEventKind.COGNITIVE_PROPOSAL,
            source_event_id="qq:1",
            trace_id="trace:1",
        ),
        kind=EpisodeEventKind.COGNITIVE_PROPOSAL,
        source_event_id="qq:1",
        trace_id="trace:1",
        observed_at=_NOW,
        actor_entity=_ACTOR,
    )
    new_ep = store.append_event_ref(ep.episode_id, ref)
    assert old.event_refs == ()
    assert new_ep.event_refs == (ref,)
    assert new_ep.revision == old.revision + 1
