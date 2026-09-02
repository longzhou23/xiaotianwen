from datetime import datetime, timezone

import pytest

from iris_memory.cognitive.contracts import (
    CanonicalExperience,
    CognitiveContractError,
    EntityReference,
    ExitReason,
    GroundingResult,
    GroundingStatus,
    IdentityClaim,
    IdentityClaimStatus,
    Intent,
    Perspective,
    ResolvedEvent,
    SocialAction,
    TriggerDecision,
    deep_freeze,
)


def _event() -> ResolvedEvent:
    return ResolvedEvent(
        event_id="qq:42",
        source="qq",
        occurred_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
        session_id="group:1",
        mode="casual_group_chat",
        content="测试",
        actor=EntityReference("person:qq:10001", "platform_uid", 1.0, ("qq:10001",)),
    )


def test_contracts_require_stable_entity_ids_and_identity_evidence():
    with pytest.raises(CognitiveContractError):
        EntityReference("longz", "alias", 1.0)

    claim = IdentityClaim(
        mention="longz",
        candidate_entity="person:qq:10001",
        evidence=("qq UID 10001",),
        confidence=1.0,
        source="platform_uid",
        status=IdentityClaimStatus.CONFIRMED,
    )

    assert claim.candidate_entity == "person:qq:10001"
    assert claim.status is IdentityClaimStatus.CONFIRMED


def test_canonical_experience_requires_provenance():
    with pytest.raises(CognitiveContractError):
        CanonicalExperience(
            id="experience:qq:42",
            event=_event(),
            subject=None,
            perspective=Perspective.UNRESOLVED,
            provenance=(),
        )


def test_trigger_contract_is_fail_closed_and_exit_reason_is_specific():
    decision = TriggerDecision(
        should_start_loop=False,
        score=-2,
        reason="ordinary group message has no deterministic activation signal",
        exit_reason=ExitReason.TRIGGER_NO,
    )

    assert decision.exit_reason is ExitReason.TRIGGER_NO

    with pytest.raises(CognitiveContractError):
        TriggerDecision(
            should_start_loop=False,
            score=0,
            reason="bad exit",
            exit_reason=ExitReason.NO_INTENT,
        )


def test_owner_boundary_mappings_are_deep_immutable_and_detached_from_inputs():
    raw = {"nested": {"values": ["before"]}}
    event = ResolvedEvent(
        event_id="qq:immutable",
        source="qq",
        occurred_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
        session_id="group:immutable",
        mode="casual_group_chat",
        content="测试",
        actor=EntityReference("person:qq:10001", "platform_uid", 1.0),
        raw_metadata=raw,
    )
    raw["nested"]["values"].append("after")

    assert event.raw_metadata["nested"]["values"] == ("before",)
    with pytest.raises(TypeError):
        event.raw_metadata["nested"]["new"] = True
    with pytest.raises(AttributeError):
        event.raw_metadata["nested"]["values"].append("blocked")


def test_tuple_annotated_contract_fields_canonicalize_lists_and_detach_source():
    basis = ["ev1"]
    intent = Intent(SocialAction.INFORM, None, "reason", basis, 0.8)
    assert intent.basis == ("ev1",)
    basis.append("ev2")
    assert intent.basis == ("ev1",)
    with pytest.raises(AttributeError):
        intent.basis.append("blocked")

    allowed = ["allow"]
    blocked = ["block"]
    grounding = GroundingResult(
        semantic_requirement="req",
        status=GroundingStatus.SUFFICIENT,
        basis=["b"],
        allowed_claims=allowed,
        blocked_claims=blocked,
        required_tool=None,
        confidence=1.0,
    )
    assert grounding.basis == ("b",)
    assert grounding.allowed_claims == ("allow",)
    assert grounding.blocked_claims == ("block",)
    allowed.append("mutated")
    assert grounding.allowed_claims == ("allow",)
    with pytest.raises(AttributeError):
        grounding.blocked_claims.append("mutated")

    ref = EntityReference("person:qq:10001", "platform_uid", 1.0, ["ev"])
    assert ref.evidence == ("ev",)
    with pytest.raises(AttributeError):
        ref.evidence.append("mutated")


def test_nested_set_tuple_and_mapping_values_are_frozen():
    nested_source = {"values": [1, {"inner": ["x"]}], "tags": {"a", "b"}}
    frozen = deep_freeze(nested_source)
    assert isinstance(frozen["tags"], frozenset)
    assert frozen["values"][1]["inner"] == ("x",)
    with pytest.raises(TypeError):
        frozen["values"][1]["new"] = True
    with pytest.raises(AttributeError):
        frozen["values"][1]["inner"].append("y")
