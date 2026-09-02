"""P1d Review/ReviewEvidence implementation tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from iris_memory.cognitive.episode import (
    Episode,
    EpisodeEventKind,
    EpisodeEventRef,
    EpisodeState,
    make_episode_id,
)
from iris_memory.cognitive.outcome import (
    OutcomeKind,
    OutcomeObservation,
    make_outcome_observation_id,
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
    ReviewDimension,
    ReviewEligibility,
    ReviewEvidenceRef,
    ReviewEvidence,
    ReviewFinding,
    LocalEvidenceProposition,
    ReviewRun,
    ReviewStatus,
)
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
from iris_memory.cognitive.review_service import (
    DeterministicReviewEngine,
    ReviewFactResolver,
    ReviewInputSnapshot,
    StructuredModelReviewEngine,
    compute_input_snapshot_hash,
    evaluate_review_eligibility,
    model_version_fallback,
    promote_finding_to_evidence,
    review_episode,
    validate_model_provenance,
    validate_proposition_safe,
)
from iris_memory.cognitive.review_store import (
    AppendOnlyReviewStore,
    InMemoryReviewStore,
    ReviewStoreIntegrityError,
    _evidence_to_dict,
)


_NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def _episode(state=EpisodeState.FINALIZED, refs=()) -> Episode:
    return Episode(
        episode_id=make_episode_id("g1", "qq:1"),
        scope_id="g1",
        state=state,
        root_event_id="qq:1",
        opened_at=_NOW,
        last_activity_at=_NOW,
        event_refs=tuple(refs),
        provenance=("test",),
    )


def _host_ref(
    *,
    source_event_id: str = "qq:1",
    trace_id: str = "trace:1",
    ref_id: str | None = None,
) -> EpisodeEventRef:
    return EpisodeEventRef(
        ref_id=ref_id or f"HOST_OUTPUT:{source_event_id}:{trace_id}:host:1",
        kind=EpisodeEventKind.HOST_OUTPUT,
        source_event_id=source_event_id,
        trace_id=trace_id,
        execution_record_id=f"{trace_id}:1",
        observed_at=_NOW,
    )


def _correction_outcome() -> OutcomeObservation:
    return OutcomeObservation(
        observation_id=make_outcome_observation_id(
            "ep:1",
            OutcomeKind.EXPLICIT_CORRECTION,
            source_event_id="qq:2",
        ),
        target_episode_id=make_episode_id("g1", "qq:1"),
        kind=OutcomeKind.EXPLICIT_CORRECTION,
        observed_at=_NOW,
        source_event_id="qq:2",
    )


def _host_result(*, legacy_fallthrough: bool = True) -> HostResult:
    return HostResult(
        legacy_fallthrough=legacy_fallthrough,
        output_generated=True,
        output_nonempty=True,
        dispatch_observed=True,
        output_state=OutputState.OUTPUT_READY,
        producer=OutputProducer.LEGACY_HOST,
        applied_enforcement=GroundingEnforcement.NOT_APPLIED,
    )


def _behavior_trace(*, event_id: str = "qq:1", trace_id: str = "trace:1") -> BehaviorTrace:
    trace = BehaviorTrace(
        event_id=event_id,
        trigger=TriggerDecision(should_start_loop=True, reason="test", score=1),
        participation=None,
        intent=None,
        grounding=None,
        exit_reason=None,
    )
    # The frozen production contract mints this id.  Test fixtures pin it so
    # independently built EpisodeEventRef values can model the same record.
    object.__setattr__(trace, "trace_id", trace_id)
    return trace


def _host_envelopes(host_ref: EpisodeEventRef | None = None, *, legacy_fallthrough: bool = True):
    host_ref = host_ref or _host_ref()
    trace = _behavior_trace(event_id=host_ref.source_event_id, trace_id=host_ref.trace_id)
    return {
        (EvidenceSourceType.HOST_RESULT, host_ref.ref_id): BehaviorExecutionRecord(
            trace=trace,
            host_result=_host_result(legacy_fallthrough=legacy_fallthrough),
            comparison=ShadowComparison(
                cognitive_would_participate=True,
                cognitive_would_reply=True,
                cognitive_exit_reason=None,
                legacy_replied=True,
                legacy_output_present=True,
                divergence=DivergenceType.MATCH_REPLY,
            ),
            stage=TraceStage.HOST_OUTPUT,
            revision=1,
            updated_at=_NOW,
        )
    }


def test_eligibility_open_defers_and_finalized_correction_reviews():
    assert evaluate_review_eligibility(_episode(state=EpisodeState.OPEN), ()).decision is ReviewEligibility.DEFER
    decision = evaluate_review_eligibility(
        _episode(refs=(_host_ref(),)),
        (_correction_outcome(),),
    )
    assert decision.decision is ReviewEligibility.REVIEW
    trivial = evaluate_review_eligibility(_episode(), ())
    assert trivial.decision is ReviewEligibility.SKIP


def test_deterministic_engine_generates_correction_finding():
    episode = _episode(refs=(_host_ref(),))
    outcomes = (_correction_outcome(),)
    findings = DeterministicReviewEngine().generate_findings(episode, outcomes)
    assert any(f.dimension is ReviewDimension.ATTRIBUTION for f in findings)
    assert all(f.claim == "User explicitly contradicted the Host output." for f in findings)
    assert all(f.finding_type is FindingType.LOCAL_OBSERVATION for f in findings)


def test_correction_is_finding_only_not_grounding_evidence():
    episode = _episode(refs=(_host_ref(),))
    outcome = _correction_outcome()
    store = InMemoryReviewStore()
    run = review_episode(episode, (outcome,), store, fact_envelopes=_host_envelopes())
    assert run is not None
    assert run.status is ReviewStatus.COMPLETED
    assert run.findings[0].dimension is ReviewDimension.ATTRIBUTION
    assert store.list_evidence_for_episode(episode.episode_id) == ()


def test_policy_proposition_rejected():
    from iris_memory.cognitive.review import LocalEvidenceProposition

    prop = LocalEvidenceProposition(
        ReviewDimension.GROUNDING,
        behavior_refs=("b1",),
        observation_refs=("o1",),
        statement="下次应该查工具。",
    )
    with pytest.raises(ValueError):
        validate_proposition_safe(prop)


def test_policy_leakage_phrases_rejected():
    from iris_memory.cognitive.review import LocalEvidenceProposition

    bad_statements = [
        "Using a tool is the safer general approach.",
        "User prefers concise answers.",
        "这个用户喜欢玩笑。",
        "以后别回答这种问题。",
        "Always use tools for astronomy.",
    ]
    for statement in bad_statements:
        prop = LocalEvidenceProposition(
            ReviewDimension.GROUNDING,
            behavior_refs=("b1",),
            observation_refs=("o1",),
            statement=statement,
        )
        with pytest.raises(ValueError):
            validate_proposition_safe(prop)


def test_model_provenance_required():
    with pytest.raises(ValueError):
        validate_model_provenance(
            InterpretationProducer.MODEL,
            model=None,
            model_version=None,
            prompt_version=None,
            schema_version="1",
        )
    validate_model_provenance(
        InterpretationProducer.MODEL,
        model="m",
        model_version="m-v1",
        prompt_version="p1",
        schema_version="1",
    )
    with pytest.raises(ValueError):
        validate_model_provenance(
            InterpretationProducer.DETERMINISTIC,
            model="m",
            model_version=None,
            prompt_version=None,
            schema_version="1",
        )


def test_input_hash_stable_and_sensitive():
    episode = _episode(refs=(_host_ref(),))
    outcome = _correction_outcome()
    h1 = compute_input_snapshot_hash(episode, [outcome])
    h2 = compute_input_snapshot_hash(episode, [outcome])
    assert h1 == h2
    other = OutcomeObservation(
        observation_id=make_outcome_observation_id("ep:1", OutcomeKind.EXPLICIT_ACKNOWLEDGEMENT, source_event_id="qq:2"),
        target_episode_id=episode.episode_id,
        kind=OutcomeKind.EXPLICIT_ACKNOWLEDGEMENT,
        observed_at=_NOW,
        source_event_id="qq:2",
    )
    assert compute_input_snapshot_hash(episode, [outcome, other]) != h1


def test_in_memory_review_store_roundtrip():
    store = InMemoryReviewStore()
    from iris_memory.cognitive.review import ReviewRun

    run = ReviewRun(
        review_run_id="run:1",
        episode_id="ep:1",
        status=ReviewStatus.COMPLETED,
        input_snapshot_hash="sha256:x",
    )
    store.record_review_run(run)
    assert store.get_review_run("run:1") is run
    assert store.list_review_runs_for_episode("ep:1") == (run,)


def test_append_only_review_store_replay_is_idempotent(tmp_path):
    path = tmp_path / "reviews.jsonl"
    run = ReviewRun(
        review_run_id="run:1",
        episode_id="ep:1",
        status=ReviewStatus.COMPLETED,
        input_snapshot_hash="sha256:x",
    )
    store = AppendOnlyReviewStore(path)
    store.record_review_run(run)
    assert path.read_text(encoding="utf-8").count('"run:1"') == 1

    reopened = AppendOnlyReviewStore(path)
    assert reopened.get_review_run("run:1") is not None
    assert reopened.get_review_run("run:1").review_run_id == "run:1"
    # Replay must not append duplicate records, and re-recording an existing id
    # must be idempotent.
    reopened.record_review_run(run)
    assert path.read_text(encoding="utf-8").count('"run:1"') == 1


def test_append_only_review_store_fails_unknown_operation(tmp_path):
    path = tmp_path / "reviews.jsonl"
    path.write_text('{"schema_version":1,"operation_kind":"BANANA","payload":{}}\n', encoding="utf-8")
    with pytest.raises(ValueError):
        AppendOnlyReviewStore(path)


def test_model_version_fallback_is_nonempty():
    assert model_version_fallback("model-a") == "model-a"
    assert model_version_fallback("model-a", deployment_id="deploy-42") == "deploy-42"
    with pytest.raises(ValueError):
        model_version_fallback("", None)


def test_review_episode_runs_and_promotes_for_correction():
    episode = _episode(refs=(_host_ref(),))
    outcome = _correction_outcome()
    store = InMemoryReviewStore()
    run = review_episode(episode, (outcome,), store, fact_envelopes=_host_envelopes())
    assert run is not None
    assert run.status is ReviewStatus.COMPLETED
    assert store.get_review_run(run.review_run_id) is run
    assert store.list_review_runs_for_episode(episode.episode_id) == (run,)
    assert store.list_evidence_for_episode(episode.episode_id) == ()


def test_review_episode_skips_trivial_or_non_finalized():
    assert review_episode(_episode(state=EpisodeState.OPEN), (), InMemoryReviewStore()) is None
    assert review_episode(_episode(), (), InMemoryReviewStore()) is None


def test_structured_model_review_engine_protocol_accepts_stub():
    class StubModelEngine:
        def generate_candidate_findings(self, snapshot, deterministic_findings):
            return tuple(deterministic_findings)

    engine = StubModelEngine()
    # This is an interface-boundary test: a structured model engine can be
    # supplied to the pipeline, but it may only return candidate findings.
    assert isinstance(engine, StructuredModelReviewEngine)


def _base_finding(*, run_id: str = "run:one") -> tuple[Episode, OutcomeObservation, ReviewFinding]:
    episode = _episode(refs=(_host_ref(),))
    outcome = _correction_outcome()
    finding = DeterministicReviewEngine().generate_findings(episode, (outcome,), review_run_id=run_id)[0]
    return episode, outcome, finding


def _run_with_finding(finding: ReviewFinding) -> ReviewRun:
    return ReviewRun(
        review_run_id=finding.review_run_id,
        episode_id=finding.episode_id,
        status=ReviewStatus.COMPLETED,
        input_snapshot_hash="sha256:run-input",
        findings=(finding,),
    )


def _valid_evidence(finding: ReviewFinding) -> ReviewEvidence:
    return ReviewEvidence(
        evidence_id="evidence:one",
        source_review_run_id=finding.review_run_id,
        source_finding_id=finding.finding_id,
        source_episode_id=finding.episode_id,
        dimension=finding.dimension,
        proposition=LocalEvidenceProposition(
            dimension=finding.dimension,
            behavior_refs=(finding.attributed_to.ref_id,),
            observation_refs=(finding.evidence_refs[1].ref_id,),
            statement="Local immutable observation.",
        ),
        scope=BehaviorScope(channel="group", directedness="directed"),
        evidence_refs=finding.evidence_refs,
        confidence=finding.confidence,
        causal_attribution=CausalAttribution.NONE,
        attributed_to=finding.attributed_to,
        interpretation_producer=InterpretationProducer.DETERMINISTIC,
    )


def test_snapshot_hashes_full_fact_and_outcome_payloads():
    episode = _episode(refs=(_host_ref(),))
    outcome = _correction_outcome()
    h_a = compute_input_snapshot_hash(episode, (outcome,), _host_envelopes(legacy_fallthrough=True))
    h_b = compute_input_snapshot_hash(episode, (outcome,), _host_envelopes(legacy_fallthrough=False))
    assert h_a != h_b
    assert h_a != compute_input_snapshot_hash(episode, (replace(outcome, confidence=0.5),), _host_envelopes())
    assert h_a != compute_input_snapshot_hash(
        episode,
        (replace(outcome, observed_at=outcome.observed_at + timedelta(seconds=1)),),
        _host_envelopes(),
    )


def test_snapshot_detaches_envelope_mapping_and_rejects_mutable_custom_payloads():
    episode = _episode(refs=(_host_ref(),))
    outcome = _correction_outcome()
    supplied = _host_envelopes()
    snapshot = ReviewInputSnapshot(episode, (outcome,), supplied)
    supplied.clear()
    resolver = ReviewFactResolver(snapshot)
    resolved = resolver.resolve(episode.episode_id, EvidenceSourceType.HOST_RESULT, _host_ref().ref_id)
    assert resolved.source_type is EvidenceSourceType.HOST_RESULT
    assert resolved.payload["fields"]["host_result"]["fields"]["output_state"] == OutputState.OUTPUT_READY.value
    with pytest.raises(ValueError):
        ReviewInputSnapshot(
            episode,
            (outcome,),
            {(EvidenceSourceType.HOST_RESULT, _host_ref().ref_id): {"nested": ["mutable"]}},
        )


def test_resolver_requires_typed_complete_facts_and_never_uses_event_proxy():
    episode = _episode(refs=(_host_ref(),))
    outcome = _correction_outcome()
    with pytest.raises(ValueError, match="mismatch"):
        ReviewInputSnapshot(
            episode,
            (outcome,),
            {(EvidenceSourceType.HOST_RESULT, _host_ref().ref_id): outcome},
        )
    with pytest.raises(ValueError, match="mismatch"):
        ReviewInputSnapshot(
            episode,
            (outcome,),
            {(EvidenceSourceType.BEHAVIOR_TRACE, "trace:1"): _host_result()},
        )
    with pytest.raises(ValueError, match="mismatch"):
        ReviewInputSnapshot(
            episode,
            (outcome,),
            {(EvidenceSourceType.HOST_RESULT, _host_ref().ref_id): _host_ref()},
        )
    no_envelope = ReviewFactResolver(ReviewInputSnapshot(episode, (outcome,), {}))
    with pytest.raises(ValueError, match="unresolved complete HOST_RESULT"):
        no_envelope.resolve(episode.episode_id, EvidenceSourceType.HOST_RESULT, _host_ref().ref_id)
    resolved = ReviewFactResolver(ReviewInputSnapshot(episode, (outcome,), _host_envelopes())).resolve(
        episode.episode_id,
        EvidenceSourceType.HOST_RESULT,
        _host_ref().ref_id,
    )
    assert resolved.source_type is EvidenceSourceType.HOST_RESULT
    with pytest.raises(ValueError, match="unsupported canonical fact"):
        ReviewInputSnapshot(
            episode,
            (outcome,),
            {(EvidenceSourceType.TOOL_RESULT, "tool:1"): object()},
        )


@pytest.mark.parametrize("causal", [CausalAttribution.POSSIBLE, CausalAttribution.SUPPORTED, CausalAttribution.DIRECT])
def test_all_non_none_causal_hypotheses_are_finding_only(causal):
    episode, outcome, finding = _base_finding()

    class CausalEngine:
        def generate_findings(self, *_args, **_kwargs):
            return [replace(
                finding,
                finding_type=FindingType.CAUSAL_HYPOTHESIS,
                causal_attribution=causal,
                claim="Lack of tool use caused the user correction.",
            )]

    store = InMemoryReviewStore()
    run = review_episode(episode, (outcome,), store, fact_envelopes=_host_envelopes(), deterministic_engine=CausalEngine())
    assert run is not None and run.status is ReviewStatus.COMPLETED
    assert store.list_evidence_for_episode(episode.episode_id) == ()


@pytest.mark.parametrize(
    "claim",
    [
        "Next time use tools.",
        "下次应该先查工具。",
        "This user prefers concise answers.",
        "类似情况下保持沉默更好。",
        "User explicitly contradicted the Host output. Next time use tools.",
        "User explicitly contradicted the Host output. Nеxt time use tools.",
    ],
)
@pytest.mark.parametrize("producer", [InterpretationProducer.DETERMINISTIC, InterpretationProducer.MODEL])
def test_all_policy_or_appended_statements_are_finding_only_for_any_claimed_producer(claim, producer):
    episode, outcome, finding = _base_finding()

    class PolicyEngine:
        def generate_findings(self, *_args, **_kwargs):
            return [replace(
                finding,
                claim=claim,
                interpretation_producer=producer,
                model="m" if producer is InterpretationProducer.MODEL else None,
                model_version="v" if producer is InterpretationProducer.MODEL else None,
                prompt_version="p" if producer is InterpretationProducer.MODEL else None,
            )]

    store = InMemoryReviewStore()
    run = review_episode(episode, (outcome,), store, fact_envelopes=_host_envelopes(), deterministic_engine=PolicyEngine())
    assert run is not None
    assert store.list_evidence_for_episode(episode.episode_id) == ()


@pytest.mark.parametrize("field", ["model", "model_version", "prompt_version"])
def test_model_provenance_is_checked_before_promotion(field):
    episode, outcome, finding = _base_finding()
    candidate = replace(
        finding,
        interpretation_producer=InterpretationProducer.MODEL,
        model="m",
        model_version="v",
        prompt_version="p",
    )
    candidate = replace(candidate, **{field: None})

    class IncompleteModelEngine:
        def generate_findings(self, *_args, **_kwargs):
            return [candidate]

    store = InMemoryReviewStore()
    run = review_episode(episode, (outcome,), store, fact_envelopes=_host_envelopes(), deterministic_engine=IncompleteModelEngine())
    assert run is not None and run.status is ReviewStatus.FAILED
    assert store.list_evidence_for_episode(episode.episode_id) == ()


def test_public_promoter_itself_rejects_missing_model_provenance():
    episode, outcome, finding = _base_finding()
    finding = replace(
        finding,
        interpretation_producer=InterpretationProducer.MODEL,
        model=None,
        model_version="v",
        prompt_version="p",
    )
    run = _run_with_finding(finding)
    resolver = ReviewFactResolver(ReviewInputSnapshot(episode, (outcome,), _host_envelopes()))
    with pytest.raises(ValueError, match="MODEL interpretation requires"):
        promote_finding_to_evidence(
            finding,
            episode,
            (outcome,),
            resolver,
            scope=BehaviorScope("group", "directed"),
            review_run=run,
        )


@pytest.mark.parametrize("field,value", [("review_run_id", "run:foreign"), ("episode_id", "episode:foreign")])
def test_orchestration_rebinds_candidate_lineage_before_persistence(field, value):
    episode, outcome, finding = _base_finding(run_id="run:actual")

    class ForeignLineageEngine:
        def generate_findings(self, *_args, **_kwargs):
            return [replace(finding, **{field: value})]

    store = InMemoryReviewStore()
    run = review_episode(
        episode,
        (outcome,),
        store,
        fact_envelopes=_host_envelopes(),
        deterministic_engine=ForeignLineageEngine(),
        review_run_id="run:actual",
    )
    assert run is not None
    assert all(item.review_run_id == run.review_run_id and item.episode_id == episode.episode_id for item in run.findings)
    assert store.list_evidence_for_episode(episode.episode_id) == ()


def test_promoter_rejects_foreign_authoritative_run_or_episode():
    episode, outcome, finding = _base_finding()
    run = _run_with_finding(finding)
    resolver = ReviewFactResolver(ReviewInputSnapshot(episode, (outcome,), _host_envelopes()))
    with pytest.raises(ValueError, match="authoritative ReviewRun"):
        promote_finding_to_evidence(
            replace(finding, review_run_id="run:foreign"),
            episode,
            (outcome,),
            resolver,
            scope=BehaviorScope("group", "directed"),
            review_run=run,
        )
    with pytest.raises(ValueError, match="supplied Episode"):
        promote_finding_to_evidence(
            finding,
            replace(episode, episode_id="episode:foreign"),
            (outcome,),
            resolver,
            scope=BehaviorScope("group", "directed"),
            review_run=run,
        )


def test_store_enforces_identity_finding_and_evidence_lineage(tmp_path):
    episode, _outcome, finding = _base_finding()
    run = _run_with_finding(finding)
    store = InMemoryReviewStore()
    assert store.record_review_run(run) is run
    assert store.record_review_run(run) is run
    with pytest.raises(ReviewStoreIntegrityError, match="ReviewRun identity"):
        store.record_review_run(replace(run, input_snapshot_hash="sha256:different"))
    with pytest.raises(ReviewStoreIntegrityError, match="duplicate Finding"):
        store.record_review_run(replace(run, findings=(finding, finding)))
    foreign_finding = replace(finding, finding_id="finding:foreign", review_run_id="run:foreign")
    with pytest.raises(ReviewStoreIntegrityError, match="Finding does not belong"):
        store.record_review_run(replace(run, findings=(foreign_finding,)))

    evidence = _valid_evidence(finding)
    assert store.record_evidence(evidence) is evidence
    assert store.record_evidence(evidence) is evidence
    with pytest.raises(ReviewStoreIntegrityError, match="ReviewEvidence identity"):
        store.record_evidence(replace(evidence, confidence=Confidence.LOW))
    with pytest.raises(ReviewStoreIntegrityError, match="unknown ReviewRun"):
        InMemoryReviewStore().record_evidence(evidence)
    with pytest.raises(ReviewStoreIntegrityError, match="unknown Finding"):
        store.record_evidence(replace(evidence, evidence_id="evidence:unknown-finding", source_finding_id="finding:other"))
    with pytest.raises(ReviewStoreIntegrityError, match="Episode lineage"):
        store.record_evidence(replace(evidence, evidence_id="evidence:wrong-episode", source_episode_id="episode:other"))

    path = tmp_path / "reviews.jsonl"
    append_store = AppendOnlyReviewStore(path)
    append_store.record_review_run(run)
    append_store.record_evidence(evidence)
    assert AppendOnlyReviewStore(path).get_review_run(run.review_run_id) == run
    with pytest.raises(ReviewStoreIntegrityError, match="ReviewRun identity"):
        append_store.record_review_run(replace(run, input_snapshot_hash="sha256:different"))


def test_append_only_store_replay_rejects_orphan_evidence(tmp_path):
    _episode_value, _outcome, finding = _base_finding()
    evidence = _valid_evidence(finding)
    path = tmp_path / "orphan.jsonl"
    path.write_text(
        json.dumps({
            "schema_version": 1,
            "operation_id": "orphan",
            "operation_kind": "REVIEW_EVIDENCE",
            "payload": _evidence_to_dict(evidence),
        }) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ReviewStoreIntegrityError, match="unknown ReviewRun"):
        AppendOnlyReviewStore(path)


# ---------------------------------------------------------------------------
# P1d.2: production Evidence disabled; supplied fact attachment is mandatory.
# ---------------------------------------------------------------------------


def _acknowledgement_outcome() -> OutcomeObservation:
    return OutcomeObservation(
        observation_id=make_outcome_observation_id(
            "ep:1", OutcomeKind.EXPLICIT_ACKNOWLEDGEMENT, source_event_id="qq:2"
        ),
        target_episode_id=make_episode_id("g1", "qq:1"),
        kind=OutcomeKind.EXPLICIT_ACKNOWLEDGEMENT,
        observed_at=_NOW,
        source_event_id="qq:2",
    )


def test_p1d2_acknowledgement_finding_never_produces_evidence():
    episode = _episode(refs=(_host_ref(),))
    store = InMemoryReviewStore()
    run = review_episode(
        episode,
        (_acknowledgement_outcome(),),
        store,
        fact_envelopes=_host_envelopes(),
    )
    assert run is not None and run.status is ReviewStatus.COMPLETED
    assert len(run.findings) == 1
    assert store.list_evidence_for_episode(episode.episode_id) == ()


def test_p1d2_public_orchestration_cannot_promote_caller_tool_claim():
    episode, outcome, finding = _base_finding()

    class CallerControlledEngine:
        def generate_findings(self, *_args, **_kwargs):
            return [replace(finding, claim="Host output differed from the available ToolResult.")]

    store = InMemoryReviewStore()
    run = review_episode(
        episode,
        (outcome,),
        store,
        fact_envelopes=_host_envelopes(),
        deterministic_engine=CallerControlledEngine(),
    )
    assert run is not None and run.status is ReviewStatus.COMPLETED
    assert len(run.findings) == 1
    assert store.list_evidence_for_episode(episode.episode_id) == ()


def test_p1d2_model_candidate_cannot_produce_evidence():
    episode = _episode(refs=(_host_ref(),))
    outcome = _correction_outcome()

    class ModelCandidate:
        def generate_candidate_findings(self, _snapshot, deterministic_findings):
            return [replace(
                deterministic_findings[0],
                finding_id="finding:model-candidate",
                claim="Host output differed from the available ToolResult.",
                interpretation_producer=InterpretationProducer.MODEL,
                model="test-model",
                model_version="v1",
                prompt_version="p1",
            )]

    store = InMemoryReviewStore()
    run = review_episode(
        episode,
        (outcome,),
        store,
        fact_envelopes=_host_envelopes(),
        model_engine=ModelCandidate(),
    )
    assert run is not None and run.status is ReviewStatus.COMPLETED
    assert len(run.findings) == 2
    assert store.list_evidence_for_episode(episode.episode_id) == ()


def test_p1d2_has_no_promotable_statement_or_private_authority_boundary():
    import iris_memory.cognitive.review_service as review_service

    assert review_service._SAFE_PROMOTABLE_STATEMENTS == frozenset()
    assert not hasattr(review_service, "_PROMOTION_AUTHORITY")


def test_p1d2_direct_promoter_is_non_production_and_fail_closed():
    episode, outcome, finding = _base_finding()
    run = _run_with_finding(finding)
    resolver = ReviewFactResolver(ReviewInputSnapshot(episode, (outcome,), _host_envelopes()))
    assert promote_finding_to_evidence(
        finding,
        episode,
        (outcome,),
        resolver,
        scope=BehaviorScope("group", "directed"),
        review_run=run,
    ) is None


def test_p1d2_snapshot_accepts_only_host_record_attached_to_current_episode():
    attached_ref = _host_ref(trace_id="trace:attached")
    episode = _episode(refs=(attached_ref,))
    outcome = _correction_outcome()
    accepted = ReviewInputSnapshot(episode, (outcome,), _host_envelopes(attached_ref))
    assert ReviewFactResolver(accepted).resolve(
        episode.episode_id, EvidenceSourceType.HOST_RESULT, attached_ref.ref_id
    ).source_type is EvidenceSourceType.HOST_RESULT

    unrelated_ref = _host_ref(trace_id="trace:unattached")
    with pytest.raises(ValueError, match="not attached"):
        ReviewInputSnapshot(episode, (outcome,), _host_envelopes(unrelated_ref))


def test_p1d2_host_record_attached_to_e1_is_rejected_for_e2():
    e1_ref = _host_ref(trace_id="trace:e1")
    episode_e1 = _episode(refs=(e1_ref,))
    episode_e2 = replace(episode_e1, episode_id="episode:e2", event_refs=(_host_ref(trace_id="trace:e2"),))
    outcome_e2 = replace(_correction_outcome(), target_episode_id=episode_e2.episode_id)
    with pytest.raises(ValueError, match="not attached"):
        ReviewInputSnapshot(episode_e2, (outcome_e2,), _host_envelopes(e1_ref))


def test_p1_showcase_host_result_attachment_requires_exact_execution_linkage():
    ref = _host_ref()
    episode = _episode(refs=(ref,))
    outcome = _correction_outcome()
    record = _host_envelopes(ref)[(EvidenceSourceType.HOST_RESULT, ref.ref_id)]

    accepted = ReviewInputSnapshot(
        episode,
        (outcome,),
        {(EvidenceSourceType.HOST_RESULT, ref.ref_id): record},
    )
    assert accepted.fact_envelopes

    invalid_refs = {
        "wrong_revision": replace(ref, execution_record_id=f"{record.trace.trace_id}:999"),
        "missing_execution_id": replace(ref, execution_record_id=None),
        "wrong_trace": replace(ref, trace_id="trace:wrong"),
        "wrong_event": replace(ref, source_event_id="event:wrong"),
        "wrong_kind": replace(ref, kind=EpisodeEventKind.DISPATCH),
    }
    for _name, invalid_ref in invalid_refs.items():
        invalid_episode = replace(episode, event_refs=(invalid_ref,))
        with pytest.raises(ValueError, match="not attached"):
            ReviewInputSnapshot(
                invalid_episode,
                (outcome,),
                {(EvidenceSourceType.HOST_RESULT, invalid_ref.ref_id): record},
            )

    dispatch_record = replace(record, stage=TraceStage.DISPATCH)
    with pytest.raises(ValueError, match="not attached"):
        ReviewInputSnapshot(
            episode,
            (outcome,),
            {(EvidenceSourceType.HOST_RESULT, ref.ref_id): dispatch_record},
        )


def test_p1_showcase_service_rejects_wrong_revision_injected_host_record():
    ref = _host_ref()
    episode = _episode(refs=(ref,))
    outcome = _correction_outcome()
    record = _host_envelopes(ref)[(EvidenceSourceType.HOST_RESULT, ref.ref_id)]
    invalid_ref = replace(ref, execution_record_id=f"{record.trace.trace_id}:999")
    invalid_episode = replace(episode, event_refs=(invalid_ref,))

    from iris_memory.cognitive.episode_store import InMemoryEpisodeStore
    from iris_memory.web.services.observatory_service import P1ObservatoryService

    episode_store = InMemoryEpisodeStore()
    episode_store.create_episode(invalid_episode)
    episode_store.record_outcome(outcome)
    detail = P1ObservatoryService(
        episode_store,
        execution_records={invalid_ref.ref_id: record},
    ).episode_detail(invalid_episode.episode_id)
    assert detail["attachments"][0]["status"] == "REJECTED"
    assert detail["snapshot"]["available"] is False


def test_p1d2_episode_event_and_behavior_trace_attachment_are_enforced():
    trace = _behavior_trace(trace_id="trace:attached")
    attached_ref = _host_ref(trace_id=trace.trace_id)
    episode = _episode(refs=(attached_ref,))
    outcome = _correction_outcome()
    snapshot = ReviewInputSnapshot(
        episode,
        (outcome,),
        {
            (EvidenceSourceType.EPISODE_EVENT, attached_ref.ref_id): attached_ref,
            (EvidenceSourceType.BEHAVIOR_TRACE, trace.trace_id): trace,
        },
    )
    resolver = ReviewFactResolver(snapshot)
    assert resolver.resolve(episode.episode_id, EvidenceSourceType.EPISODE_EVENT, attached_ref.ref_id).source_type is EvidenceSourceType.EPISODE_EVENT
    assert resolver.resolve(episode.episode_id, EvidenceSourceType.BEHAVIOR_TRACE, trace.trace_id).source_type is EvidenceSourceType.BEHAVIOR_TRACE

    foreign_ref = _host_ref(trace_id="trace:foreign")
    foreign_trace = _behavior_trace(trace_id="trace:foreign")
    with pytest.raises(ValueError, match="EpisodeEventRef is not attached"):
        ReviewInputSnapshot(
            episode,
            (outcome,),
            {(EvidenceSourceType.EPISODE_EVENT, foreign_ref.ref_id): foreign_ref},
        )
    with pytest.raises(ValueError, match="BehaviorTrace is not attached"):
        ReviewInputSnapshot(
            episode,
            (outcome,),
            {(EvidenceSourceType.BEHAVIOR_TRACE, foreign_trace.trace_id): foreign_trace},
        )


def test_p1d2_outcome_target_attachment_allows_late_feedback_from_newer_event():
    episode = _episode(refs=(_host_ref(),))
    late_for_old_episode = replace(_correction_outcome(), source_event_id="qq:newer-feedback")
    snapshot = ReviewInputSnapshot(
        episode,
        (late_for_old_episode,),
        {(EvidenceSourceType.OUTCOME_OBSERVATION, late_for_old_episode.observation_id): late_for_old_episode},
    )
    assert snapshot.outcomes == (late_for_old_episode,)

    wrong_target = replace(late_for_old_episode, target_episode_id="episode:other")
    with pytest.raises(ValueError, match="targets a different Episode"):
        ReviewInputSnapshot(episode, (wrong_target,), {})
