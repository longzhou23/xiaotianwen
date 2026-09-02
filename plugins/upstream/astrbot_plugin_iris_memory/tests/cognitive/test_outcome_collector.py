"""OutcomeCollector deterministic observation tests."""

from __future__ import annotations

from datetime import datetime, timezone

from iris_memory.cognitive.contracts import (
    CanonicalExperience,
    EntityReference,
    Perspective,
    ResolvedEvent,
)
from iris_memory.cognitive.outcome import OutcomeKind
from iris_memory.cognitive.outcome_collector import OutcomeCollector


_NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
_USER = EntityReference("person:qq:1", "platform_uid", 1.0, ("qq:1",))


def _exp(content: str, *, reply_to=None, mentioned=()) -> CanonicalExperience:
    event = ResolvedEvent(
        event_id="qq:1",
        source="qq",
        occurred_at=_NOW,
        session_id="g1",
        mode="private",
        content=content,
        actor=_USER,
        mentioned_entities=mentioned,
        reply_to=reply_to,
    )
    return CanonicalExperience(
        id="experience:qq:1",
        event=event,
        subject=_USER,
        perspective=Perspective.INTERPERSONAL,
        provenance=("test",),
    )


def test_thanks_is_acknowledgement_not_approval():
    collector = OutcomeCollector()
    obs = collector.classify_experience(_exp("谢谢"), target_episode_id="ep:1")
    kinds = {o.kind for o in obs}
    assert OutcomeKind.EXPLICIT_ACKNOWLEDGEMENT in kinds
    assert not any(k.value == "USER_APPROVED" for k in kinds)
    assert not any(hasattr(o, "reward") or hasattr(o, "score") for o in obs)


def test_wrong_is_correction_not_negative_reward():
    collector = OutcomeCollector()
    obs = collector.classify_experience(_exp("不对"), target_episode_id="ep:1")
    assert any(o.kind is OutcomeKind.EXPLICIT_CORRECTION for o in obs)


def test_stop_is_stop_request():
    collector = OutcomeCollector()
    obs = collector.classify_experience(_exp("别说了"), target_episode_id="ep:1")
    assert any(o.kind is OutcomeKind.EXPLICIT_STOP_REQUEST for o in obs)


def test_question_is_followup_question():
    collector = OutcomeCollector()
    obs = collector.classify_experience(_exp("那这个怎么安装？"), target_episode_id="ep:1")
    assert any(o.kind is OutcomeKind.FOLLOWUP_QUESTION for o in obs)


def test_window_elapsed_is_absence_not_negative():
    collector = OutcomeCollector()
    obs = collector.observation_window_elapsed(
        target_episode_id="ep:1",
        observed_at=_NOW,
    )
    assert obs.kind is OutcomeKind.OBSERVATION_WINDOW_ELAPSED
    assert obs.explicitness.value == "ABSENCE"
