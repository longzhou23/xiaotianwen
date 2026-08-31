from __future__ import annotations

from astrbot_plugin_xiaotianwen_orchestrator.p2 import (
    ProactiveInput,
    ProactivePolicy,
    StatusNoticeCoordinator,
)


def _candidate(**overrides: object) -> ProactiveInput:
    values: dict[str, object] = {
        "session_id": "group:g-1",
        "scope": "group",
        "enabled": True,
        "allowlisted": True,
        "user_activity_count": 3,
        "min_user_activity": 3,
        "silence_seconds": 700,
        "min_silence_seconds": 600,
        "cooldown_seconds": 0,
        "probability": 0.5,
        "draw": 0.2,
    }
    values.update(overrides)
    return ProactiveInput(**values)  # type: ignore[arg-type]


def test_proactive_policy_is_shared_by_scope_and_does_not_create_virtual_events() -> None:
    policy = ProactivePolicy()
    assert policy.evaluate(_candidate()).triggered
    assert policy.evaluate(_candidate(scope="private", session_id="private:u-1")).triggered
    assert policy.evaluate(_candidate(quiet_time=True)).reason_code == "quiet_time"
    assert policy.evaluate(_candidate(cooldown_seconds=1)).reason_code == "cooldown"
    assert policy.evaluate(_candidate(draw=0.8)).reason_code == "probability_miss"


def test_status_notice_uses_request_id_and_retracts_on_cancel() -> None:
    coordinator = StatusNoticeCoordinator(notice_after_seconds=3)
    coordinator.start("request-status-1", route="vision", started_at=10)
    assert coordinator.maybe_show("request-status-1", now=12) is None
    shown = coordinator.maybe_show("request-status-1", now=13)
    assert shown is not None
    assert shown.action == "show"
    assert shown.request_id == "request-status-1"
    assert coordinator.maybe_show("request-status-1", now=14) is None
    final = coordinator.finish("request-status-1", now=15, cancelled=True)
    assert final is not None
    assert final.action == "retract"
    assert final.status == "cancelled"
