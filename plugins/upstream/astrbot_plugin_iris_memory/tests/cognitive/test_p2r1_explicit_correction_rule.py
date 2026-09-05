from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from iris_memory.cognitive.contracts import (
    BehaviorExecutionRecord,
    BehaviorTrace,
    DivergenceType,
    GroundingEnforcement,
    HostResult,
    OutputProducer,
    OutputState,
    ResolvedEvent,
    RuntimeMode,
    ShadowComparison,
    TraceStage,
    TriggerDecision,
)
from iris_memory.cognitive.episode import (
    Episode,
    EpisodeEventKind,
    EpisodeEventRef,
    EpisodeState,
)
from iris_memory.cognitive.explicit_correction_rule import (
    RULE_ID,
    RULE_STATEMENT,
    build_explicit_correction_candidate,
)
from iris_memory.cognitive.inbound_semantic_authority import (
    InboundSemanticActAuthorityServiceV1,
    InboundSemanticActAuthorityStoreV1,
    InboundSemanticEvaluatorProfileV1,
)
from iris_memory.cognitive.outcome import (
    OutcomeExplicitness,
    OutcomeKind,
    OutcomeObservation,
)
from iris_memory.cognitive.promotion_infrastructure import (
    P2PromotionStore,
    ProductionPromotionGateV1,
    PromotionCommand,
)
from iris_memory.cognitive.reply_link_archive import P2r0HistoricalArchiveService
from iris_memory.cognitive.reply_link_authority import (
    HostOutputMessageIdentityFactV1,
    InboundReplyReferenceFactV1,
    P2r0Store,
    PlatformMessageIdentityV1,
)
from iris_memory.cognitive.review import (
    AttributionRef,
    AttributionTargetType,
    CausalAttribution,
    Confidence,
    EvidenceKind,
    EvidenceSourceType,
    FindingType,
    InterpretationProducer,
    ReviewDimension,
    ReviewEvidenceRef,
    ReviewFinding,
    ReviewRun,
    ReviewStatus,
)
from iris_memory.cognitive.review_service import (
    ReviewInputSnapshot,
    compute_input_snapshot_hash,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class _Evaluator:
    def __init__(self, decision: str):
        self.decision = decision
        self.calls = 0

    def evaluate(self, _value: object) -> dict[str, str]:
        self.calls += 1
        return {"decision": self.decision}


def _profile() -> InboundSemanticEvaluatorProfileV1:
    return InboundSemanticEvaluatorProfileV1(
        profile_id="fixture.explicit-correction",
        profile_version="1",
        evaluator_kind="TEST_FIXTURE",
        provider="fixture",
        model="fixture",
        model_version="1",
        prompt_template_hash="fixture-prompt-v1",
    )


def _fixture(
    tmp_path: Path,
    *,
    decision: str = "MATCH",
    claim: str = "User explicitly contradicted the Host output.",
    target_episode_id: str | None = None,
    source_event_id: str | None = "event:inbound",
    reply_target: PlatformMessageIdentityV1 | None = None,
    extra_finding_outcome: bool = False,
    semantic_source_event_id: str | None = None,
    explicitness: OutcomeExplicitness = OutcomeExplicitness.EXPLICIT,
):
    trace = BehaviorTrace(
        event_id="event:host",
        trigger=TriggerDecision(True, "test", 1),
        participation=None,
        intent=None,
        grounding=None,
        exit_reason=None,
        runtime_mode=RuntimeMode.SHADOW,
        created_at=NOW,
    )
    object.__setattr__(trace, "trace_id", "trace:host")
    record = BehaviorExecutionRecord(
        trace=trace,
        host_result=HostResult(
            True,
            True,
            True,
            True,
            OutputState.OUTPUT_READY,
            OutputProducer.LEGACY_HOST,
            GroundingEnforcement.NOT_APPLIED,
        ),
        comparison=ShadowComparison(True, True, None, True, True, DivergenceType.MATCH_REPLY),
        stage=TraceStage.HOST_OUTPUT,
        revision=1,
        updated_at=NOW,
    )
    host_ref = EpisodeEventRef(
        "HOST_OUTPUT:event:host",
        EpisodeEventKind.HOST_OUTPUT,
        trace.event_id,
        trace.trace_id,
        f"{trace.trace_id}:1",
        NOW,
    )
    inbound_ref = EpisodeEventRef(
        "EXPERIENCE:event:inbound",
        EpisodeEventKind.EXPERIENCE,
        "event:inbound",
        None,
        None,
        NOW,
    )
    episode = Episode(
        "episode:rule",
        "scope:rule",
        EpisodeState.FINALIZED,
        "event:root",
        NOW,
        NOW,
        event_refs=(host_ref, inbound_ref),
        provenance=("fixture",),
    )
    host_identity = PlatformMessageIdentityV1("napcat", "bot", "group", "host-1")
    inbound_identity = PlatformMessageIdentityV1("napcat", "user", "group", "inbound-1")
    host = HostOutputMessageIdentityFactV1.create(
        platform_message_identity=host_identity,
        operation_index=0,
        host_send_result_schema_version="h0.2.v1",
        platform_send_receipt_schema_version="h0.2.receipt.v1",
        source_event_id=trace.event_id,
        trace_id=trace.trace_id,
        host_output_event_ref_id=host_ref.ref_id,
    )
    inbound = InboundReplyReferenceFactV1.create(
        source_event_id="event:inbound",
        source_platform_message_identity=inbound_identity,
        reply_target_platform_message_identity=reply_target or host_identity,
    )
    observation = OutcomeObservation(
        observation_id="outcome:rule",
        target_episode_id=target_episode_id or episode.episode_id,
        kind=OutcomeKind.EXPLICIT_CORRECTION,
        observed_at=NOW,
        source_event_id=source_event_id,
        explicitness=explicitness,
        confidence=1.0,
        producer="fixture",
    )
    fact_envelopes = {(EvidenceSourceType.HOST_RESULT, host_ref.ref_id): record}
    snapshot = ReviewInputSnapshot(episode, (observation,), fact_envelopes)
    outcome_ref = ReviewEvidenceRef(
        observation.observation_id,
        EvidenceSourceType.OUTCOME_OBSERVATION,
        EvidenceKind.EXPLICIT,
    )
    host_evidence_ref = ReviewEvidenceRef(host_ref.ref_id, EvidenceSourceType.HOST_RESULT, EvidenceKind.STRUCTURAL)
    evidence_refs = (host_evidence_ref, outcome_ref)
    if extra_finding_outcome:
        evidence_refs += (
            ReviewEvidenceRef("outcome:extra", EvidenceSourceType.OUTCOME_OBSERVATION, EvidenceKind.EXPLICIT),
        )
    finding = ReviewFinding(
        finding_id="finding:rule",
        review_run_id="run:rule",
        episode_id=episode.episode_id,
        dimension=ReviewDimension.ATTRIBUTION,
        finding_type=FindingType.LOCAL_OBSERVATION,
        claim=claim,
        evidence_refs=evidence_refs,
        attributed_to=AttributionRef(AttributionTargetType.HOST_RESULT, host_ref.ref_id),
        confidence=Confidence.MEDIUM,
        causal_attribution=CausalAttribution.NONE,
        interpretation_producer=InterpretationProducer.DETERMINISTIC,
        created_at=NOW,
        producer="deterministic_review_engine",
    )
    run = ReviewRun(
        "run:rule",
        episode.episode_id,
        ReviewStatus.COMPLETED,
        compute_input_snapshot_hash(episode, (observation,), fact_envelopes),
        created_at=NOW,
        findings=(finding,),
    )
    p2r0 = P2r0Store(tmp_path / "p2r0.jsonl")
    p2r0.record_host_output_fact(host)
    p2r0.record_inbound_reply_fact(inbound)
    p2 = P2PromotionStore(tmp_path / "p2.jsonl")
    archive_service = P2r0HistoricalArchiveService(p2, p2r0)
    archive = archive_service.archive_review_run(run, snapshot)
    assert archive is not None

    semantic_store = InboundSemanticActAuthorityStoreV1(tmp_path / "semantic.jsonl")
    evaluator = _Evaluator(decision)
    semantic_service = InboundSemanticActAuthorityServiceV1(
        semantic_store,
        profile=_profile(),
        evaluator=evaluator,
    )
    semantic_event_id = semantic_source_event_id or source_event_id
    if semantic_event_id is not None:
        semantic_inbound = inbound
        if semantic_source_event_id is not None:
            semantic_inbound = InboundReplyReferenceFactV1.create(
                source_event_id=semantic_event_id,
                source_platform_message_identity=inbound_identity,
                reply_target_platform_message_identity=reply_target or host_identity,
            )
        semantic_service.evaluate_after_inbound_commit(
            ResolvedEvent(
                event_id=semantic_event_id,
                source="napcat",
                occurred_at=NOW,
                session_id="group",
                mode="casual_group_chat",
                content="不是，我说的是……",
                actor=None,
            ),
            semantic_inbound,
            inbound_identity,
        )
    return {
        "episode": episode,
        "observation": observation,
        "snapshot": snapshot,
        "finding": finding,
        "run": run,
        "host": host,
        "inbound": inbound,
        "p2": p2,
        "p2r0": p2r0,
        "semantic_store": semantic_store,
        "evaluator": evaluator,
    }


def _candidate(fixture):
    return build_explicit_correction_candidate(
        fixture["run"],
        fixture["snapshot"],
        fixture["finding"],
        p2_store=fixture["p2"],
        p2r0_store=fixture["p2r0"],
        semantic_store=fixture["semantic_store"],
    )


def _rearchive_with_finding(tmp_path: Path, fixture, finding: ReviewFinding):
    """Commit one altered Finding shape into fresh P2/P2r0 authority stores.

    This deliberately avoids a caller-only mutation: each adversarial Finding
    below is made part of an otherwise valid archived ReviewRun before the
    rule sees it.
    """

    run = replace(fixture["run"], findings=(finding,))
    p2r0 = P2r0Store(tmp_path / "p2r0.jsonl")
    p2r0.record_host_output_fact(fixture["host"])
    p2r0.record_inbound_reply_fact(fixture["inbound"])
    p2 = P2PromotionStore(tmp_path / "p2.jsonl")
    archive = P2r0HistoricalArchiveService(p2, p2r0).archive_review_run(
        run, fixture["snapshot"]
    )
    assert archive is not None
    return {**fixture, "finding": finding, "run": run, "p2": p2, "p2r0": p2r0}


def test_exact_correction_rule_builds_one_candidate_from_authoritative_chain(tmp_path: Path):
    fixture = _fixture(tmp_path)
    candidate = _candidate(fixture)
    assert candidate is not None
    assert candidate.rule_id == RULE_ID
    assert candidate.evidence.proposition.statement == RULE_STATEMENT
    assert candidate.evidence.created_at == fixture["finding"].created_at
    assert candidate.host_output_fact_id == fixture["host"].fact_id
    assert candidate.inbound_reply_fact_id == fixture["inbound"].fact_id
    assert fixture["p2"].evidence_commits == ()


@pytest.mark.parametrize("decision", ["NO_MATCH", "ABSTAIN"])
def test_nonmatching_semantic_authority_does_not_build_candidate(tmp_path: Path, decision: str):
    assert _candidate(_fixture(tmp_path, decision=decision)) is None


def test_missing_semantic_authority_does_not_build_candidate(tmp_path: Path):
    fixture = _fixture(tmp_path)
    fixture["semantic_store"] = InboundSemanticActAuthorityStoreV1(tmp_path / "empty.jsonl")
    assert _candidate(fixture) is None


def test_missing_source_event_id_fails_closed(tmp_path: Path):
    assert _candidate(_fixture(tmp_path, source_event_id=None, semantic_source_event_id=None)) is None


def test_non_explicit_outcome_cannot_promote(tmp_path: Path):
    assert _candidate(_fixture(tmp_path, explicitness=OutcomeExplicitness.STRUCTURAL)) is None


def test_semantic_authority_for_different_source_event_fails_closed(tmp_path: Path):
    fixture = _fixture(tmp_path, semantic_source_event_id="event:other")
    assert _candidate(fixture) is None


def test_wrong_target_episode_fails_closed(tmp_path: Path):
    with pytest.raises(ValueError):
        _fixture(tmp_path, target_episode_id="episode:other")


def test_multiple_outcome_refs_fail_closed(tmp_path: Path):
    assert _candidate(_fixture(tmp_path, extra_finding_outcome=True)) is None


def test_additional_episode_event_reference_is_rejected_from_archived_finding(tmp_path: Path):
    fixture = _fixture(tmp_path / "base")
    extra = ReviewEvidenceRef(
        fixture["episode"].event_refs[1].ref_id,
        EvidenceSourceType.EPISODE_EVENT,
        EvidenceKind.STRUCTURAL,
    )
    finding = replace(
        fixture["finding"],
        evidence_refs=fixture["finding"].evidence_refs + (extra,),
    )
    assert _candidate(_rearchive_with_finding(tmp_path / "extra-event", fixture, finding)) is None


def test_duplicate_host_reference_is_rejected_from_archived_finding(tmp_path: Path):
    fixture = _fixture(tmp_path / "base")
    finding = replace(
        fixture["finding"],
        evidence_refs=fixture["finding"].evidence_refs
        + (fixture["finding"].evidence_refs[0],),
    )
    assert _candidate(_rearchive_with_finding(tmp_path / "duplicate-host", fixture, finding)) is None


def test_duplicate_outcome_reference_is_rejected_from_archived_finding(tmp_path: Path):
    fixture = _fixture(tmp_path / "base")
    finding = replace(
        fixture["finding"],
        evidence_refs=fixture["finding"].evidence_refs
        + (fixture["finding"].evidence_refs[1],),
    )
    assert _candidate(_rearchive_with_finding(tmp_path / "duplicate-outcome", fixture, finding)) is None


def test_reply_to_another_host_is_not_exact_reply_link(tmp_path: Path):
    other_host = PlatformMessageIdentityV1("napcat", "bot", "group", "host-2")
    assert _candidate(_fixture(tmp_path, reply_target=other_host)) is None


def test_finding_must_be_authoritative_run_member(tmp_path: Path):
    fixture = _fixture(tmp_path)
    synthetic = replace(fixture["finding"], finding_id="finding:synthetic")
    fixture["finding"] = synthetic
    assert _candidate(fixture) is None


def test_claim_prose_is_not_the_structural_authority(tmp_path: Path):
    fixture = _fixture(tmp_path, claim="arbitrary caller prose")
    candidate = _candidate(fixture)
    assert candidate is not None
    assert candidate.evidence.proposition.statement == RULE_STATEMENT


def test_rule_is_deterministic_and_does_not_call_provider(tmp_path: Path):
    fixture = _fixture(tmp_path)
    before = fixture["evaluator"].calls
    first = _candidate(fixture)
    second = _candidate(fixture)
    assert first is not None and second is not None
    assert first.evidence.evidence_id == second.evidence.evidence_id
    assert first.exact_reply_link_id == second.exact_reply_link_id
    assert fixture["evaluator"].calls == before
    assert fixture["p2"].evidence_commits == ()


def test_production_promotion_remains_disabled(tmp_path: Path):
    fixture = _fixture(tmp_path)
    assert _candidate(fixture) is not None
    decision = ProductionPromotionGateV1().evaluate(PromotionCommand("finding:rule", RULE_ID))
    assert not decision.accepted
    assert fixture["p2"].evidence_commits == ()
