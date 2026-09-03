"""P2a.2 infrastructure: immutable identity, archive, replay, and reject-all tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import json
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
    ShadowComparison,
    TraceStage,
    TriggerDecision,
)
from iris_memory.cognitive.episode import Episode, EpisodeEventKind, EpisodeEventRef, EpisodeState, make_episode_id
from iris_memory.cognitive.outcome import OutcomeKind, OutcomeObservation, make_outcome_observation_id
from iris_memory.cognitive.promotion_infrastructure import (
    CanonicalHashV1,
    COMMIT_OUTCOME_INDETERMINATE,
    CommitOutcomeIndeterminateError,
    LegacyArchiveUnavailableError,
    NOT_P2_VALID,
    P2CanonicalArtifactEncoderV1,
    P2PromotedEvidenceCommitV1,
    P2PromotionStore,
    SyntheticP2PromotionStore,
    P2ValidatedEvidenceReader,
    PRODUCTION_RULE_DISABLED,
    ProductionPromotionGateV1,
    PromotionCommand,
    PromotionInfrastructureIntegrityError,
    PromotionReceiptV1,
    ReceiptIdentityBodyV1,
    FrozenCanonicalArtifactEncodingProfileV1,
    CanonicalTypeProfileV1,
    P2_CANONICAL_ARTIFACT_ENCODING_PROFILE,
    P2_TX_COMMIT,
    P2_TX_PREPARE,
    P2_PROMOTED_EVIDENCE_COMMIT,
    P2_REVIEW_RUN_WITH_SNAPSHOT,
    build_synthetic_candidate,
    canonical_artifact_encoding_profile_hash,
    default_encoding_profile_v1,
)
from iris_memory.cognitive.review import (
    AttributionRef,
    AttributionTargetType,
    BehaviorScope,
    CausalAttribution,
    Confidence,
    EvidenceKind,
    EvidenceSourceType,
    FindingType,
    InterpretationProducer,
    LocalEvidenceProposition,
    ReviewDimension,
    ReviewEvidence,
    ReviewEvidenceRef,
    ReviewFinding,
    ReviewRun,
    ReviewStatus,
)
from iris_memory.cognitive.review_service import ReviewInputSnapshot, _canonical_json


NOW = datetime(2026, 9, 8, 3, 4, 5, 6007, tzinfo=timezone.utc)


def _artifacts():
    episode = Episode(
        episode_id=make_episode_id("p2-test", "event:1"),
        scope_id="p2-test",
        state=EpisodeState.FINALIZED,
        root_event_id="event:1",
        opened_at=NOW,
        last_activity_at=NOW,
        provenance=("p2-test",),
    )
    outcome = OutcomeObservation(
        observation_id=make_outcome_observation_id(episode.episode_id, OutcomeKind.EXPLICIT_CORRECTION, source_event_id="event:2"),
        target_episode_id=episode.episode_id,
        kind=OutcomeKind.EXPLICIT_CORRECTION,
        observed_at=NOW,
        source_event_id="event:2",
    )
    snapshot = ReviewInputSnapshot(episode, (outcome,), {})
    input_hash = "sha256:" + __import__("hashlib").sha256(_canonical_json(snapshot.canonical_payload()).encode("utf-8")).hexdigest()
    finding = ReviewFinding(
        finding_id="finding:p2-test",
        review_run_id="run:p2-test",
        episode_id=episode.episode_id,
        dimension=ReviewDimension.ATTRIBUTION,
        finding_type=FindingType.LOCAL_OBSERVATION,
        claim="A local historical observation exists.",
        evidence_refs=(ReviewEvidenceRef(outcome.observation_id, EvidenceSourceType.OUTCOME_OBSERVATION, EvidenceKind.EXPLICIT),),
        attributed_to=AttributionRef(AttributionTargetType.OUTCOME_OBSERVATION, outcome.observation_id),
        confidence=Confidence.MEDIUM,
        causal_attribution=CausalAttribution.NONE,
        interpretation_producer=InterpretationProducer.DETERMINISTIC,
        created_at=NOW,
        provenance=("p2-test",),
    )
    run = ReviewRun(
        review_run_id="run:p2-test",
        episode_id=episode.episode_id,
        status=ReviewStatus.COMPLETED,
        input_snapshot_hash=input_hash,
        created_at=NOW,
        findings=(finding,),
        provenance=("p2-test",),
    )
    evidence = ReviewEvidence(
        evidence_id="evidence:caller-controlled",
        source_review_run_id=run.review_run_id,
        source_finding_id=finding.finding_id,
        source_episode_id=episode.episode_id,
        dimension=finding.dimension,
        proposition=LocalEvidenceProposition(
            ReviewDimension.ATTRIBUTION,
            observation_refs=(outcome.observation_id,),
            statement="A local historical observation exists.",
        ),
        scope=BehaviorScope("p2-test", "directed"),
        evidence_refs=finding.evidence_refs,
        confidence=finding.confidence,
        causal_attribution=CausalAttribution.NONE,
        attributed_to=finding.attributed_to,
        interpretation_producer=finding.interpretation_producer,
        created_at=finding.created_at,
        provenance=("p2-test",),
    )
    return episode, outcome, snapshot, finding, run, evidence


def _attached_host_artifacts():
    """Use legal P1 identities that are deliberately not helper-derived."""
    trace = BehaviorTrace(
        event_id="event:host-source",
        trigger=TriggerDecision(True, "test", 1),
        participation=None,
        intent=None,
        grounding=None,
        exit_reason=None,
        created_at=NOW,
    )
    object.__setattr__(trace, "trace_id", "trace:host-custom")
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
        revision=7,
        updated_at=NOW,
    )
    event_ref = EpisodeEventRef(
        ref_id="host-ref:custom-non-derived",
        kind=EpisodeEventKind.HOST_OUTPUT,
        source_event_id=trace.event_id,
        trace_id=trace.trace_id,
        execution_record_id=f"{trace.trace_id}:{record.revision}",
        observed_at=NOW,
    )
    episode = Episode(
        episode_id="episode:custom-authoritative-id",
        scope_id="p2-test",
        state=EpisodeState.FINALIZED,
        root_event_id="event:root-not-in-id",
        opened_at=NOW,
        last_activity_at=NOW,
        event_refs=(event_ref,),
        provenance=("p2-test",),
    )
    outcome = OutcomeObservation(
        observation_id="outcome:custom-authoritative-id",
        target_episode_id=episode.episode_id,
        kind=OutcomeKind.EXPLICIT_CORRECTION,
        observed_at=NOW,
        source_event_id="event:late-feedback",
    )
    snapshot = ReviewInputSnapshot(
        episode,
        (outcome,),
        {
            (EvidenceSourceType.HOST_RESULT, event_ref.ref_id): record,
            (EvidenceSourceType.BEHAVIOR_TRACE, trace.trace_id): trace,
            (EvidenceSourceType.EPISODE_EVENT, event_ref.ref_id): event_ref,
            (EvidenceSourceType.OUTCOME_OBSERVATION, outcome.observation_id): outcome,
        },
    )
    input_hash = "sha256:" + hashlib.sha256(_canonical_json(snapshot.canonical_payload()).encode("utf-8")).hexdigest()
    run = ReviewRun(
        review_run_id="run:custom-authoritative-id",
        episode_id=episode.episode_id,
        status=ReviewStatus.INSUFFICIENT_EVIDENCE,
        input_snapshot_hash=input_hash,
        created_at=NOW,
        findings=(),
        provenance=("p2-test",),
    )
    return snapshot, run


def _profile_store(tmp_path, *, test_only=True):
    profile = default_encoding_profile_v1()
    encoder = P2CanonicalArtifactEncoderV1(profile)
    store = SyntheticP2PromotionStore(tmp_path / "promotion.jsonl") if test_only else P2PromotionStore(tmp_path / "promotion.jsonl")
    assert store.record_encoding_profile(profile, encoder) == encoder.profile_hash
    return store, profile, encoder


def _transaction_records(records, operation):
    prepare = next(record for record in records if record["record_type"] == P2_TX_PREPARE and record["operation"] == operation)
    commit = next(record for record in records if record["record_type"] == P2_TX_COMMIT and record["transaction_id"] == prepare["transaction_id"])
    return prepare, commit


def _recompute_prepare(record):
    payload_hash = CanonicalHashV1.hash("p2a:persistence-transaction-payload:v1", record["payload"])
    record["payload_hash"] = payload_hash
    identity = {
        "transaction_schema": record["transaction_schema"],
        "persistence_root": record["persistence_root"],
        "operation": record["operation"],
        "payload_hash": payload_hash,
    }
    record["transaction_id"] = "tx:p2a:" + CanonicalHashV1.digest("p2a:persistence-transaction-identity:v1", identity)
    body = {key: record[key] for key in record if key != "prepare_hash"}
    record["prepare_hash"] = CanonicalHashV1.hash("p2a:persistence-transaction-prepare:v1", body)


def _recompute_commit(record, prepare):
    record["transaction_id"] = prepare["transaction_id"]
    record["prepare_hash"] = prepare["prepare_hash"]
    body = {key: record[key] for key in record if key != "commit_hash"}
    record["commit_hash"] = CanonicalHashV1.hash("p2a:persistence-transaction-commit:v1", body)


def _recompute_transaction(records, prepare):
    previous_id = prepare["transaction_id"]
    commit = next(record for record in records if record["record_type"] == P2_TX_COMMIT and record["transaction_id"] == previous_id)
    _recompute_prepare(prepare)
    _recompute_commit(commit, prepare)


def _write_canonical_records(path, records):
    path.write_bytes(b"".join(CanonicalHashV1.canonical_json_utf8(record) + b"\n" for record in records))


class _RollbackFailureHandle:
    """Delegate every file operation except cleanup truncation."""

    def __init__(self, handle):
        self._handle = handle

    def __enter__(self):
        self._handle.__enter__()
        return self

    def __exit__(self, *args):
        return self._handle.__exit__(*args)

    def __getattr__(self, name):
        return getattr(self._handle, name)

    def truncate(self, *_args, **_kwargs):
        raise OSError("simulated rollback truncate failure")


def _make_truncate_fail_for_store(monkeypatch, store):
    original_open = Path.open

    def wrapped_open(path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        if path == store.path and args and args[0] == "a+b":
            return _RollbackFailureHandle(handle)
        return handle

    monkeypatch.setattr(Path, "open", wrapped_open)


def _recompute_run_record(records, record, raw):
    fields = record["payload"]["fields"]
    archive = fields["archive"]["fields"]
    run = fields["run"]["fields"]
    p1_hash = "sha256:" + hashlib.sha256(raw).hexdigest()
    run["input_snapshot_hash"] = p1_hash
    archive["input_snapshot_hash"] = p1_hash
    archive["canonical_snapshot_json_utf8"] = {"$bytes": base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")}
    archive_body = {key: archive[key] for key in archive if key != "archive_payload_hash"}
    archive["archive_payload_hash"] = CanonicalHashV1.hash("p2a:promotion-snapshot-archive:v1", archive_body)
    body = {
        "schema_version": fields["schema_version"], "run": fields["run"], "archive": fields["archive"],
        "encoding_profile_hash": fields["encoding_profile_hash"],
    }
    fields["logical_commit_hash"] = CanonicalHashV1.hash("p2a:review-run-with-snapshot:v1", body)
    _recompute_transaction(records, record)


def _recompute_evidence_record(records, record):
    fields = record["payload"]["fields"]
    evidence = fields["evidence"]
    receipt = fields["receipt"]["fields"]
    profile_hash = fields["encoding_profile_hash"]
    evidence_hash = CanonicalHashV1.hash("p2a:review-evidence-payload:v1", evidence)
    receipt["evidence_id"] = evidence["fields"]["evidence_id"]
    receipt["evidence_payload_hash"] = evidence_hash
    receipt["encoding_profile_hash"] = profile_hash
    receipt["receipt_id"] = ReceiptIdentityBodyV1(
        "p2a.receipt-identity.v1", receipt["evidence_id"], evidence_hash, profile_hash
    ).receipt_id()
    body = {
        "schema_version": fields["schema_version"], "evidence": evidence, "receipt": fields["receipt"],
        "encoding_profile_hash": profile_hash,
    }
    fields["logical_commit_hash"] = CanonicalHashV1.hash("p2a:promoted-evidence-commit:v1", body)
    _recompute_transaction(records, record)


def test_canonical_hash_is_domain_separated_and_rejects_non_json_values():
    assert CanonicalHashV1.hash("p2a:test:a", {"value": 1}) != CanonicalHashV1.hash("p2a:test:b", {"value": 1})
    assert CanonicalHashV1.hash("p2a:test:a", {"value": 1}) == CanonicalHashV1.hash("p2a:test:a", {"value": 1})
    with pytest.raises(PromotionInfrastructureIntegrityError):
        CanonicalHashV1.hash("p2a:test", {1: "non-string-key"})
    with pytest.raises(PromotionInfrastructureIntegrityError):
        CanonicalHashV1.hash("p2a:test", {"bad": float("nan")})
    with pytest.raises(PromotionInfrastructureIntegrityError):
        CanonicalHashV1.hash("p2a:test", {"bad": object()})


def test_explicit_encoder_is_deterministic_and_preserves_p1_tuple_order():
    _episode, _outcome, _snapshot, finding, run, _evidence = _artifacts()
    encoder = P2CanonicalArtifactEncoderV1()
    assert encoder.encode(run) == encoder.encode(run)
    refs = (
        ReviewEvidenceRef("z", EvidenceSourceType.EPISODE_EVENT, EvidenceKind.STRUCTURAL),
        ReviewEvidenceRef("a", EvidenceSourceType.OUTCOME_OBSERVATION, EvidenceKind.EXPLICIT),
    )
    ordered = replace(finding, evidence_refs=refs)
    assert [ref["fields"]["ref_id"] for ref in encoder.encode(ordered)["fields"]["evidence_refs"]] == ["z", "a"]
    encoded_run = encoder.encode(run)
    assert "model" in encoded_run["fields"] and encoded_run["fields"]["model"] is None


def test_encoder_rejects_naive_datetime_unknown_enum_and_unknown_type():
    _episode, _outcome, _snapshot, finding, run, _evidence = _artifacts()
    encoder = P2CanonicalArtifactEncoderV1()
    with pytest.raises(PromotionInfrastructureIntegrityError):
        encoder.encode(replace(run, created_at=NOW.replace(tzinfo=None)))
    with pytest.raises(PromotionInfrastructureIntegrityError):
        encoder.encode(replace(finding, dimension="FUTURE_DIMENSION"))
    with pytest.raises(PromotionInfrastructureIntegrityError):
        encoder.encode(object())


def test_equivalent_timezone_datetimes_have_same_p2_encoding():
    _episode, _outcome, _snapshot, _finding, run, _evidence = _artifacts()
    encoder = P2CanonicalArtifactEncoderV1()
    plus_eight = NOW.astimezone(timezone(timedelta(hours=8)))
    assert encoder.encode(run) == encoder.encode(replace(run, created_at=plus_eight))


def test_profile_hash_is_content_addressed_and_registry_rejects_same_human_id_changed_content(tmp_path):
    store, profile, encoder = _profile_store(tmp_path)
    first = profile.type_profiles[0]
    changed = replace(profile, type_profiles=(replace(first, fields=tuple(reversed(first.fields))),) + profile.type_profiles[1:])
    assert canonical_artifact_encoding_profile_hash(changed) != canonical_artifact_encoding_profile_hash(profile)
    with pytest.raises(PromotionInfrastructureIntegrityError, match="encoder/profile hash mismatch"):
        store.record_encoding_profile(changed, encoder)


def test_snapshot_archive_uses_exact_p1_snapshot_and_run_logical_commit_replays(tmp_path):
    _episode, _outcome, snapshot, _finding, run, _evidence = _artifacts()
    store, _profile, encoder = _profile_store(tmp_path)
    commit = store.record_run_with_snapshot(run, snapshot, encoder)
    assert commit.archive.input_snapshot_hash == run.input_snapshot_hash
    assert commit.encoding_profile_hash == encoder.profile_hash
    assert store.require_archive(run)
    replayed = SyntheticP2PromotionStore(tmp_path / "promotion.jsonl")
    assert replayed.require_archive(run)


def test_legacy_run_without_p2_archive_is_explicitly_unavailable(tmp_path):
    _episode, _outcome, _snapshot, _finding, run, _evidence = _artifacts()
    store, _profile, _encoder = _profile_store(tmp_path)
    with pytest.raises(LegacyArchiveUnavailableError, match="LEGACY_ARCHIVE_UNAVAILABLE"):
        store.require_archive(run)


def test_archive_and_run_mismatch_are_rejected_on_replay(tmp_path):
    _episode, _outcome, snapshot, _finding, run, _evidence = _artifacts()
    store, _profile, encoder = _profile_store(tmp_path)
    store.record_run_with_snapshot(run, snapshot, encoder)
    path = tmp_path / "promotion.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    record, _commit = _transaction_records(records, P2_REVIEW_RUN_WITH_SNAPSHOT)
    record["payload"]["fields"]["archive"]["fields"]["review_run_id"] = "run:forged"
    _recompute_transaction(records, record)
    _write_canonical_records(path, records)
    with pytest.raises(PromotionInfrastructureIntegrityError):
        SyntheticP2PromotionStore(path)


def test_replay_rejects_missing_future_or_unknown_schema_fields_before_indexing(tmp_path):
    _episode, _outcome, snapshot, _finding, run, _evidence = _artifacts()
    store, _profile, encoder = _profile_store(tmp_path)
    store.record_run_with_snapshot(run, snapshot, encoder)
    record = dict(store.require_archive(run))
    # The indexed record is immutable; hostile payloads are rejected rather
    # than allowing a future/missing P1 field to be ignored on replay.
    encoded = json.loads(CanonicalHashV1.canonical_json_utf8(record))
    encoded["fields"]["run"]["fields"].pop("model")
    with pytest.raises(PromotionInfrastructureIntegrityError, match="fields mismatch"):
        store._apply_run_commit(encoded)
    encoded = json.loads(CanonicalHashV1.canonical_json_utf8(record))
    encoded["fields"]["archive"]["fields"]["schema_version"] = "future-archive-schema"
    with pytest.raises(PromotionInfrastructureIntegrityError, match="unknown nested"):
        store._apply_run_commit(encoded)


def test_truncated_or_unknown_logical_record_fails_closed(tmp_path):
    path = tmp_path / "broken.jsonl"
    path.write_text('{"schema_version":"p2a', encoding="utf-8")
    with pytest.raises(PromotionInfrastructureIntegrityError, match="malformed or truncated"):
        SyntheticP2PromotionStore(path)
    store, _profile, _encoder = _profile_store(tmp_path / "unknown")
    with pytest.raises(PromotionInfrastructureIntegrityError, match="unknown P2 operation"):
        store._apply_logical_operation("P2_UNKNOWN_OPERATION", {})


def test_synthetic_evidence_identity_receipt_and_retry_are_deterministic(tmp_path):
    _episode, _outcome, snapshot, finding, run, evidence = _artifacts()
    store, _profile, encoder = _profile_store(tmp_path)
    store.record_run_with_snapshot(run, snapshot, encoder)
    first_candidate = build_synthetic_candidate(evidence, finding, encoder)
    retry_candidate = build_synthetic_candidate(replace(evidence, created_at=evidence.created_at), finding, encoder)
    assert not hasattr(first_candidate, "receipt")
    assert first_candidate.evidence.evidence_id.startswith("evidence:p2a:")
    assert first_candidate.evidence.evidence_id == retry_candidate.evidence.evidence_id
    first = store.record_synthetic_evidence(first_candidate, encoder)
    retry = store.record_synthetic_evidence(retry_candidate, encoder)
    assert first.receipt.receipt_id.startswith("receipt:p2a:")
    assert first.receipt.receipt_id == retry.receipt.receipt_id
    assert first.logical_commit_hash == retry.logical_commit_hash
    changed_evidence = replace(first.evidence, confidence=Confidence.LOW)
    changed_payload_hash = CanonicalHashV1.hash("p2a:review-evidence-payload:v1", encoder.encode(changed_evidence))
    changed_receipt = PromotionReceiptV1(
        "p2a.promotion-receipt.v1",
        ReceiptIdentityBodyV1(
            "p2a.receipt-identity.v1", changed_evidence.evidence_id, changed_payload_hash, encoder.profile_hash
        ).receipt_id(),
        changed_evidence.evidence_id,
        changed_payload_hash,
        encoder.profile_hash,
    )
    changed_body = {
        "schema_version": "p2a.promoted-evidence-commit.v1", "evidence": encoder.encode(changed_evidence),
        "receipt": encoder.encode(changed_receipt), "encoding_profile_hash": encoder.profile_hash,
    }
    conflicting = P2PromotedEvidenceCommitV1(
        "p2a.promoted-evidence-commit.v1", changed_evidence, changed_receipt, encoder.profile_hash,
        CanonicalHashV1.hash("p2a:promoted-evidence-commit:v1", changed_body),
    )
    with pytest.raises(PromotionInfrastructureIntegrityError, match="identity is not derived"):
        store._apply_evidence_commit(encoder.encode(conflicting))


def test_receipt_payload_mismatch_and_raw_evidence_never_become_p2_valid(tmp_path):
    _episode, _outcome, snapshot, finding, run, evidence = _artifacts()
    store, _profile, encoder = _profile_store(tmp_path)
    store.record_run_with_snapshot(run, snapshot, encoder)
    commit = store.record_synthetic_evidence(build_synthetic_candidate(evidence, finding, encoder), encoder)
    broken = replace(commit, receipt=replace(commit.receipt, evidence_payload_hash="sha256:forged"))
    with pytest.raises(PromotionInfrastructureIntegrityError, match="receipt"):
        store._apply_evidence_commit(encoder.encode(broken))
    reader = P2ValidatedEvidenceReader(store)
    assert reader.status_for_raw_evidence(evidence) == NOT_P2_VALID
    assert reader.validated_evidence() == ()


def test_evidence_commit_requires_snapshot_backed_run_and_contained_finding(tmp_path):
    _episode, _outcome, snapshot, finding, run, evidence = _artifacts()
    store, _profile, encoder = _profile_store(tmp_path)
    orphan = build_synthetic_candidate(evidence, finding, encoder)
    with pytest.raises(PromotionInfrastructureIntegrityError, match="without exact P2 archive"):
        store.record_synthetic_evidence(orphan, encoder)
    store.record_run_with_snapshot(run, snapshot, encoder)
    foreign = build_synthetic_candidate(replace(evidence, source_finding_id="finding:foreign"), replace(finding, finding_id="finding:foreign"), encoder)
    with pytest.raises(PromotionInfrastructureIntegrityError, match="not contained"):
        store.record_synthetic_evidence(foreign, encoder)


def test_production_gate_and_production_store_are_permanently_reject_all(tmp_path):
    _episode, _outcome, _snapshot, finding, _run, evidence = _artifacts()
    production, _profile, encoder = _profile_store(tmp_path, test_only=False)
    assert ProductionPromotionGateV1().evaluate(PromotionCommand(finding.finding_id, "future-looking-rule")).reason == PRODUCTION_RULE_DISABLED
    commit = build_synthetic_candidate(evidence, finding, encoder)
    with pytest.raises(AttributeError):
        production.record_synthetic_evidence(commit, encoder)
    assert not hasattr(P2PromotionStore, "record_synthetic_evidence")
    assert P2ValidatedEvidenceReader(production).validated_evidence() == ()


def test_production_store_has_no_caller_controlled_test_only_mode(tmp_path):
    with pytest.raises(TypeError):
        P2PromotionStore(tmp_path / "production.jsonl", test_only=True)
    synthetic = SyntheticP2PromotionStore(tmp_path / "synthetic.jsonl")
    assert synthetic.root == "synthetic-test"
    assert P2PromotionStore(tmp_path / "production.jsonl").root == "production"


def test_test_fixture_record_cannot_cross_to_production_root(tmp_path):
    _episode, _outcome, snapshot, finding, run, evidence = _artifacts()
    test_store, _profile, encoder = _profile_store(tmp_path / "test", test_only=True)
    test_store.record_run_with_snapshot(run, snapshot, encoder)
    test_store.record_synthetic_evidence(build_synthetic_candidate(evidence, finding, encoder), encoder)
    copied = tmp_path / "production" / "promotion.jsonl"
    copied.parent.mkdir()
    copied.write_bytes((tmp_path / "test" / "promotion.jsonl").read_bytes())
    with pytest.raises(PromotionInfrastructureIntegrityError, match="root mismatch"):
        P2PromotionStore(copied)


def test_structurally_perfect_rewritten_fixture_is_rejected_by_production_store(tmp_path):
    _episode, _outcome, snapshot, finding, run, evidence = _artifacts()
    test_store, _profile, encoder = _profile_store(tmp_path / "test", test_only=True)
    test_store.record_run_with_snapshot(run, snapshot, encoder)
    test_store.record_synthetic_evidence(build_synthetic_candidate(evidence, finding, encoder), encoder)
    production_path = tmp_path / "production" / "promotion.jsonl"
    production_path.parent.mkdir()
    rewritten = []
    for line in (tmp_path / "test" / "promotion.jsonl").read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        record["persistence_root"] = "production"
        rewritten.append(record)
    for record in rewritten:
        if record["record_type"] == P2_TX_PREPARE:
            _recompute_transaction(rewritten, record)
    _write_canonical_records(production_path, rewritten)
    with pytest.raises(PromotionInfrastructureIntegrityError, match=PRODUCTION_RULE_DISABLED):
        P2PromotionStore(production_path)


def test_profile_requires_exact_schema_and_deep_snapshots_caller_sequences(tmp_path):
    profile = default_encoding_profile_v1()
    entries = list(profile.type_profiles)
    entries[0] = CanonicalTypeProfileV1(entries[0].type_name, "p2a.promotion-artifact.v1", list(entries[0].fields))
    wrong = FrozenCanonicalArtifactEncodingProfileV1(profile.profile_id, profile.profile_version, entries)
    entries.clear()
    assert len(wrong.type_profiles) == len(default_encoding_profile_v1().type_profiles)
    with pytest.raises(PromotionInfrastructureIntegrityError, match="schema or fields"):
        P2CanonicalArtifactEncoderV1(wrong)

    store, _profile, encoder = _profile_store(tmp_path)
    payload = json.loads(CanonicalHashV1.canonical_json_utf8(encoder.encode(default_encoding_profile_v1())))
    entry = payload["fields"]["type_profiles"][0]["fields"]
    entry["schema"] = "p2a.promotion-artifact.v1"
    with pytest.raises(PromotionInfrastructureIntegrityError, match="schema or field"):
        SyntheticP2PromotionStore(tmp_path / "wrong-profile.jsonl")._apply_logical_operation(
            P2_CANONICAL_ARTIFACT_ENCODING_PROFILE, payload
        )


def test_public_archive_and_evidence_views_are_deeply_immutable_and_detached(tmp_path):
    _episode, _outcome, snapshot, finding, run, evidence = _artifacts()
    store, _profile, encoder = _profile_store(tmp_path)
    store.record_run_with_snapshot(run, snapshot, encoder)
    commit = store.record_synthetic_evidence(build_synthetic_candidate(evidence, finding, encoder), encoder)
    archive = store.require_archive(run)
    with pytest.raises(TypeError):
        archive["fields"]["archive"]["fields"]["episode_id"] = "episode:forged"
    evidence_view = store.evidence_commits[0]
    with pytest.raises(TypeError):
        evidence_view["fields"]["receipt"]["fields"]["receipt_id"] = "receipt:p2a:" + "0" * 64
    assert store.require_archive(run)["fields"]["archive"]["fields"]["episode_id"] == run.episode_id
    assert store.evidence_commits[0]["fields"]["evidence"]["fields"]["evidence_id"] == commit.evidence.evidence_id
    replayed = SyntheticP2PromotionStore(tmp_path / "promotion.jsonl")
    assert replayed.require_archive(run) == store.require_archive(run)


@pytest.mark.parametrize("attack", ("empty", "noncanonical", "schema", "episode"))
def test_disk_replay_rejects_rehashed_invalid_p1_snapshot(tmp_path, attack):
    _episode, _outcome, snapshot, _finding, run, _evidence = _artifacts()
    store, _profile, encoder = _profile_store(tmp_path)
    store.record_run_with_snapshot(run, snapshot, encoder)
    path = tmp_path / "promotion.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    snapshot_payload = snapshot.canonical_payload()
    if attack == "empty":
        raw = b"{}"
    elif attack == "noncanonical":
        raw = json.dumps(json.loads(_canonical_json(snapshot_payload)), ensure_ascii=False, indent=2).encode("utf-8")
    else:
        altered = json.loads(_canonical_json(snapshot_payload))
        if attack == "schema":
            altered["schema_version"] = "p1d.review-input-snapshot.future"
        else:
            altered["episode"]["fields"]["episode_id"] = "episode:forged:root"
        raw = _canonical_json(altered).encode("utf-8")
    run_prepare, _commit = _transaction_records(records, P2_REVIEW_RUN_WITH_SNAPSHOT)
    _recompute_run_record(records, run_prepare, raw)
    _write_canonical_records(path, records)
    with pytest.raises(PromotionInfrastructureIntegrityError):
        SyntheticP2PromotionStore(path)


def test_disk_replay_rejects_rehashed_evidence_identity_and_timestamp_attacks(tmp_path):
    _episode, _outcome, snapshot, finding, run, evidence = _artifacts()
    store, _profile, encoder = _profile_store(tmp_path)
    store.record_run_with_snapshot(run, snapshot, encoder)
    store.record_synthetic_evidence(build_synthetic_candidate(evidence, finding, encoder), encoder)
    source = tmp_path / "promotion.jsonl"
    base = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]

    for name, mutate in (
        ("zero-id", lambda fields: fields.__setitem__("evidence_id", "evidence:p2a:" + "0" * 64)),
        ("timestamp", lambda fields: fields.__setitem__("created_at", (NOW + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"))),
        ("proposition", lambda fields: fields["proposition"]["fields"].__setitem__("statement", "Forged but local-looking statement.")),
    ):
        records = json.loads(json.dumps(base))
        evidence_prepare, _commit = _transaction_records(records, P2_PROMOTED_EVIDENCE_COMMIT)
        evidence_fields = evidence_prepare["payload"]["fields"]["evidence"]["fields"]
        mutate(evidence_fields)
        _recompute_evidence_record(records, evidence_prepare)
        attack_path = tmp_path / f"{name}.jsonl"
        _write_canonical_records(attack_path, records)
        with pytest.raises(PromotionInfrastructureIntegrityError):
            SyntheticP2PromotionStore(attack_path)


def test_disk_replay_rejects_rehashed_outcome_and_fact_envelope_lineage_attacks(tmp_path):
    episode, outcome, _snapshot, finding, _run, evidence = _artifacts()
    event_ref = EpisodeEventRef("HOST_OUTPUT:event:1", EpisodeEventKind.HOST_OUTPUT, source_event_id="event:1", observed_at=NOW)
    attached_episode = replace(episode, event_refs=(event_ref,))
    snapshot = ReviewInputSnapshot(
        attached_episode,
        (outcome,),
        {
            (EvidenceSourceType.EPISODE_EVENT, event_ref.ref_id): event_ref,
            (EvidenceSourceType.OUTCOME_OBSERVATION, outcome.observation_id): outcome,
        },
    )
    run = replace(
        _run,
        episode_id=attached_episode.episode_id,
        input_snapshot_hash="sha256:" + hashlib.sha256(_canonical_json(snapshot.canonical_payload()).encode("utf-8")).hexdigest(),
        findings=(replace(finding, episode_id=attached_episode.episode_id),),
    )
    store, _profile, encoder = _profile_store(tmp_path)
    store.record_run_with_snapshot(run, snapshot, encoder)
    path = tmp_path / "promotion.jsonl"
    base = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    for name, mutate in (
        ("outcome", lambda value: value["outcomes"][0]["fields"].__setitem__("target_episode_id", "episode:forged:target")),
        ("outcome-kind", lambda value: value["outcomes"][0]["fields"].__setitem__("kind", "EXPLICIT_ACKNOWLEDGEMENT")),
        ("fact", lambda value: value["fact_envelopes"][0]["payload"]["fields"].__setitem__("ref_id", "HOST_OUTPUT:forged")),
    ):
        records = json.loads(json.dumps(base))
        p1_value = json.loads(_canonical_json(snapshot.canonical_payload()))
        mutate(p1_value)
        run_prepare, _commit = _transaction_records(records, P2_REVIEW_RUN_WITH_SNAPSHOT)
        _recompute_run_record(records, run_prepare, _canonical_json(p1_value).encode("utf-8"))
        attack_path = tmp_path / f"{name}-lineage.jsonl"
        _write_canonical_records(attack_path, records)
        with pytest.raises(PromotionInfrastructureIntegrityError):
            SyntheticP2PromotionStore(attack_path)


def test_replay_accepts_real_p1_snapshot_with_non_derived_ids_and_attached_host_facts(tmp_path):
    snapshot, run = _attached_host_artifacts()
    store, _profile, encoder = _profile_store(tmp_path)
    stored = store.record_run_with_snapshot(run, snapshot, encoder)
    assert stored.run is run

    replayed = SyntheticP2PromotionStore(tmp_path / "promotion.jsonl")
    archived = replayed.require_archive(run)
    assert archived["fields"]["archive"]["fields"]["episode_id"] == run.episode_id


@pytest.mark.parametrize(
    "name",
    ("host-stage", "host-revision", "host-event", "trace-event"),
)
def test_disk_replay_rejects_rehashed_unattached_host_and_trace_facts(tmp_path, name):
    snapshot, run = _attached_host_artifacts()
    store, _profile, encoder = _profile_store(tmp_path)
    store.record_run_with_snapshot(run, snapshot, encoder)
    path = tmp_path / "promotion.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    p1_value = json.loads(_canonical_json(snapshot.canonical_payload()))
    # Canonical fact envelopes are sorted by source_type then ref_id:
    # BEHAVIOR_TRACE, EPISODE_EVENT, HOST_RESULT, OUTCOME_OBSERVATION.
    indexes = {item["source_type"]: index for index, item in enumerate(p1_value["fact_envelopes"])}
    if name.startswith("host"):
        host = p1_value["fact_envelopes"][indexes["HOST_RESULT"]]
        if name == "host-stage":
            host["payload"]["fields"]["stage"] = "DISPATCH"
        elif name == "host-revision":
            host["payload"]["fields"]["revision"] = 8
        else:
            host["payload"]["fields"]["trace"]["fields"]["event_id"] = "event:foreign"
    else:
        p1_value["fact_envelopes"][indexes["BEHAVIOR_TRACE"]]["payload"]["fields"]["event_id"] = "event:foreign"
    run_prepare, _commit = _transaction_records(records, P2_REVIEW_RUN_WITH_SNAPSHOT)
    _recompute_run_record(records, run_prepare, _canonical_json(p1_value).encode("utf-8"))
    attack_path = tmp_path / f"{name}.jsonl"
    _write_canonical_records(attack_path, records)
    with pytest.raises(PromotionInfrastructureIntegrityError):
        SyntheticP2PromotionStore(attack_path)


def test_failed_append_never_publishes_profile_run_or_evidence(monkeypatch, tmp_path):
    _episode, _outcome, snapshot, finding, run, evidence = _artifacts()
    profile = default_encoding_profile_v1()
    encoder = P2CanonicalArtifactEncoderV1(profile)
    store = SyntheticP2PromotionStore(tmp_path / "failed.jsonl")

    def fail_append(_record, *, stage):
        raise OSError("simulated append failure")

    monkeypatch.setattr(store, "_append", fail_append)
    with pytest.raises(OSError):
        store.record_encoding_profile(profile, encoder)
    assert not store._profiles
    # Register once through a separate durable store, then repeat for Run and Evidence.
    durable, _profile, durable_encoder = _profile_store(tmp_path / "durable")
    run_store = SyntheticP2PromotionStore(tmp_path / "run-failed.jsonl")
    run_store._profiles = dict(durable._profiles)
    run_store._profile_objects = dict(durable._profile_objects)
    run_store._profile_humans = dict(durable._profile_humans)
    monkeypatch.setattr(run_store, "_append", fail_append)
    with pytest.raises(OSError):
        run_store.record_run_with_snapshot(run, snapshot, durable_encoder)
    with pytest.raises(LegacyArchiveUnavailableError):
        run_store.require_archive(run)

    durable.record_run_with_snapshot(run, snapshot, durable_encoder)
    commit = build_synthetic_candidate(evidence, finding, durable_encoder)
    monkeypatch.setattr(durable, "_append", fail_append)
    with pytest.raises(OSError):
        durable.record_synthetic_evidence(commit, durable_encoder)
    assert durable.evidence_commits == ()


@pytest.mark.parametrize("phase", ("write", "flush", "sync"))
def test_persistence_phase_failure_rolls_back_disk_and_authority(monkeypatch, tmp_path, phase):
    _episode, _outcome, snapshot, finding, run, evidence = _artifacts()

    def inject_failure(store):
        if phase == "write":
            def partial_write(handle, encoded):
                handle.write(encoded[: max(1, len(encoded) // 2)])
                raise OSError("simulated partial write failure")

            monkeypatch.setattr(store, "_write_append_bytes", partial_write)
        elif phase == "flush":
            def failed_flush(handle):
                handle.flush()
                raise OSError("simulated flush failure")

            monkeypatch.setattr(store, "_flush_append_handle", failed_flush)
        else:
            def failed_sync(handle):
                __import__("os").fsync(handle.fileno())
                raise OSError("simulated durability failure")

            monkeypatch.setattr(store, "_sync_append_handle", failed_sync)

    profile_path = tmp_path / f"profile-{phase}.jsonl"
    profile = default_encoding_profile_v1()
    encoder = P2CanonicalArtifactEncoderV1(profile)
    profile_store = SyntheticP2PromotionStore(profile_path)
    inject_failure(profile_store)
    with pytest.raises(OSError):
        profile_store.record_encoding_profile(profile, encoder)
    assert not profile_store._profiles
    assert SyntheticP2PromotionStore(profile_path)._profiles == {}

    run_path = tmp_path / f"run-{phase}.jsonl"
    run_store = SyntheticP2PromotionStore(run_path)
    run_store.record_encoding_profile(profile, encoder)
    inject_failure(run_store)
    with pytest.raises(OSError):
        run_store.record_run_with_snapshot(run, snapshot, encoder)
    with pytest.raises(LegacyArchiveUnavailableError):
        run_store.require_archive(run)
    with pytest.raises(LegacyArchiveUnavailableError):
        SyntheticP2PromotionStore(run_path).require_archive(run)

    evidence_path = tmp_path / f"evidence-{phase}.jsonl"
    evidence_store = SyntheticP2PromotionStore(evidence_path)
    evidence_store.record_encoding_profile(profile, encoder)
    evidence_store.record_run_with_snapshot(run, snapshot, encoder)
    commit = build_synthetic_candidate(evidence, finding, encoder)
    inject_failure(evidence_store)
    with pytest.raises(OSError):
        evidence_store.record_synthetic_evidence(commit, encoder)
    assert evidence_store.evidence_commits == ()
    assert SyntheticP2PromotionStore(evidence_path).evidence_commits == ()


@pytest.mark.parametrize("family", ("profile", "run", "evidence"))
def test_prepare_durability_and_cleanup_failure_never_becomes_authoritative(monkeypatch, tmp_path, family):
    """A residual PREPARE proves nothing, even if it is a complete JSONL line."""
    _episode, _outcome, snapshot, finding, run, evidence = _artifacts()
    profile = default_encoding_profile_v1()
    encoder = P2CanonicalArtifactEncoderV1(profile)
    path = tmp_path / f"prepare-{family}.jsonl"
    store = SyntheticP2PromotionStore(path)

    if family in ("run", "evidence"):
        store.record_encoding_profile(profile, encoder)
    if family == "evidence":
        store.record_run_with_snapshot(run, snapshot, encoder)

    _make_truncate_fail_for_store(monkeypatch, store)

    def failed_prepare_sync(_handle):
        raise OSError("simulated PREPARE durability failure")

    monkeypatch.setattr(store, "_sync_append_handle", failed_prepare_sync)
    if family == "profile":
        action = lambda: store.record_encoding_profile(profile, encoder)
    elif family == "run":
        action = lambda: store.record_run_with_snapshot(run, snapshot, encoder)
    else:
        candidate = build_synthetic_candidate(evidence, finding, encoder)
        action = lambda: store.record_synthetic_evidence(candidate, encoder)

    with pytest.raises(OSError, match="PREPARE durability"):
        action()

    # The complete residual PREPARE may still be present, but it has no COMMIT
    # and therefore cannot publish anything either live or after a reopen.
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert records[-1]["record_type"] == P2_TX_PREPARE
    assert not any(
        record["record_type"] == P2_TX_COMMIT
        and record["transaction_id"] == records[-1]["transaction_id"]
        for record in records
    )
    reopened = SyntheticP2PromotionStore(path)
    if family == "profile":
        assert not store._profiles
        assert not reopened._profiles
    elif family == "run":
        with pytest.raises(LegacyArchiveUnavailableError):
            store.require_archive(run)
        with pytest.raises(LegacyArchiveUnavailableError):
            reopened.require_archive(run)
    else:
        assert store.evidence_commits == ()
        assert reopened.evidence_commits == ()


def test_replay_requires_matching_prepare_and_commit_markers(tmp_path):
    """Orphan, malformed, conflicting, or mismatched COMMITs fail closed."""
    profile = default_encoding_profile_v1()
    encoder = P2CanonicalArtifactEncoderV1(profile)
    store = SyntheticP2PromotionStore(tmp_path / "seed.jsonl")
    payload = encoder.encode(profile)
    prepare = json.loads(CanonicalHashV1.canonical_json_utf8(store._prepare(P2_CANONICAL_ARTIFACT_ENCODING_PROFILE, payload)))
    commit = json.loads(CanonicalHashV1.canonical_json_utf8(store._commit(prepare)))

    prepare_only = tmp_path / "prepare-only.jsonl"
    _write_canonical_records(prepare_only, (prepare,))
    assert SyntheticP2PromotionStore(prepare_only)._profiles == {}

    wrong_transaction = json.loads(json.dumps(prepare))
    wrong_transaction["transaction_id"] = "tx:p2a:" + "0" * 64
    body = {key: wrong_transaction[key] for key in wrong_transaction if key != "prepare_hash"}
    wrong_transaction["prepare_hash"] = CanonicalHashV1.hash("p2a:persistence-transaction-prepare:v1", body)
    wrong_transaction_path = tmp_path / "wrong-transaction.jsonl"
    _write_canonical_records(wrong_transaction_path, (wrong_transaction,))
    with pytest.raises(PromotionInfrastructureIntegrityError, match="transaction identity mismatch"):
        SyntheticP2PromotionStore(wrong_transaction_path)

    orphan = tmp_path / "orphan-commit.jsonl"
    _write_canonical_records(orphan, (commit,))
    with pytest.raises(PromotionInfrastructureIntegrityError, match="no prior PREPARE"):
        SyntheticP2PromotionStore(orphan)

    wrong_reference = json.loads(json.dumps(commit))
    wrong_reference["prepare_hash"] = "sha256:" + "0" * 64
    _recompute_commit(wrong_reference, prepare)
    wrong_reference["prepare_hash"] = "sha256:" + "0" * 64
    body = {key: wrong_reference[key] for key in wrong_reference if key != "commit_hash"}
    wrong_reference["commit_hash"] = CanonicalHashV1.hash("p2a:persistence-transaction-commit:v1", body)
    wrong_path = tmp_path / "wrong-reference.jsonl"
    _write_canonical_records(wrong_path, (prepare, wrong_reference))
    with pytest.raises(PromotionInfrastructureIntegrityError, match="wrong PREPARE"):
        SyntheticP2PromotionStore(wrong_path)

    wrong_commit_hash = json.loads(json.dumps(commit))
    wrong_commit_hash["commit_hash"] = "sha256:" + "0" * 64
    wrong_commit_hash_path = tmp_path / "wrong-commit-hash.jsonl"
    _write_canonical_records(wrong_commit_hash_path, (prepare, wrong_commit_hash))
    with pytest.raises(PromotionInfrastructureIntegrityError, match="COMMIT hash mismatch"):
        SyntheticP2PromotionStore(wrong_commit_hash_path)

    conflicting = json.loads(json.dumps(commit))
    conflicting["prepare_hash"] = "sha256:" + "1" * 64
    body = {key: conflicting[key] for key in conflicting if key != "commit_hash"}
    conflicting["commit_hash"] = CanonicalHashV1.hash("p2a:persistence-transaction-commit:v1", body)
    conflict_path = tmp_path / "conflicting-commit.jsonl"
    _write_canonical_records(conflict_path, (prepare, commit, conflicting))
    with pytest.raises(PromotionInfrastructureIntegrityError, match="conflicts with different COMMIT"):
        SyntheticP2PromotionStore(conflict_path)

    malformed = json.loads(json.dumps(commit))
    malformed.pop("prepare_hash")
    malformed_path = tmp_path / "malformed-commit.jsonl"
    _write_canonical_records(malformed_path, (prepare, malformed))
    with pytest.raises(PromotionInfrastructureIntegrityError, match="COMMIT record shape"):
        SyntheticP2PromotionStore(malformed_path)

    wrong_root = json.loads(json.dumps(commit))
    wrong_root["persistence_root"] = "production"
    body = {key: wrong_root[key] for key in wrong_root if key != "commit_hash"}
    wrong_root["commit_hash"] = CanonicalHashV1.hash("p2a:persistence-transaction-commit:v1", body)
    wrong_root_path = tmp_path / "wrong-root-commit.jsonl"
    _write_canonical_records(wrong_root_path, (prepare, wrong_root))
    with pytest.raises(PromotionInfrastructureIntegrityError, match="schema/root mismatch"):
        SyntheticP2PromotionStore(wrong_root_path)

    truncated = tmp_path / "truncated-commit.jsonl"
    truncated.write_bytes(CanonicalHashV1.canonical_json_utf8(prepare) + b"\n" + b'{"record_type":"P2_TX_COMMIT"')
    with pytest.raises(PromotionInfrastructureIntegrityError, match="malformed or truncated"):
        SyntheticP2PromotionStore(truncated)


@pytest.mark.parametrize("phase", ("write", "flush", "sync"))
def test_commit_stage_failures_are_explicitly_indeterminate(monkeypatch, tmp_path, phase):
    """The final marker never reports a potentially durable outcome as NOT_COMMITTED."""
    profile = default_encoding_profile_v1()
    encoder = P2CanonicalArtifactEncoderV1(profile)
    path = tmp_path / f"commit-{phase}.jsonl"
    store = SyntheticP2PromotionStore(path)
    writes = {"count": 0}

    if phase == "write":
        original = store._write_append_bytes

        def fail_commit_write(handle, encoded):
            writes["count"] += 1
            if writes["count"] == 2:
                handle.write(encoded[: max(1, len(encoded) // 2)])
                raise OSError("simulated COMMIT write failure")
            return original(handle, encoded)

        monkeypatch.setattr(store, "_write_append_bytes", fail_commit_write)
    elif phase == "flush":
        original = store._flush_append_handle

        def fail_commit_flush(handle):
            writes["count"] += 1
            original(handle)
            if writes["count"] == 2:
                raise OSError("simulated COMMIT flush failure")

        monkeypatch.setattr(store, "_flush_append_handle", fail_commit_flush)
    else:
        original = store._sync_append_handle

        def fail_commit_sync(handle):
            writes["count"] += 1
            original(handle)
            if writes["count"] == 2:
                raise OSError("simulated COMMIT sync failure")

        monkeypatch.setattr(store, "_sync_append_handle", fail_commit_sync)

    with pytest.raises(CommitOutcomeIndeterminateError, match=COMMIT_OUTCOME_INDETERMINATE):
        store.record_encoding_profile(profile, encoder)
    assert not store._profiles
    assert SyntheticP2PromotionStore(path)._profiles == {}


def test_commit_sync_cleanup_failure_is_indeterminate_and_replayable_if_marker_survives(monkeypatch, tmp_path):
    """A surviving final marker is never silently reported as definitely absent."""
    profile = default_encoding_profile_v1()
    encoder = P2CanonicalArtifactEncoderV1(profile)
    path = tmp_path / "commit-indeterminate.jsonl"
    store = SyntheticP2PromotionStore(path)
    syncs = {"count": 0}
    original_sync = store._sync_append_handle

    def fail_commit_sync(handle):
        syncs["count"] += 1
        original_sync(handle)
        if syncs["count"] == 2:
            raise OSError("simulated final COMMIT sync failure")

    _make_truncate_fail_for_store(monkeypatch, store)
    monkeypatch.setattr(store, "_sync_append_handle", fail_commit_sync)
    with pytest.raises(CommitOutcomeIndeterminateError, match=COMMIT_OUTCOME_INDETERMINATE):
        store.record_encoding_profile(profile, encoder)
    assert not store._profiles

    # The caller received an explicit indeterminate outcome.  If a complete
    # marker survived, replay may materialize it; this is not silent authority.
    reopened = SyntheticP2PromotionStore(path)
    assert reopened._profiles
