from __future__ import annotations

from astrbot_plugin_xiaotianwen_orchestrator.contracts import TurnEnvelope
from astrbot_plugin_xiaotianwen_orchestrator.ingress import (
    CanaryPolicy,
    OrchestratorMode,
    PrimaryReplyOwnership,
)
from astrbot_plugin_xiaotianwen_orchestrator.output import (
    DeliveryCoordinator,
    DeliveryStatus,
)


def _turn(index: int, *, session_id: str = "group:test-group") -> TurnEnvelope:
    return TurnEnvelope(
        request_id=f"request-{index}",
        session_id=session_id,
        route="chat",
        trigger="message",
        sender_id="synthetic-user",
        text=f"message {index}",
        reply_to=None,
        media=(),
        received_at=float(index),
        batch_started_at=float(index),
    )


def test_modes_and_canary_allowlist_never_create_two_owners() -> None:
    policy = CanaryPolicy.from_values(private_ids=["test-user"], group_ids=["test-group"])
    ownership = PrimaryReplyOwnership(OrchestratorMode.CANARY, canary=policy)
    turn = _turn(1)

    first = ownership.decide(turn, "event-1")
    duplicate = ownership.decide(turn, "event-1")
    legacy = ownership.decide(_turn(2, session_id="group:not-allowed"), "event-2")

    assert first == duplicate
    assert first.should_dispatch is True
    assert first.owner == PrimaryReplyOwnership.ORCHESTRATOR
    assert legacy.should_dispatch is False
    assert legacy.owner == PrimaryReplyOwnership.LEGACY


def test_session_fallback_returns_future_events_to_legacy() -> None:
    ownership = PrimaryReplyOwnership("active")
    assert ownership.decide(_turn(1), "event-1").should_dispatch is True

    ownership.fallback_session("group:test-group")
    decision = ownership.decide(_turn(2), "event-2")

    assert decision.owner == PrimaryReplyOwnership.LEGACY
    assert decision.should_dispatch is False


def test_one_hundred_events_have_one_stable_owner_and_no_duplicate_request() -> None:
    ownership = PrimaryReplyOwnership("active", max_events=120)
    decisions = [ownership.decide(_turn(index), f"event-{index}") for index in range(100)]

    assert len(decisions) == 100
    assert ownership.event_count == 100
    assert all(item.owner == PrimaryReplyOwnership.ORCHESTRATOR for item in decisions)
    assert len({item.request_id for item in decisions}) == 100


def test_delivery_requires_owner_and_audit_then_suppresses_duplicates_and_late_output() -> None:
    delivery = DeliveryCoordinator()
    assert delivery.claim_owner("request-1", "orchestrator") is True
    assert delivery.claim_owner("request-1", "legacy") is False

    before_audit = delivery.attempt(
        "request-1",
        owner="orchestrator",
        delivery_id="text-1",
    )
    delivery.mark_audit_passed("request-1")
    allowed = delivery.attempt("request-1", owner="orchestrator", delivery_id="text-1")
    duplicate = delivery.attempt("request-1", owner="orchestrator", delivery_id="text-1")
    delivery.cancel("request-1")
    late = delivery.attempt("request-1", owner="orchestrator", delivery_id="text-2")

    assert before_audit.status is DeliveryStatus.AUDIT_REQUIRED
    assert allowed.status is DeliveryStatus.ALLOWED
    assert duplicate.status is DeliveryStatus.DUPLICATE
    assert late.status is DeliveryStatus.CANCELLED
    assert delivery.send_count("request-1") == 1
