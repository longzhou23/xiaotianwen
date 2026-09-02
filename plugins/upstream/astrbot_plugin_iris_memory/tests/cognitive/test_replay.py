import json
from datetime import datetime, timezone
from pathlib import Path

from iris_memory.cognitive.contracts import EntityReference, ResolvedEvent
from iris_memory.cognitive.iris_adapter import CognitiveRuntime
from iris_memory.cognitive.situation import SituationBuilder
from iris_memory.cognitive.trigger import TriggerController


_FIXTURE = Path(__file__).parent / "fixtures" / "legacy_memory_replay.json"


def _event(*, mode: str = "casual_group_chat", mentioned=(), reply_to=None) -> ResolvedEvent:
    return ResolvedEvent(
        event_id="qq:fixture-1",
        source="qq",
        occurred_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
        session_id="group:fixture",
        mode=mode,
        content="今晚的观测还继续吗？",
        actor=EntityReference("person:qq:10001", "platform_uid", 1.0, ("qq:10001",)),
        mentioned_entities=mentioned,
        reply_to=reply_to,
    )


def test_sanitized_historical_memory_replay_preserves_raw_and_projects_self():
    runtime = CognitiveRuntime()
    cases = json.loads(_FIXTURE.read_text(encoding="utf-8"))["cases"]

    for case in cases:
        view = runtime.post_adapter.project_memory(
            memory_id=case["memory_id"],
            content=case["content"],
            metadata=case["metadata"],
        )
        assert view.raw_content == case["content"]
        assert view.perspective.value == case["expected_perspective"]
        assert view.content == case["expected_content"]


def test_minimal_situation_and_trigger_replay_are_deterministic_and_fail_closed():
    runtime = CognitiveRuntime()
    trigger = TriggerController(self_entity=runtime.identity.self_entity)
    situation = SituationBuilder().build(_event())

    assert situation.shared_focus_type == "message"
    assert situation.active_entities == ("person:qq:10001",)
    assert trigger.evaluate(_event()).exit_reason.value == "TRIGGER_NO"

    self_ref = EntityReference("agent:xiaotianwen", "self_binding", 1.0, ("fixed SELF",))
    mentioned = trigger.evaluate(_event(mentioned=(self_ref,)))
    private = trigger.evaluate(_event(mode="private"))
    reply = trigger.evaluate(_event(reply_to=self_ref))
    assert mentioned.should_start_loop and mentioned.exit_reason is None
    assert private.should_start_loop and private.exit_reason is None
    assert reply.should_start_loop and reply.exit_reason is None
