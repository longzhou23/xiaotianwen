"""Permanent P2r0 pure-authority contract vectors."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from iris_memory.cognitive.contracts import (
    BehaviorExecutionRecord,
    BehaviorTrace,
    DivergenceType,
    GroundingEnforcement,
    HostResult,
    OutputProducer,
    OutputState,
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
    P2CanonicalArtifactEncoderV1,
    P2PromotionStore,
    P2ReviewRunWithSnapshotV1,
    default_encoding_profile_v1,
)
from iris_memory.cognitive.reply_link_authority import (
    ARCHIVE_PREFIX,
    FACT_CAPTURE_BINDING_DOMAIN,
    FACT_CAPTURE_BINDING_SCHEMA,
    HOST_FACT_PREFIX,
    INBOUND_FACT_PREFIX,
    P2R0_HOST_OUTPUT_FACT_CAPTURE,
    P2R0_INBOUND_REPLY_FACT_CAPTURE,
    P2R0_PERSISTENCE_ROOT,
    P2R0_TX_COMMIT,
    P2R0_TX_PREPARE,
    TX_PREFIX,
    CanonicalHashV1,
    ExactHostReplyLinkV1,
    FactCaptureAuthorityBindingV1,
    HostOutputMessageIdentityFactV1,
    InboundReplyReferenceFactV1,
    P2r0AuthorityResolver,
    P2r0CanonicalArtifactEncoderV1,
    P2r0CommitOutcomeIndeterminateError,
    P2r0FindingAttributionConflict,
    P2r0IntegrityError,
    P2r0Store,
    P2rReplyLinkFactArchiveV1,
    PlatformMessageIdentityV1,
    default_p2r0_encoding_profile,
    p2r0_encoding_profile_hash,
    resolve_exact_host_reply_link,
    resolve_finding_host_fact,
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
from iris_memory.cognitive.review_service import ReviewInputSnapshot, _canonical_json

NOW = datetime(2026, 9, 8, 3, 4, 5, 6007, tzinfo=timezone.utc)


def _facts():
    host_identity = PlatformMessageIdentityV1("napcat-instance-1", "bot-1", "group-1", "100")
    inbound_identity = PlatformMessageIdentityV1("napcat-instance-1", "user-1", "group-1", "101")
    host = HostOutputMessageIdentityFactV1.create(
        platform_message_identity=host_identity,
        operation_index=1,
        host_send_result_schema_version="h0.2.v1",
        platform_send_receipt_schema_version="h0.2.receipt.v1",
        source_event_id="event:host",
        trace_id="trace:host",
        host_output_event_ref_id="HOST_OUTPUT:event:host",
    )
    inbound = InboundReplyReferenceFactV1.create(
        source_event_id="event:inbound",
        source_platform_message_identity=inbound_identity,
        reply_target_platform_message_identity=host_identity,
    )
    return host, inbound


def _capture_tx_ids(path: Path):
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return {
        record["operation"]: record["transaction_id"]
        for record in records
        if record["record_type"] == P2R0_TX_PREPARE
    }


def _capture_tx_for_fact(path: Path, fact_id: str) -> str:
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for record in records:
        if record["record_type"] != P2R0_TX_PREPARE:
            continue
        fields = record["payload"].get("fields", {})
        if fields.get("fact_id") == fact_id:
            return record["transaction_id"]
    raise AssertionError(f"capture transaction for {fact_id} not found")


def _archive(store: P2r0Store, path: Path):
    host, inbound = _facts()
    store.record_host_output_fact(host)
    store.record_inbound_reply_fact(inbound)
    tx = _capture_tx_ids(path)
    archive = P2rReplyLinkFactArchiveV1.create(
        review_run_id="run:p2r0-test",
        episode_id="episode:p2r0-test",
        input_snapshot_hash="sha256:" + "1" * 64,
        p2_run_snapshot_logical_commit_hash="sha256:" + "2" * 64,
        p2r0_encoding_profile_hash=store.encoder.profile_hash,
        host_output_facts=(host,),
        inbound_reply_facts=(inbound,),
        fact_capture_authority=(
            FactCaptureAuthorityBindingV1(FACT_CAPTURE_BINDING_SCHEMA, host.fact_id, tx[P2R0_HOST_OUTPUT_FACT_CAPTURE]),
            FactCaptureAuthorityBindingV1(FACT_CAPTURE_BINDING_SCHEMA, inbound.fact_id, tx[P2R0_INBOUND_REPLY_FACT_CAPTURE]),
        ),
    )
    store.record_archive(archive)
    return host, inbound, archive


def _authoritative_host_fixture(tmp_path: Path):
    """Build a real persisted P2a Run/snapshot and a bound P2r0 archive."""
    trace = BehaviorTrace(
        event_id="event:host-authority",
        trigger=TriggerDecision(True, "test", 1),
        participation=None,
        intent=None,
        grounding=None,
        exit_reason=None,
        created_at=NOW,
    )
    object.__setattr__(trace, "trace_id", "trace:host-authority")
    record = BehaviorExecutionRecord(
        trace=trace,
        host_result=HostResult(
            legacy_fallthrough=True,
            output_generated=True,
            output_nonempty=True,
            dispatch_observed=True,
            output_state=OutputState.OUTPUT_READY,
            producer=OutputProducer.LEGACY_HOST,
            applied_enforcement=GroundingEnforcement.NOT_APPLIED,
        ),
        comparison=ShadowComparison(True, True, None, True, True, DivergenceType.MATCH_REPLY),
        stage=TraceStage.HOST_OUTPUT,
        revision=1,
        updated_at=NOW,
    )
    event_ref = EpisodeEventRef(
        ref_id="HOST_OUTPUT:event:host-authority",
        kind=EpisodeEventKind.HOST_OUTPUT,
        source_event_id=trace.event_id,
        trace_id=trace.trace_id,
        execution_record_id=f"{trace.trace_id}:1",
        observed_at=NOW,
    )
    episode = Episode(
        episode_id="episode:p2r0-authority",
        scope_id="p2r0-authority",
        state=EpisodeState.FINALIZED,
        root_event_id="event:root",
        opened_at=NOW,
        last_activity_at=NOW,
        event_refs=(event_ref,),
        provenance=("p2r0-test",),
    )
    identity = PlatformMessageIdentityV1("napcat-instance-1", "bot-1", "group-1", "900")
    inbound_identity = PlatformMessageIdentityV1("napcat-instance-1", "user-1", "group-1", "901")
    host = HostOutputMessageIdentityFactV1.create(
        platform_message_identity=identity,
        operation_index=1,
        host_send_result_schema_version="h0.2.v1",
        platform_send_receipt_schema_version="h0.2.receipt.v1",
        source_event_id=trace.event_id,
        trace_id=trace.trace_id,
        host_output_event_ref_id=event_ref.ref_id,
    )
    inbound = InboundReplyReferenceFactV1.create(
        source_event_id="event:inbound-authority",
        source_platform_message_identity=inbound_identity,
        reply_target_platform_message_identity=identity,
    )
    snapshot = ReviewInputSnapshot(
        episode,
        (),
        {
            (EvidenceSourceType.HOST_RESULT, event_ref.ref_id): record,
            (EvidenceSourceType.EPISODE_EVENT, event_ref.ref_id): event_ref,
        },
    )
    input_hash = "sha256:" + __import__("hashlib").sha256(_canonical_json(snapshot.canonical_payload()).encode("utf-8")).hexdigest()
    finding = ReviewFinding(
        finding_id="finding:p2r0-authority",
        review_run_id="run:p2r0-authority",
        episode_id=episode.episode_id,
        dimension=ReviewDimension.REALIZATION,
        finding_type=FindingType.LOCAL_OBSERVATION,
        claim="A host output was observed.",
        evidence_refs=(ReviewEvidenceRef(event_ref.ref_id, EvidenceSourceType.HOST_RESULT, EvidenceKind.STRUCTURAL),),
        attributed_to=AttributionRef(AttributionTargetType.HOST_RESULT, event_ref.ref_id),
        confidence=Confidence.MEDIUM,
        causal_attribution=CausalAttribution.NONE,
        interpretation_producer=InterpretationProducer.DETERMINISTIC,
        created_at=NOW,
        provenance=("p2r0-test",),
    )
    run = ReviewRun(
        review_run_id="run:p2r0-authority",
        episode_id=episode.episode_id,
        status=ReviewStatus.COMPLETED,
        input_snapshot_hash=input_hash,
        created_at=NOW,
        findings=(finding,),
        provenance=("p2r0-test",),
    )
    p2_path = tmp_path / "p2-authority.jsonl"
    p2 = P2PromotionStore(p2_path)
    p2_encoder = P2CanonicalArtifactEncoderV1(default_encoding_profile_v1())
    p2.record_encoding_profile(p2_encoder.profile, p2_encoder)
    p2_run = p2.record_run_with_snapshot(run, snapshot, p2_encoder)

    p2r_path = tmp_path / "p2r0-authority.jsonl"
    p2r = P2r0Store(p2r_path)
    p2r.record_host_output_fact(host)
    p2r.record_inbound_reply_fact(inbound)
    tx = _capture_tx_ids(p2r_path)
    archive = P2rReplyLinkFactArchiveV1.from_p2_run_snapshot(
        p2_run,
        p2r0_encoding_profile_hash=p2r.encoder.profile_hash,
        host_output_facts=(host,),
        inbound_reply_facts=(inbound,),
        fact_capture_authority=(
            FactCaptureAuthorityBindingV1(FACT_CAPTURE_BINDING_SCHEMA, host.fact_id, tx[P2R0_HOST_OUTPUT_FACT_CAPTURE]),
            FactCaptureAuthorityBindingV1(FACT_CAPTURE_BINDING_SCHEMA, inbound.fact_id, tx[P2R0_INBOUND_REPLY_FACT_CAPTURE]),
        ),
    )
    p2r.record_archive(archive)
    return p2, p2r, p2_run, snapshot, finding, host, inbound, archive


def test_four_field_platform_identity_and_generic_names():
    base = PlatformMessageIdentityV1("napcat-instance-1", "a", "c", "m")
    assert base == PlatformMessageIdentityV1("napcat-instance-1", "a", "c", "m")
    assert base != PlatformMessageIdentityV1("other-instance", "a", "c", "m")
    assert base != PlatformMessageIdentityV1("napcat-instance-1", "other", "c", "m")
    assert base != PlatformMessageIdentityV1("napcat-instance-1", "a", "other", "m")
    assert base != PlatformMessageIdentityV1("napcat-instance-1", "a", "c", "other")
    with pytest.raises(P2r0IntegrityError):
        PlatformMessageIdentityV1("qq", "a", "c", "m")


def test_profile_is_closed_and_p2a_separate():
    profile = default_p2r0_encoding_profile()
    encoder = P2r0CanonicalArtifactEncoderV1(profile)
    assert encoder.profile_hash == p2r0_encoding_profile_hash(profile)
    assert profile.type_profiles
    with pytest.raises(P2r0IntegrityError):
        P2r0CanonicalArtifactEncoderV1(type(profile)(profile.profile_id, profile.profile_version, profile.type_profiles, "p2a.canonical-artifact-encoding-profile.v1"))
    wrong_type_schema = replace(profile.type_profiles[0], schema="p2r0.unknown.v1")
    with pytest.raises(P2r0IntegrityError):
        P2r0CanonicalArtifactEncoderV1(
            type(profile)(profile.profile_id, profile.profile_version, (wrong_type_schema,) + profile.type_profiles[1:])
        )


def test_host_and_inbound_fact_ids_are_content_addressed():
    host, inbound = _facts()
    assert host.fact_id.startswith(HOST_FACT_PREFIX)
    assert inbound.fact_id.startswith(INBOUND_FACT_PREFIX)
    altered = HostOutputMessageIdentityFactV1.create(
        platform_message_identity=host.platform_message_identity,
        operation_index=2,
        host_send_result_schema_version=host.host_send_result_schema_version,
        platform_send_receipt_schema_version=host.platform_send_receipt_schema_version,
        source_event_id=host.source_event_id,
        trace_id=host.trace_id,
        host_output_event_ref_id=host.host_output_event_ref_id,
    )
    assert altered.fact_id != host.fact_id


def test_duplicate_host_platform_identity_is_a_conflict():
    path = Path("duplicate-host.jsonl")
    # The store's identity index is independent of fact_id; a second factual
    # record for the same platform message cannot become a parallel truth.
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        store = P2r0Store(Path(directory) / path)
        host, _ = _facts()
        store.record_host_output_fact(host)
        altered = HostOutputMessageIdentityFactV1.create(
            platform_message_identity=host.platform_message_identity,
            operation_index=host.operation_index + 1,
            host_send_result_schema_version=host.host_send_result_schema_version,
            platform_send_receipt_schema_version=host.platform_send_receipt_schema_version,
            source_event_id=host.source_event_id,
            trace_id=host.trace_id,
            host_output_event_ref_id=host.host_output_event_ref_id,
        )
        with pytest.raises(P2r0IntegrityError):
            store.record_host_output_fact(altered)


def test_archive_payload_tamper_is_rejected():
    host, inbound = _facts()
    binding = FactCaptureAuthorityBindingV1(FACT_CAPTURE_BINDING_SCHEMA, host.fact_id, TX_PREFIX + "0" * 64)
    archive = P2rReplyLinkFactArchiveV1.create(
        review_run_id="run:tamper",
        episode_id="episode:tamper",
        input_snapshot_hash="sha256:" + "1" * 64,
        p2_run_snapshot_logical_commit_hash="sha256:" + "2" * 64,
        p2r0_encoding_profile_hash=p2r0_encoding_profile_hash(),
        host_output_facts=(host,),
        inbound_reply_facts=(inbound,),
        fact_capture_authority=(binding, FactCaptureAuthorityBindingV1(FACT_CAPTURE_BINDING_SCHEMA, inbound.fact_id, TX_PREFIX + "1" * 64)),
    )
    with pytest.raises(P2r0IntegrityError):
        replace(archive, archive_payload_hash="sha256:" + "f" * 64)


def test_archive_is_acyclic_and_exact_link_uses_four_field_equality(tmp_path):
    path = tmp_path / "facts.jsonl"
    store = P2r0Store(path)
    host, inbound, archive = _archive(store, path)
    assert archive.archive_id.startswith(ARCHIVE_PREFIX)
    link = resolve_exact_host_reply_link(archive, inbound.fact_id)
    assert link.status == "EXACT_REPLY_LINK"
    assert link.host_output_fact_id == host.fact_id
    assert link.matched_platform_message_identity == host.platform_message_identity
    assert ExactHostReplyLinkV1.derive(archive, "missing").status == "EXACT_REPLY_LINK_UNAVAILABLE_NO_INBOUND_FACT"


def test_finding_join_uses_host_output_event_ref_not_fact_id(tmp_path):
    path = tmp_path / "facts.jsonl"
    store = P2r0Store(path)
    host, _, archive = _archive(store, path)

    finding = SimpleNamespace(
        review_run_id=archive.review_run_id,
        episode_id=archive.episode_id,
        attributed_to=AttributionRef(AttributionTargetType.HOST_RESULT, host.host_output_event_ref_id)
    )
    # A storage-valid P2r0 archive is not sufficient authority for a Finding
    # join; the persisted P2a Run/snapshot must be bound explicitly.
    assert resolve_finding_host_fact(finding, archive) is None
    wrong_namespace = SimpleNamespace(
        attributed_to=AttributionRef(AttributionTargetType.HOST_RESULT, host.fact_id)
    )
    assert resolve_finding_host_fact(wrong_namespace, archive) is None


def test_binding_domain_fingerprint_is_deterministic():
    host, _ = _facts()
    binding = FactCaptureAuthorityBindingV1(FACT_CAPTURE_BINDING_SCHEMA, host.fact_id, TX_PREFIX + "a" * 64)
    assert binding.fingerprint() == CanonicalHashV1.hash(FACT_CAPTURE_BINDING_DOMAIN, binding.identity_body())
    assert binding.fingerprint() == "sha256:29baf5dae35c95c16480d9be9a5a8686977f1d7607894ec94915c4444d554143"


def test_platform_identity_integer_normalization_and_strict_types():
    assert PlatformMessageIdentityV1(123, 456, " 789 ", " 42 ").canonical_body() == {
        "platform_id": "123", "account_id": "456", "conversation_id": "789", "message_id": "42",
    }
    with pytest.raises(P2r0IntegrityError):
        PlatformMessageIdentityV1(True, "account", "conversation", "message")
    with pytest.raises(P2r0IntegrityError):
        PlatformMessageIdentityV1(1.0, "account", "conversation", "message")
    with pytest.raises(P2r0IntegrityError):
        PlatformMessageIdentityV1("instance", None, "conversation", "message")


def test_authoritative_resolver_requires_persisted_p2_source(tmp_path):
    p2r_path = tmp_path / "p2r0.jsonl"
    p2r = P2r0Store(p2r_path)
    _host, inbound, archive = _archive(p2r, p2r_path)
    with pytest.raises(P2r0IntegrityError, match="authoritative P2 source"):
        P2r0AuthorityResolver(p2r).resolve_exact_host_reply_link(archive.archive_id, inbound.fact_id)
    with pytest.raises(P2r0IntegrityError, match="storage alone"):
        p2r.resolve_exact_host_reply_link(archive.archive_id, inbound.fact_id)
    assert P2r0AuthorityResolver(p2r, None)._p2_authority is None


def test_authoritative_resolver_uses_persisted_p2_and_finding_snapshot_join(tmp_path):
    p2, p2r, p2_run, _snapshot, finding, host, inbound, archive = _authoritative_host_fixture(tmp_path)
    resolver = P2r0AuthorityResolver(p2r, p2)
    link = resolver.resolve_exact_host_reply_link(archive.archive_id, inbound.fact_id)
    assert link.status == "EXACT_REPLY_LINK"
    assert resolver.resolve_finding_host_fact(finding, archive.archive_id) == host
    # A forged exact-type wrapper is not passed into resolver composition and
    # therefore cannot replace the persisted P2 authority source.
    assert p2_run.logical_commit_hash == archive.p2_run_snapshot_logical_commit_hash


def test_forged_caller_p2_wrapper_cannot_grant_authority(tmp_path):
    p2, _p2r, p2_run, _snapshot, _finding, _host, inbound, archive = _authoritative_host_fixture(tmp_path)
    forged = P2ReviewRunWithSnapshotV1(
        p2_run.schema_version,
        p2_run.run,
        p2_run.archive,
        p2_run.encoding_profile_hash,
        "sha256:" + "f" * 64,
    )
    # A separate P2r0 store can persist this storage-valid archive, but the
    # resolver must reject it against the bound authoritative P2a history.
    separate = P2r0Store(tmp_path / "forged-p2r0.jsonl")
    separate.record_host_output_fact(archive.host_output_facts[0])
    separate.record_inbound_reply_fact(inbound)
    tx = _capture_tx_ids(separate.path)
    separate_archive = P2rReplyLinkFactArchiveV1.from_p2_run_snapshot(
        forged,
        p2r0_encoding_profile_hash=separate.encoder.profile_hash,
        host_output_facts=archive.host_output_facts,
        inbound_reply_facts=archive.inbound_reply_facts,
        fact_capture_authority=(
            FactCaptureAuthorityBindingV1(FACT_CAPTURE_BINDING_SCHEMA, archive.host_output_facts[0].fact_id, tx[P2R0_HOST_OUTPUT_FACT_CAPTURE]),
            FactCaptureAuthorityBindingV1(FACT_CAPTURE_BINDING_SCHEMA, inbound.fact_id, tx[P2R0_INBOUND_REPLY_FACT_CAPTURE]),
        ),
    )
    separate.record_archive(separate_archive)
    with pytest.raises(P2r0IntegrityError):
        P2r0AuthorityResolver(separate, p2).resolve_exact_host_reply_link(separate_archive.archive_id, inbound.fact_id)


def test_frozen_inbound_and_binding_shapes_are_exact():
    host, inbound = _facts()
    assert tuple(InboundReplyReferenceFactV1.__dataclass_fields__) == (
        "schema_version", "fact_id", "source_event_id", "source_platform_message_identity",
        "reply_target_platform_message_identity",
    )
    assert tuple(FactCaptureAuthorityBindingV1.__dataclass_fields__) == (
        "schema_version", "fact_id", "transaction_id",
    )
    encoder = P2r0CanonicalArtifactEncoderV1()
    inbound_fields = encoder.encode(inbound)["fields"]
    binding = FactCaptureAuthorityBindingV1(FACT_CAPTURE_BINDING_SCHEMA, host.fact_id, TX_PREFIX + "b" * 64)
    binding_fields = encoder.encode(binding)["fields"]
    assert tuple(inbound_fields) == tuple(encoder.profile.type_profiles[2].fields)
    assert tuple(binding_fields) == tuple(encoder.profile.type_profiles[3].fields)
    with pytest.raises(P2r0IntegrityError):
        InboundReplyReferenceFactV1("wrong-schema", inbound.fact_id, inbound.source_event_id, inbound.source_platform_message_identity, inbound.reply_target_platform_message_identity)
    with pytest.raises(P2r0IntegrityError):
        FactCaptureAuthorityBindingV1("wrong-schema", host.fact_id, TX_PREFIX + "c" * 64)


def test_finding_join_rejects_wrong_event_kind_and_unattached_finding(tmp_path):
    p2, p2r, _p2_run, snapshot, finding, _host, _inbound, archive = _authoritative_host_fixture(tmp_path)
    resolver = P2r0AuthorityResolver(p2r, p2)
    assert resolver.resolve_finding_host_fact(finding, archive.archive_id) is not None
    wrong_target = replace(
        finding,
        attributed_to=AttributionRef(AttributionTargetType.EPISODE_EVENT, finding.attributed_to.ref_id),
    )
    assert resolver.resolve_finding_host_fact(wrong_target, archive.archive_id) is None
    missing_event = replace(
        finding,
        attributed_to=AttributionRef(AttributionTargetType.HOST_RESULT, "TOOL_RESULT:event:host-authority"),
    )
    assert resolver.resolve_finding_host_fact(missing_event, archive.archive_id) is None
    assert snapshot.episode.event_refs[0].kind is EpisodeEventKind.HOST_OUTPUT


def test_finding_join_rejects_host_fact_with_changed_lineage(tmp_path):
    p2, _p2r, _p2_run, _snapshot, finding, host, inbound, archive = _authoritative_host_fixture(tmp_path)
    bad_host = HostOutputMessageIdentityFactV1.create(
        platform_message_identity=PlatformMessageIdentityV1("napcat-instance-1", "bot-1", "group-1", "902"),
        operation_index=host.operation_index,
        host_send_result_schema_version=host.host_send_result_schema_version,
        platform_send_receipt_schema_version=host.platform_send_receipt_schema_version,
        source_event_id=host.source_event_id,
        trace_id="trace:wrong-lineage",
        host_output_event_ref_id=host.host_output_event_ref_id,
    )
    # This archive is internally valid, but its Host fact no longer matches
    # the authoritative snapshot EventRef trace/execution lineage.
    bad_store = P2r0Store(tmp_path / "bad-lineage-p2r0.jsonl")
    bad_store.record_host_output_fact(bad_host)
    bad_store.record_inbound_reply_fact(inbound)
    tx = _capture_tx_ids(bad_store.path)
    bad_archive = P2rReplyLinkFactArchiveV1.create(
        review_run_id=archive.review_run_id,
        episode_id=archive.episode_id,
        input_snapshot_hash=archive.input_snapshot_hash,
        p2_run_snapshot_logical_commit_hash=archive.p2_run_snapshot_logical_commit_hash,
        p2r0_encoding_profile_hash=bad_store.encoder.profile_hash,
        host_output_facts=(bad_host,),
        inbound_reply_facts=(inbound,),
        fact_capture_authority=(
            FactCaptureAuthorityBindingV1(FACT_CAPTURE_BINDING_SCHEMA, bad_host.fact_id, tx[P2R0_HOST_OUTPUT_FACT_CAPTURE]),
            FactCaptureAuthorityBindingV1(FACT_CAPTURE_BINDING_SCHEMA, inbound.fact_id, tx[P2R0_INBOUND_REPLY_FACT_CAPTURE]),
        ),
    )
    bad_store.record_archive(bad_archive)
    resolver = P2r0AuthorityResolver(bad_store, p2)
    assert resolver.resolve_finding_host_fact(finding, bad_archive.archive_id) is None


def test_finding_join_rejects_two_host_facts_for_one_authoritative_event(tmp_path):
    p2, _p2r, _p2_run, _snapshot, finding, host, inbound, archive = _authoritative_host_fixture(tmp_path)
    second = HostOutputMessageIdentityFactV1.create(
        platform_message_identity=PlatformMessageIdentityV1("napcat-instance-1", "bot-2", "group-1", "903"),
        operation_index=host.operation_index,
        host_send_result_schema_version=host.host_send_result_schema_version,
        platform_send_receipt_schema_version=host.platform_send_receipt_schema_version,
        source_event_id=host.source_event_id,
        trace_id=host.trace_id,
        host_output_event_ref_id=host.host_output_event_ref_id,
    )
    # Archive construction remains acyclic and storage-valid; ambiguity is an
    # authoritative Finding-join conflict, not a reason to pick first-writer.
    store = P2r0Store(tmp_path / "ambiguous.jsonl")
    store.record_host_output_fact(host)
    store.record_host_output_fact(second)
    store.record_inbound_reply_fact(inbound)
    ambiguous = P2rReplyLinkFactArchiveV1.create(
        review_run_id=archive.review_run_id,
        episode_id=archive.episode_id,
        input_snapshot_hash=archive.input_snapshot_hash,
        p2_run_snapshot_logical_commit_hash=archive.p2_run_snapshot_logical_commit_hash,
        p2r0_encoding_profile_hash=store.encoder.profile_hash,
        host_output_facts=(host, second),
        inbound_reply_facts=archive.inbound_reply_facts,
        fact_capture_authority=(
            FactCaptureAuthorityBindingV1(FACT_CAPTURE_BINDING_SCHEMA, host.fact_id, _capture_tx_for_fact(store.path, host.fact_id)),
            FactCaptureAuthorityBindingV1(FACT_CAPTURE_BINDING_SCHEMA, second.fact_id, _capture_tx_for_fact(store.path, second.fact_id)),
            FactCaptureAuthorityBindingV1(FACT_CAPTURE_BINDING_SCHEMA, inbound.fact_id, _capture_tx_for_fact(store.path, inbound.fact_id)),
        ),
    )
    store.record_archive(ambiguous)
    resolver = P2r0AuthorityResolver(store, p2)
    with pytest.raises(P2r0FindingAttributionConflict):
        resolver.resolve_finding_host_fact(finding, ambiguous.archive_id)


def test_prepare_commit_and_reopen_publish_only_committed_history(tmp_path):
    path = tmp_path / "facts.jsonl"
    store = P2r0Store(path)
    host, _ = _facts()
    store.record_host_output_fact(host)
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert sum(record["record_type"] == P2R0_TX_PREPARE for record in records) == 1
    assert sum(record["record_type"] == P2R0_TX_COMMIT for record in records) == 1
    assert P2R0_PERSISTENCE_ROOT == "p2r0-reply-link-facts"
    assert P2r0Store(path).host_output_facts == (host,)
    prepare_only = tmp_path / "prepare-only.jsonl"
    prepare = next(record for record in records if record["record_type"] == P2R0_TX_PREPARE)
    prepare_only.write_bytes((json.dumps(prepare, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
    assert P2r0Store(prepare_only).host_output_facts == ()


def test_archive_binding_requires_committed_capture(tmp_path):
    path = tmp_path / "facts.jsonl"
    store = P2r0Store(path)
    host, inbound = _facts()
    fake_tx = TX_PREFIX + "0" * 64
    archive = P2rReplyLinkFactArchiveV1.create(
        review_run_id="run", episode_id="episode", input_snapshot_hash="sha256:" + "3" * 64, p2_run_snapshot_logical_commit_hash="sha256:" + "4" * 64,
        p2r0_encoding_profile_hash=store.encoder.profile_hash, host_output_facts=(host,), inbound_reply_facts=(inbound,),
        fact_capture_authority=(FactCaptureAuthorityBindingV1(FACT_CAPTURE_BINDING_SCHEMA, host.fact_id, fake_tx), FactCaptureAuthorityBindingV1(FACT_CAPTURE_BINDING_SCHEMA, inbound.fact_id, fake_tx)),
    )
    with pytest.raises(P2r0IntegrityError):
        store.record_archive(archive)


def test_commit_failure_does_not_publish_authority(tmp_path):
    path = tmp_path / "failure.jsonl"

    class FailingStore(P2r0Store):
        def _append(self, record, stage):
            if stage == P2R0_TX_COMMIT:
                raise OSError("injected fsync failure")
            return super()._append(record, stage)

    store = FailingStore(path)
    host, _ = _facts()
    with pytest.raises(P2r0CommitOutcomeIndeterminateError):
        store.record_host_output_fact(host)
    assert store.host_output_facts == ()
    # The failed transaction has no COMMIT and therefore cannot become fact
    # authority after a fresh process/replay.
    assert P2r0Store(path).host_output_facts == ()


@pytest.mark.parametrize("failure_kind", ("prepare_write", "commit_flush", "commit_fsync"))
def test_persistence_failure_never_publishes_p2r0_authority(tmp_path, failure_kind):
    path = tmp_path / f"failure-{failure_kind}.jsonl"

    class FailingStore(P2r0Store):
        def __init__(self, target):
            self._append_calls = 0
            super().__init__(target)

        def _append(self, record, stage):
            self._append_calls += 1
            if failure_kind == "prepare_write" and self._append_calls == 1:
                raise OSError("injected PREPARE write failure")
            return super()._append(record, stage)

        def _flush_append_handle(self, handle):
            if failure_kind == "commit_flush" and self._append_calls == 2:
                raise OSError("injected COMMIT flush failure")
            return super()._flush_append_handle(handle)

        def _sync_append_handle(self, handle):
            if failure_kind == "commit_fsync" and self._append_calls == 2:
                raise OSError("injected COMMIT fsync failure")
            return super()._sync_append_handle(handle)

    store = FailingStore(path)
    host, _ = _facts()
    expected = OSError if failure_kind == "prepare_write" else P2r0CommitOutcomeIndeterminateError
    with pytest.raises(expected):
        store.record_host_output_fact(host)
    assert store.host_output_facts == ()
    assert P2r0Store(path).host_output_facts == ()


def test_conflicting_inbound_targets_for_one_source_are_rejected():
    host, inbound = _facts()
    other = InboundReplyReferenceFactV1.create(
        source_event_id=inbound.source_event_id,
        source_platform_message_identity=inbound.source_platform_message_identity,
        reply_target_platform_message_identity=PlatformMessageIdentityV1("napcat-instance-1", "bot-2", "group-1", "200"),
    )
    binding = (
        FactCaptureAuthorityBindingV1(FACT_CAPTURE_BINDING_SCHEMA, host.fact_id, TX_PREFIX + "0" * 64),
        FactCaptureAuthorityBindingV1(FACT_CAPTURE_BINDING_SCHEMA, inbound.fact_id, TX_PREFIX + "1" * 64),
        FactCaptureAuthorityBindingV1(FACT_CAPTURE_BINDING_SCHEMA, other.fact_id, TX_PREFIX + "2" * 64),
    )
    with pytest.raises(P2r0IntegrityError):
        P2rReplyLinkFactArchiveV1.create(
            review_run_id="run", episode_id="episode", input_snapshot_hash="sha256:" + "3" * 64,
            p2_run_snapshot_logical_commit_hash="sha256:" + "4" * 64,
            p2r0_encoding_profile_hash=p2r0_encoding_profile_hash(), host_output_facts=(host,),
            inbound_reply_facts=(inbound, other), fact_capture_authority=binding,
        )


def test_replay_rejects_unknown_or_noncanonical_history(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text('{"record_type":"UNKNOWN"}\n', encoding="utf-8")
    with pytest.raises(P2r0IntegrityError):
        P2r0Store(path)
