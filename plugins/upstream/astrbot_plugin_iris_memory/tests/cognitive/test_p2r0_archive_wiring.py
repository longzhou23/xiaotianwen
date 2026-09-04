from dataclasses import replace
from datetime import datetime, timezone

import pytest

from iris_memory.cognitive.contracts import (
    BehaviorExecutionRecord,
    BehaviorTrace,
    DivergenceType,
    GroundingEnforcement,
    HostResult,
    OutputProducer,
    OutputState,
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
from iris_memory.cognitive.promotion_infrastructure import (
    LegacyArchiveUnavailableError,
    P2PromotionStore,
    P2ValidatedEvidenceReader,
    ProductionPromotionGateV1,
    PromotionCommand,
)
from iris_memory.cognitive.reply_link_archive import (
    HistoricalArchiveWiringError,
    P2r0HistoricalArchiveService,
    ProductionReviewCompletionCoordinator,
)
from iris_memory.cognitive.reply_link_authority import (
    FACT_CAPTURE_BINDING_SCHEMA,
    P2R0_HOST_OUTPUT_FACT_CAPTURE,
    P2R0_TX_PREPARE,
    FactCaptureAuthorityBindingV1,
    HostOutputMessageIdentityFactV1,
    InboundReplyReferenceFactV1,
    P2r0IntegrityError,
    P2r0Store,
    P2rReplyLinkFactArchiveV1,
    PlatformMessageIdentityV1,
)
from iris_memory.cognitive.review import ReviewRun, ReviewStatus
from iris_memory.cognitive.review_service import (
    ReviewInputSnapshot,
    compute_input_snapshot_hash,
)
from iris_memory.cognitive.review_store import AppendOnlyReviewStore

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _fixture(tmp_path):
    trace = BehaviorTrace(
        event_id="event:archive-input", trigger=TriggerDecision(True, "test", 1),
        participation=None, intent=None, grounding=None, exit_reason=None,
        runtime_mode=RuntimeMode.SHADOW, created_at=NOW,
    )
    object.__setattr__(trace, "trace_id", "trace:archive-input")
    record = BehaviorExecutionRecord(
        trace=trace,
        host_result=HostResult(True, True, True, True, OutputState.OUTPUT_READY, OutputProducer.LEGACY_HOST, GroundingEnforcement.NOT_APPLIED),
        comparison=ShadowComparison(True, True, None, True, True, DivergenceType.MATCH_REPLY),
        stage=TraceStage.HOST_OUTPUT, revision=1, updated_at=NOW,
    )
    host_ref = EpisodeEventRef(
        "HOST_OUTPUT:event:archive-input", EpisodeEventKind.HOST_OUTPUT,
        trace.event_id, trace.trace_id, f"{trace.trace_id}:1", NOW,
    )
    inbound_ref = EpisodeEventRef(
        "EXPERIENCE:event:archive-inbound", EpisodeEventKind.EXPERIENCE,
        "event:archive-inbound", None, None, NOW,
    )
    episode = Episode(
        "episode:archive", "scope:archive", EpisodeState.FINALIZED, "event:root",
        NOW, NOW, event_refs=(host_ref, inbound_ref), provenance=("test",),
    )
    host_identity = PlatformMessageIdentityV1("napcat", "bot", "group", "900")
    inbound_identity = PlatformMessageIdentityV1("napcat", "user", "group", "901")
    host = HostOutputMessageIdentityFactV1.create(
        platform_message_identity=host_identity, operation_index=0,
        host_send_result_schema_version="h0.2.v1", platform_send_receipt_schema_version="h0.2.receipt.v1",
        source_event_id=trace.event_id, trace_id=trace.trace_id, host_output_event_ref_id=host_ref.ref_id,
    )
    inbound = InboundReplyReferenceFactV1.create(
        source_event_id="event:archive-inbound", source_platform_message_identity=inbound_identity,
        reply_target_platform_message_identity=host_identity,
    )
    snapshot = ReviewInputSnapshot(episode, (), {})
    run = ReviewRun(
        "run:archive", episode.episode_id, ReviewStatus.COMPLETED,
        compute_input_snapshot_hash(episode, ()), created_at=NOW,
    )
    p2r0 = P2r0Store(tmp_path / "p2r0.jsonl")
    p2r0.record_host_output_fact(host)
    p2r0.record_inbound_reply_fact(inbound)
    p2 = P2PromotionStore(tmp_path / "p2.jsonl")
    return P2r0HistoricalArchiveService(p2, p2r0), p2, p2r0, run, snapshot, host, inbound, record


def test_archive_composes_committed_facts_and_is_idempotent(tmp_path):
    service, p2, p2r0, run, snapshot, host, inbound, _ = _fixture(tmp_path)
    archive = service.archive_review_run(run, snapshot)
    assert archive is not None
    assert tuple(item.fact_id for item in archive.host_output_facts) == (host.fact_id,)
    assert tuple(item.fact_id for item in archive.inbound_reply_facts) == (inbound.fact_id,)
    assert len(p2r0.archives) == 1
    assert service.archive_review_run(run, snapshot) == archive
    assert p2.require_archive(run)


def test_unrelated_inbound_fact_is_excluded(tmp_path):
    service, _, p2r0, run, snapshot, _, inbound, _ = _fixture(tmp_path)
    foreign = InboundReplyReferenceFactV1.create(
        source_event_id="event:foreign", source_platform_message_identity=inbound.source_platform_message_identity,
        reply_target_platform_message_identity=inbound.reply_target_platform_message_identity,
    )
    p2r0.record_inbound_reply_fact(foreign)
    archive = service.archive_review_run(run, snapshot)
    assert archive is not None
    assert tuple(item.source_event_id for item in archive.inbound_reply_facts) == (inbound.source_event_id,)


def test_segmented_host_facts_are_all_archived(tmp_path):
    service, _, p2r0, run, snapshot, host, _, _ = _fixture(tmp_path)
    second = HostOutputMessageIdentityFactV1.create(
        platform_message_identity=PlatformMessageIdentityV1("napcat", "bot", "group", "901"),
        operation_index=1, host_send_result_schema_version=host.host_send_result_schema_version,
        platform_send_receipt_schema_version=host.platform_send_receipt_schema_version,
        source_event_id=host.source_event_id, trace_id=host.trace_id,
        host_output_event_ref_id=host.host_output_event_ref_id,
    )
    p2r0.record_host_output_fact(second)
    archive = service.archive_review_run(run, snapshot)
    assert archive is not None
    assert {item.fact_id for item in archive.host_output_facts} == {host.fact_id, second.fact_id}


def test_no_carriers_yields_empty_factual_archive(tmp_path):
    _, _, _, run, snapshot, *_ = _fixture(tmp_path)
    p2r0 = P2r0Store(tmp_path / "empty-p2r0.jsonl")
    p2 = P2PromotionStore(tmp_path / "empty-p2.jsonl")
    service = P2r0HistoricalArchiveService(p2, p2r0)
    archive = service.archive_review_run(run, snapshot)
    assert archive is not None
    assert archive.host_output_facts == ()
    assert archive.inbound_reply_facts == ()
    assert archive.fact_capture_authority == ()


def test_prepare_only_capture_is_not_archived(tmp_path):
    service, _, p2r0, run, snapshot, host, _, _ = _fixture(tmp_path)
    p2r0 = P2r0Store(tmp_path / "prepare-only.jsonl")
    prepare = p2r0._prepare(P2R0_HOST_OUTPUT_FACT_CAPTURE, p2r0.encoder.encode(host))
    p2r0._append(prepare, P2R0_TX_PREPARE)
    service = P2r0HistoricalArchiveService(P2PromotionStore(tmp_path / "prepare-p2.jsonl"), p2r0)
    archive = service.archive_review_run(run, snapshot)
    assert archive is not None
    assert archive.host_output_facts == ()


def test_wrong_capture_binding_is_rejected_by_store(tmp_path):
    service, p2, p2r0, run, snapshot, host, _, _ = _fixture(tmp_path)
    p2_run = p2.record_run_with_snapshot(run, snapshot, service._encoder)
    binding = FactCaptureAuthorityBindingV1(
        FACT_CAPTURE_BINDING_SCHEMA, host.fact_id, "tx:p2r0:" + "0" * 64,
    )
    archive = P2rReplyLinkFactArchiveV1.from_p2_run_snapshot(
        p2_run, p2r0_encoding_profile_hash=p2r0.encoder.profile_hash,
        host_output_facts=(host,), inbound_reply_facts=(), fact_capture_authority=(binding,),
    )
    with pytest.raises(P2r0IntegrityError):
        p2r0.record_archive(archive, authoritative_p2_run=p2_run)


def test_production_promotion_remains_closed(tmp_path):
    service, p2, _, _run, _, *_ = _fixture(tmp_path)
    decision = ProductionPromotionGateV1().evaluate(PromotionCommand("finding:any"))
    assert not decision.accepted
    assert P2ValidatedEvidenceReader(p2).validated_evidence() == ()
    assert service.p2_store.evidence_commits == ()


def test_legacy_run_has_no_archive_backfill(tmp_path):
    _, _, _, run, *_ = _fixture(tmp_path)
    fresh = P2PromotionStore(tmp_path / "legacy.jsonl")
    with pytest.raises(LegacyArchiveUnavailableError):
        fresh.require_archive(run)


def test_archive_committed_run_without_p2_authority_fails_closed(tmp_path):
    service, p2, _, run, snapshot, *_ = _fixture(tmp_path)
    p2_run = p2.record_run_with_snapshot(run, snapshot, service._encoder)
    missing_authority = P2PromotionStore(tmp_path / "missing-authority.jsonl")
    missing_service = P2r0HistoricalArchiveService(
        missing_authority, P2r0Store(tmp_path / "missing-authority-p2r0.jsonl")
    )
    assert missing_service.archive_committed_run(p2_run, snapshot) is None


def test_production_completion_commits_run_then_archive(tmp_path):
    service, p2, p2r0, _run, snapshot, *_ = _fixture(tmp_path)
    coordinator = ProductionReviewCompletionCoordinator(
        service, AppendOnlyReviewStore(tmp_path / "reviews.jsonl")
    )
    completed = coordinator.complete_episode(snapshot.episode, ())
    assert completed is not None
    assert completed.review_run_id.startswith("run:production:")
    assert p2.require_archive(completed)
    assert len(p2r0.archives) == 1


def test_production_completion_retry_is_idempotent(tmp_path):
    service, _, p2r0, _, snapshot, *_ = _fixture(tmp_path)
    coordinator = ProductionReviewCompletionCoordinator(
        service, AppendOnlyReviewStore(tmp_path / "reviews.jsonl")
    )
    first = coordinator.complete_episode(snapshot.episode, ())
    second = coordinator.complete_episode(snapshot.episode, ())
    assert first is not None and second is not None
    assert first.review_run_id == second.review_run_id
    assert len(p2r0.archives) == 1


def test_unfinalized_episode_does_not_start_production_review(tmp_path):
    service, p2, p2r0, _, snapshot, *_ = _fixture(tmp_path)
    open_episode = replace(snapshot.episode, state=EpisodeState.OPEN)
    coordinator = ProductionReviewCompletionCoordinator(
        service, AppendOnlyReviewStore(tmp_path / "reviews.jsonl")
    )
    assert coordinator.complete_episode(open_episode, ()) is None
    assert p2.evidence_commits == ()
    assert p2r0.archives == ()


def test_archive_failure_does_not_rollback_committed_p2_run(tmp_path, monkeypatch):
    service, p2, p2r0, run, snapshot, *_ = _fixture(tmp_path)
    def fail(*_args, **_kwargs):
        raise OSError("disk")
    monkeypatch.setattr(p2r0, "record_archive", fail)
    assert service.archive_review_run(run, snapshot) is None
    assert p2.require_archive(run)
    assert p2r0.archives == ()


def test_archive_requires_exact_types(tmp_path):
    service, *_ = _fixture(tmp_path)
    with pytest.raises(HistoricalArchiveWiringError):
        service.archive_review_run(object(), object())
