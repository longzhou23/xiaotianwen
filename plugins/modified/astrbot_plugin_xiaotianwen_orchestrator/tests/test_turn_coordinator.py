from __future__ import annotations

import json

import pytest

from astrbot_plugin_xiaotianwen_orchestrator.compatibility import (
    StructuralObservationStore,
    compare_shadow_turn,
)
from astrbot_plugin_xiaotianwen_orchestrator.contracts import ContractValidationError
from astrbot_plugin_xiaotianwen_orchestrator.ingress import (
    ShadowTurnCoordinator,
    TurnState,
    event_to_envelope,
)


def _event(
    message_id: str,
    text: str,
    *,
    image_id: str | None = None,
    sender_id: str = "10001",
) -> dict[str, object]:
    segments: list[dict[str, object]] = [{"type": "text", "data": {"text": text}}]
    if image_id:
        segments.insert(
            0,
            {
                "type": "image",
                "data": {"image_id": image_id, "file": f"/tmp/{image_id}.jpg"},
            },
        )
    return {
        "message_id": message_id,
        "group_id": "42",
        "user_id": sender_id,
        "raw_message": text,
        "message": segments,
        "trigger": "message",
    }


def test_event_normalization_keeps_image_and_text_in_one_turn() -> None:
    turn = event_to_envelope(_event("m-1", "看看这个", image_id="img-a"), received_at=10)

    assert turn.session_id == "group:42"
    assert turn.text == "看看这个"
    assert len(turn.media) == 1
    assert turn.media[0].media_id == "img-a"
    assert turn.media[0].order == 0


def test_event_normalization_uses_stable_request_id_for_same_event() -> None:
    event = _event("m-deterministic", "稳定请求标识", image_id="img-deterministic")

    first = event_to_envelope(event, received_at=10)
    second = event_to_envelope(event, received_at=99)

    assert first.request_id == second.request_id


def test_inline_image_payload_is_hashed_but_not_retained_as_a_path() -> None:
    event = _event("m-inline", "看看", image_id="img-inline")
    segments = event["message"]
    assert isinstance(segments, list)
    image_segment = segments[0]
    assert isinstance(image_segment, dict)
    image_segment["data"] = {"image_id": "img-inline", "file": "base64://very-large-payload"}

    turn = event_to_envelope(event, received_at=10)

    assert turn.media[0].local_path is None
    assert turn.media[0].content_hash is not None


def test_three_second_quiet_window_merges_trailing_text_and_deduplicates_event() -> None:
    coordinator = ShadowTurnCoordinator(quiet_window_seconds=3, dedup_ttl_seconds=30)

    first = coordinator.ingest_event(_event("m-1", "这张图" , image_id="img-a"), now=0)
    duplicate = coordinator.ingest_event(_event("m-1", "这张图", image_id="img-a"), now=1)
    merged = coordinator.ingest_event(_event("m-2", "帮我标注一下"), now=2)

    assert first.action == "created"
    assert duplicate.action == "duplicate"
    assert merged.action == "merged"
    assert merged.request_id == first.request_id
    assert merged.snapshot is not None
    assert merged.snapshot.turn.text == "这张图\n帮我标注一下"
    assert [media.media_id for media in merged.snapshot.turn.media] == ["img-a"]
    assert coordinator.flush_ready(now=4.99) == ()

    ready = coordinator.flush_ready(now=5)

    assert len(ready) == 1
    assert ready[0].state is TurnState.READY
    assert ready[0].merge_count == 1
    assert coordinator.active_timer_count == 0


def test_missing_platform_message_id_uses_stable_fallback_fingerprint() -> None:
    coordinator = ShadowTurnCoordinator()
    event = _event("placeholder", "没有 message id 的事件")
    event.pop("message_id")

    first = coordinator.ingest_event(event, now=0)
    duplicate = coordinator.ingest_event(event, now=1)

    assert first.action == "created"
    assert duplicate.action == "duplicate"


def test_new_message_after_requesting_is_recorded_as_cancelled_without_cancel_call() -> None:
    coordinator = ShadowTurnCoordinator(quiet_window_seconds=3)
    first = coordinator.ingest_event(_event("m-1", "第一条"), now=0)
    coordinator.flush_ready(now=3)
    coordinator.mark_stage(first.request_id or "", TurnState.REQUESTING)

    replacement = coordinator.ingest_event(_event("m-2", "第二条"), now=3.1)

    assert replacement.action == "created"
    assert replacement.request_id != first.request_id
    cancelled = coordinator.terminal_turns[-1]
    assert cancelled.state is TurnState.CANCELLED
    assert cancelled.cancellation is not None
    assert cancelled.cancellation.reason == "new_message_after_quiet_window"


def test_legacy_comparison_is_structural_and_never_contains_message_body() -> None:
    coordinator = ShadowTurnCoordinator()
    result = coordinator.ingest_event(_event("m-secret", "不要写入日志的原文"), now=0)
    assert result.snapshot is not None

    diff = compare_shadow_turn(
        result.snapshot,
        {
            "session_id": "group:42",
            "message_ids": ["m-secret"],
            "creates_primary_reply": True,
        },
    )

    store = StructuralObservationStore(max_entries=1)
    observation = store.record("turn_comparison", diff.to_dict(), at=1)
    rendered = json.dumps(observation.to_dict(), ensure_ascii=False)
    assert diff.matches is True
    assert "不要写入日志的原文" not in rendered
    assert len(store.snapshot()) == 1


def test_disable_clears_dedup_and_pending_state_without_timer() -> None:
    coordinator = ShadowTurnCoordinator()
    coordinator.ingest_event(_event("m-1", "hello"), now=0)

    coordinator.disable()

    assert coordinator.pending_count == 0
    assert coordinator.dedup_cache_size == 0
    assert coordinator.active_timer_count == 0
    disabled = coordinator.ingest_event(_event("m-2", "ignored"), now=1)
    assert disabled.action == "disabled"


def test_legacy_observation_rejects_non_structural_values() -> None:
    coordinator = ShadowTurnCoordinator()
    result = coordinator.ingest_event(_event("m-1", "hello"), now=0)
    assert result.snapshot is not None

    with pytest.raises(ContractValidationError, match="boolean"):
        compare_shadow_turn(
            result.snapshot,
            {
                "session_id": "group:42",
                "message_ids": ["m-1"],
                "creates_primary_reply": "true",
            },
        )
