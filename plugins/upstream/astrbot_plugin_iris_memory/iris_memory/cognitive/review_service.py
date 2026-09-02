"""Review input snapshot, eligibility, deterministic engine, validation, promotion."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Callable, Iterable, Mapping, Protocol, runtime_checkable
from uuid import uuid4

from .contracts import BehaviorExecutionRecord, BehaviorTrace, TraceStage
from .episode import Episode, EpisodeEventKind, EpisodeEventRef, EpisodeState
from .outcome import OutcomeKind, OutcomeObservation
from .review import (
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
    ReviewEligibility,
    ReviewEligibilityDecision,
    ReviewEvidence,
    ReviewEvidenceRef,
    ReviewFinding,
    ReviewRun,
    ReviewStatus,
)
from .review_store import ReviewStore


# ---------------------------------------------------------------------------
# Review input snapshot and hash
# ---------------------------------------------------------------------------


_FACT_ENVELOPE_SCHEMA_VERSION = "p1d.fact-envelope.v1"


def _canonical_value(value: object) -> object:
    """Detach a supported cognitive fact into JSON-safe immutable data.

    Review snapshots deliberately accept only the frozen P1d fact roots below.
    This helper recursively serializes their fields, rejecting arbitrary live
    objects instead of preserving a mutable pointer or falling back to repr().
    """
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("fact payload floats must be finite")
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("fact payload datetimes must be timezone-aware")
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        items: dict[str, object] = {}
        for key in sorted(value, key=lambda item: str(item)):
            if not isinstance(key, str):
                raise ValueError("fact payload mapping keys must be strings")
            items[key] = _canonical_value(value[key])
        return MappingProxyType(items)
    if isinstance(value, (tuple, list, frozenset, set)):
        return tuple(_canonical_value(item) for item in value)
    if is_dataclass(value):
        params = getattr(type(value), "__dataclass_params__", None)
        if params is None or not params.frozen:
            raise ValueError("fact payload dataclass must be frozen")
        return MappingProxyType({
            "__type__": f"{type(value).__module__}.{type(value).__qualname__}",
            "fields": MappingProxyType({field.name: _canonical_value(getattr(value, field.name)) for field in fields(value)}),
        })
    raise ValueError(f"unsupported mutable or unserializable fact payload: {type(value).__name__}")


def _json_ready(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    return value


def _canonical_json(value: object) -> str:
    return json.dumps(_json_ready(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class CanonicalFactEnvelope:
    """One immutable, typed factual input accepted by P1d Review."""

    source_type: EvidenceSourceType
    ref_id: str
    schema_version: str
    payload: Mapping[str, object]

    def __post_init__(self) -> None:
        if not self.ref_id or not self.schema_version:
            raise ValueError("canonical fact envelope requires ref_id and schema_version")
        payload = _canonical_value(self.payload)
        if not isinstance(payload, Mapping):  # pragma: no cover - defensive guard
            raise ValueError("canonical fact envelope payload must be a mapping")
        object.__setattr__(self, "payload", payload)

    def canonical_payload(self) -> Mapping[str, object]:
        return MappingProxyType({
            "source_type": self.source_type.value,
            "ref_id": self.ref_id,
            "schema_version": self.schema_version,
            "payload": self.payload,
        })


_ROOT_FACT_TYPES: dict[EvidenceSourceType, type[object]] = {
    EvidenceSourceType.EPISODE_EVENT: EpisodeEventRef,
    EvidenceSourceType.OUTCOME_OBSERVATION: OutcomeObservation,
    EvidenceSourceType.BEHAVIOR_TRACE: BehaviorTrace,
    # HostResult itself contains no immutable episode linkage.  The frozen
    # execution record is the minimum factual carrier that joins it to a trace.
    EvidenceSourceType.HOST_RESULT: BehaviorExecutionRecord,
}


def _canonical_fact_envelope(source_type: EvidenceSourceType, ref_id: str, fact: object) -> CanonicalFactEnvelope:
    expected_type = _ROOT_FACT_TYPES.get(source_type)
    if expected_type is None:
        # This codebase has no frozen ToolResult value contract yet.  Treating an
        # EpisodeEventRef or arbitrary mapping as a ToolResult would recreate the
        # authority bug this boundary exists to prevent.
        raise ValueError(f"unsupported canonical fact source type: {source_type.value}")
    if type(fact) is not expected_type:
        raise ValueError(
            f"fact source type mismatch for {source_type.value}: expected {expected_type.__name__}, got {type(fact).__name__}"
        )
    if source_type is EvidenceSourceType.EPISODE_EVENT and fact.ref_id != ref_id:
        raise ValueError("EpisodeEventRef ref_id does not match fact envelope key")
    if source_type is EvidenceSourceType.OUTCOME_OBSERVATION and fact.observation_id != ref_id:
        raise ValueError("OutcomeObservation ref_id does not match fact envelope key")
    payload = _canonical_value(fact)
    if not isinstance(payload, Mapping):  # pragma: no cover - root contracts are dataclasses
        raise ValueError("canonical fact payload must be a mapping")
    return CanonicalFactEnvelope(source_type, ref_id, _FACT_ENVELOPE_SCHEMA_VERSION, payload)


def _validate_fact_envelope_attachment(
    *,
    episode: Episode,
    outcomes: tuple[OutcomeObservation, ...],
    source_type: EvidenceSourceType,
    ref_id: str,
    fact: object,
) -> None:
    """Require every approved fact to be structurally attached to the Episode.

    A caller supplied mapping key is only a lookup identifier, never authority
    that an otherwise valid fact belongs in this factual snapshot.
    """
    if source_type is EvidenceSourceType.EPISODE_EVENT:
        assert isinstance(fact, EpisodeEventRef)
        if not any(event_ref == fact for event_ref in episode.event_refs):
            raise ValueError("EpisodeEventRef is not attached to the reviewed Episode")
        return

    if source_type is EvidenceSourceType.OUTCOME_OBSERVATION:
        assert isinstance(fact, OutcomeObservation)
        if fact.target_episode_id != episode.episode_id:
            raise ValueError("OutcomeObservation targets a different Episode")
        if not any(outcome == fact for outcome in outcomes):
            raise ValueError("OutcomeObservation is not part of this Review snapshot")
        return

    if source_type is EvidenceSourceType.BEHAVIOR_TRACE:
        assert isinstance(fact, BehaviorTrace)
        if ref_id != fact.trace_id:
            raise ValueError("BehaviorTrace envelope ref_id must equal trace_id")
        if not any(
            event_ref.trace_id == fact.trace_id
            and event_ref.source_event_id == fact.event_id
            for event_ref in episode.event_refs
        ):
            raise ValueError("BehaviorTrace is not attached to the reviewed Episode")
        return

    if source_type is EvidenceSourceType.HOST_RESULT:
        assert isinstance(fact, BehaviorExecutionRecord)
        trace = fact.trace
        if not any(
            event_ref.ref_id == ref_id
            and event_ref.kind is EpisodeEventKind.HOST_OUTPUT
            and event_ref.trace_id is not None
            and event_ref.source_event_id is not None
            and event_ref.execution_record_id is not None
            and event_ref.trace_id == trace.trace_id
            and event_ref.source_event_id == trace.event_id
            and event_ref.execution_record_id == f"{trace.trace_id}:{fact.revision}"
            and fact.stage is TraceStage.HOST_OUTPUT
            for event_ref in episode.event_refs
        ):
            raise ValueError(
                "HostResult record is not attached to a HOST_OUTPUT event of the reviewed Episode"
            )
        return

    # There is deliberately no frozen ToolResult value/linkage contract in P1d.2.
    raise ValueError(f"unsupported review fact source type: {source_type.value}")


def _snapshot_fact_envelopes(
    episode: Episode,
    outcomes: tuple[OutcomeObservation, ...],
    fact_envelopes: Mapping[tuple[EvidenceSourceType, str], object],
) -> Mapping[tuple[EvidenceSourceType, str], CanonicalFactEnvelope]:
    canonical: dict[tuple[EvidenceSourceType, str], CanonicalFactEnvelope] = {}
    for key, fact in fact_envelopes.items():
        if not isinstance(key, tuple) or len(key) != 2:
            raise ValueError("fact envelope keys must be (EvidenceSourceType, ref_id)")
        source_type, ref_id = key
        if not isinstance(source_type, EvidenceSourceType) or not isinstance(ref_id, str) or not ref_id:
            raise ValueError("fact envelope key has invalid source_type/ref_id")
        envelope = _canonical_fact_envelope(source_type, ref_id, fact)
        _validate_fact_envelope_attachment(
            episode=episode,
            outcomes=outcomes,
            source_type=source_type,
            ref_id=ref_id,
            fact=fact,
        )
        canonical[key] = envelope
    return MappingProxyType(canonical)


@dataclass(frozen=True, slots=True)
class ReviewInputSnapshot:
    """Immutable canonical factual input for a single Review execution."""

    episode: Episode
    outcomes: tuple[OutcomeObservation, ...]
    fact_envelopes: Mapping[tuple[EvidenceSourceType, str], CanonicalFactEnvelope]

    def __post_init__(self) -> None:
        if not isinstance(self.episode, Episode):
            raise ValueError("ReviewInputSnapshot requires an Episode")
        outcome_tuple = tuple(sorted(self.outcomes, key=lambda outcome: outcome.observation_id))
        if any(not isinstance(outcome, OutcomeObservation) for outcome in outcome_tuple):
            raise ValueError("ReviewInputSnapshot outcomes must be OutcomeObservation values")
        if any(outcome.target_episode_id != self.episode.episode_id for outcome in outcome_tuple):
            raise ValueError("ReviewInputSnapshot outcome targets a different Episode")
        object.__setattr__(self, "outcomes", outcome_tuple)
        object.__setattr__(
            self,
            "fact_envelopes",
            _snapshot_fact_envelopes(self.episode, outcome_tuple, self.fact_envelopes),
        )

    def canonical_payload(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": "p1d.review-input-snapshot.v1",
            "episode": _canonical_value(self.episode),
            "outcomes": tuple(_canonical_value(outcome) for outcome in self.outcomes),
            "fact_envelopes": tuple(
                envelope.canonical_payload()
                for _, envelope in sorted(self.fact_envelopes.items(), key=lambda item: (item[0][0].value, item[0][1]))
            ),
        })


def compute_input_snapshot_hash(
    episode: Episode,
    outcomes: Iterable[OutcomeObservation],
    fact_envelopes: Mapping[tuple[EvidenceSourceType, str], object] | None = None,
) -> str:
    """Stable SHA-256 over the complete immutable P1d Review input snapshot."""
    snapshot = ReviewInputSnapshot(episode, tuple(outcomes), fact_envelopes or {})
    raw = _canonical_json(snapshot.canonical_payload())
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Fact resolver
# ---------------------------------------------------------------------------


class ReviewFactResolver:
    """Resolve typed canonical facts inside one immutable Review snapshot."""

    def __init__(self, snapshot: ReviewInputSnapshot) -> None:
        self.snapshot = snapshot
        self._event_ref_by_id = {ref.ref_id: ref for ref in snapshot.episode.event_refs}
        self._outcome_by_id = {o.observation_id: o for o in snapshot.outcomes}
        self._event_envelopes = {
            ref.ref_id: _canonical_fact_envelope(EvidenceSourceType.EPISODE_EVENT, ref.ref_id, ref)
            for ref in snapshot.episode.event_refs
        }
        self._outcome_envelopes = {
            outcome.observation_id: _canonical_fact_envelope(
                EvidenceSourceType.OUTCOME_OBSERVATION,
                outcome.observation_id,
                outcome,
            )
            for outcome in snapshot.outcomes
        }

    def resolve(self, episode_id: str, source_type: EvidenceSourceType, ref_id: str) -> CanonicalFactEnvelope:
        if episode_id != self.snapshot.episode.episode_id:
            raise ValueError(f"ref outside Episode boundary: {episode_id}")
        if source_type is EvidenceSourceType.OUTCOME_OBSERVATION:
            envelope = self._outcome_envelopes.get(ref_id)
            if envelope is None:
                raise ValueError(f"unknown outcome ref: {ref_id}")
            return envelope
        if source_type is EvidenceSourceType.EPISODE_EVENT:
            envelope = self._event_envelopes.get(ref_id)
            if envelope is None:
                raise ValueError(f"unknown episode event ref: {ref_id}")
            return envelope

        # Complete behavior/host/tool facts require a typed canonical envelope.
        # EpisodeEventRef can prove only its own structural existence and never
        # substitutes for a HostResult, ToolResult, or BehaviorTrace payload.
        envelope = self.snapshot.fact_envelopes.get((source_type, ref_id))
        if envelope is None:
            raise ValueError(f"unresolved complete {source_type.value} ref: {ref_id}")
        if envelope.source_type is not source_type or envelope.ref_id != ref_id:
            raise ValueError("canonical fact envelope source/ref mismatch")
        return envelope


def snapshot_episode_event_refs(episode: Episode) -> tuple[EpisodeEventRef, ...]:
    return episode.event_refs


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


def evaluate_review_eligibility(episode: Episode, outcomes: Iterable[OutcomeObservation]) -> ReviewEligibilityDecision:
    outcome_list = tuple(outcomes)
    if episode.state is not EpisodeState.FINALIZED:
        return ReviewEligibilityDecision(
            episode_id=episode.episode_id,
            decision=ReviewEligibility.DEFER,
            reason="episode_not_finalized",
        )
    has_output = any(
        ref.kind in (EpisodeEventKind.HOST_OUTPUT, EpisodeEventKind.COGNITIVE_PROPOSAL)
        for ref in episode.event_refs
    )
    meaningful_outcome = any(
        o.kind in (
            OutcomeKind.EXPLICIT_CORRECTION,
            OutcomeKind.EXPLICIT_STOP_REQUEST,
            OutcomeKind.EXPLICIT_ACKNOWLEDGEMENT,
            OutcomeKind.FOLLOWUP_QUESTION,
            OutcomeKind.TOOL_FAILED,
            OutcomeKind.TOOL_SUCCEEDED,
        )
        for o in outcome_list
    )
    if has_output or meaningful_outcome:
        return ReviewEligibilityDecision(
            episode_id=episode.episode_id,
            decision=ReviewEligibility.REVIEW,
            reason="meaningful_review_target",
            evidence_refs=tuple(ref.ref_id for ref in episode.event_refs[:20]) + tuple(o.observation_id for o in outcome_list[:20]),
        )
    return ReviewEligibilityDecision(
        episode_id=episode.episode_id,
        decision=ReviewEligibility.SKIP,
        reason="trivial_or_no_meaningful_target",
    )


# ---------------------------------------------------------------------------
# Deterministic review engine
# ---------------------------------------------------------------------------


class DeterministicReviewEngine:
    producer = "deterministic_review_engine"

    def generate_findings(
        self,
        episode: Episode,
        outcomes: Iterable[OutcomeObservation],
        *,
        review_run_id: str | None = None,
    ) -> list[ReviewFinding]:
        review_run_id = review_run_id or f"run:{uuid4().hex}"
        findings: list[ReviewFinding] = []
        outcome_list = tuple(outcomes)
        host_refs = [ref for ref in episode.event_refs if ref.kind is EpisodeEventKind.HOST_OUTPUT]
        for outcome in outcome_list:
            if outcome.kind is OutcomeKind.EXPLICIT_CORRECTION:
                for host_ref in host_refs:
                    findings.append(self._finding(
                        review_run_id,
                        episode,
                        # P1c has no neutral feedback dimension.  ATTRIBUTION is
                        # the narrowest existing diagnostic dimension: it records
                        # the user's contradiction without claiming factual error.
                        ReviewDimension.ATTRIBUTION,
                        FindingType.LOCAL_OBSERVATION,
                        "User explicitly contradicted the Host output.",
                        (
                            ReviewEvidenceRef(host_ref.ref_id, EvidenceSourceType.HOST_RESULT, EvidenceKind.STRUCTURAL),
                            ReviewEvidenceRef(outcome.observation_id, EvidenceSourceType.OUTCOME_OBSERVATION, EvidenceKind.EXPLICIT),
                        ),
                        AttributionRef(AttributionTargetType.HOST_RESULT, host_ref.ref_id),
                        Confidence.MEDIUM,
                        CausalAttribution.NONE,
                    ))
            elif outcome.kind is OutcomeKind.EXPLICIT_ACKNOWLEDGEMENT:
                for host_ref in host_refs:
                    findings.append(self._finding(
                        review_run_id,
                        episode,
                        ReviewDimension.REALIZATION,
                        FindingType.LOCAL_OBSERVATION,
                        "Host output was followed by explicit acknowledgement.",
                        (
                            ReviewEvidenceRef(host_ref.ref_id, EvidenceSourceType.HOST_RESULT, EvidenceKind.STRUCTURAL),
                            ReviewEvidenceRef(outcome.observation_id, EvidenceSourceType.OUTCOME_OBSERVATION, EvidenceKind.EXPLICIT),
                        ),
                        AttributionRef(AttributionTargetType.HOST_RESULT, host_ref.ref_id),
                        Confidence.MEDIUM,
                        CausalAttribution.NONE,
                    ))
        return findings

    def _finding(
        self,
        review_run_id: str,
        episode: Episode,
        dimension: ReviewDimension,
        finding_type: FindingType,
        claim: str,
        evidence_refs: tuple[ReviewEvidenceRef, ...],
        attributed_to: AttributionRef,
        confidence: Confidence,
        causal: CausalAttribution,
    ) -> ReviewFinding:
        return ReviewFinding(
            finding_id=f"finding:{uuid4().hex}",
            review_run_id=review_run_id,
            episode_id=episode.episode_id,
            dimension=dimension,
            finding_type=finding_type,
            claim=claim,
            evidence_refs=evidence_refs,
            attributed_to=attributed_to,
            confidence=confidence,
            causal_attribution=causal,
            interpretation_producer=InterpretationProducer.DETERMINISTIC,
            producer=self.producer,
        )


# ---------------------------------------------------------------------------
# Model review interface (structured candidates only; validator is the gate)
# ---------------------------------------------------------------------------


@runtime_checkable
class StructuredModelReviewEngine(Protocol):
    """Structured model review boundary.

    Model engines may only return candidate ReviewFindings.  They can never
    directly write ReviewEvidence or policy.  All candidates must pass the same
    grounding/provenance/promotion validators as deterministic findings.
    """

    def generate_candidate_findings(
        self,
        snapshot: ReviewInputSnapshot,
        deterministic_findings: tuple[ReviewFinding, ...],
    ) -> tuple[ReviewFinding, ...]: ...


def model_version_fallback(model: str, deployment_id: str | None = None) -> str:
    """Return a non-empty auditable model_version.

    The frozen P1d rule requires MODEL provenance to be complete.  When a
    provider does not expose a separate version, the auditable model/deployment
    identifier (or, at minimum, the model name) must be recorded.
    """
    if deployment_id:
        return deployment_id
    if model:
        return model
    raise ValueError("model_version_fallback requires a model or deployment id")


# ---------------------------------------------------------------------------
# Validation / promotion
# ---------------------------------------------------------------------------


_POLICY_TERMS = (
    "should", "must", "always", "never", "next time", "better to", "prefer", "prefers", "preferred",
    "recommended", "ought to", "likes", "loves", "wants", "expects", "in general", "general approach",
    "safer", "avoid", "do not", "don't", "i should", "we should",
    "以后", "下次", "应该", "必须", "最好", "建议", "优先", "尽量", "每次都", "绝不", "不要再",
    "喜欢", "偏好", "希望", "这个用户", "用户喜欢", "用户偏好", "用户希望",
)

# P1d.2 deliberately has no frozen statement-specific production promotion
# rule.  Review interpretation and Finding creation remain active, but normal
# Review execution is fail-closed for ReviewEvidence until such a rule exists.
_SAFE_PROMOTABLE_STATEMENTS: frozenset[str] = frozenset()


_ATTRIBUTION_TO_SOURCE = {
    AttributionTargetType.BEHAVIOR_TRACE: EvidenceSourceType.BEHAVIOR_TRACE,
    AttributionTargetType.HOST_RESULT: EvidenceSourceType.HOST_RESULT,
    AttributionTargetType.TOOL_RESULT: EvidenceSourceType.TOOL_RESULT,
    AttributionTargetType.OUTCOME_OBSERVATION: EvidenceSourceType.OUTCOME_OBSERVATION,
    AttributionTargetType.EPISODE_EVENT: EvidenceSourceType.EPISODE_EVENT,
}


def validate_finding_grounding(finding: ReviewFinding, resolver: ReviewFactResolver) -> None:
    seen_ref_ids: set[str] = set()
    for ref in finding.evidence_refs:
        if ref.ref_id in seen_ref_ids:
            raise ValueError(f"duplicate evidence ref_id: {ref.ref_id}")
        seen_ref_ids.add(ref.ref_id)
        resolver.resolve(finding.episode_id, ref.source_type, ref.ref_id)
    source_type = _ATTRIBUTION_TO_SOURCE.get(finding.attributed_to.target_type)
    if source_type is None:
        raise ValueError(f"cannot ground attribution target: {finding.attributed_to.target_type}")
    resolver.resolve(finding.episode_id, source_type, finding.attributed_to.ref_id)


def validate_policy_text(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in _POLICY_TERMS)


def validate_model_provenance(producer: InterpretationProducer, *, model: str | None, model_version: str | None, prompt_version: str | None, schema_version: str) -> None:
    if producer is InterpretationProducer.MODEL:
        if not model or not model_version or not prompt_version or not schema_version:
            raise ValueError("MODEL interpretation requires model/model_version/prompt_version/schema_version")
    else:
        if model is not None or model_version is not None:
            raise ValueError("DETERMINISTIC interpretation must not set model/model_version")


def validate_proposition_safe(proposition: LocalEvidenceProposition) -> None:
    if validate_policy_text(proposition.statement):
        raise ValueError("proposition statement contains policy/normative language")


def validate_causal_attribution(finding: ReviewFinding) -> None:
    """Validate contract shape; non-NONE causal claims stay Finding-only."""
    if finding.finding_type is not FindingType.CAUSAL_HYPOTHESIS and finding.causal_attribution is not CausalAttribution.NONE:
        raise ValueError("non-causal findings must use CausalAttribution.NONE")


def validate_promotable_semantics(finding: ReviewFinding) -> None:
    """Reject policy; return-to-caller handles non-allowlisted Finding-only text."""
    if validate_policy_text(finding.claim):
        raise ValueError("finding statement contains policy/normative language")


def _validate_promotion_lineage(finding: ReviewFinding, review_run: ReviewRun, episode: Episode) -> None:
    if review_run.episode_id != episode.episode_id:
        raise ValueError("ReviewRun does not belong to the supplied Episode")
    if finding.review_run_id != review_run.review_run_id:
        raise ValueError("Finding does not belong to the authoritative ReviewRun")
    if finding.episode_id != review_run.episode_id:
        raise ValueError("Finding Episode does not match the authoritative ReviewRun")
    matching = [candidate for candidate in review_run.findings if candidate.finding_id == finding.finding_id]
    if len(matching) != 1 or matching[0] != finding:
        raise ValueError("Finding is not the authoritative immutable Finding in ReviewRun")


def promote_finding_to_evidence(
    finding: ReviewFinding,
    episode: Episode,
    outcomes: tuple[OutcomeObservation, ...],
    resolver: ReviewFactResolver,
    *,
    scope: BehaviorScope,
    review_run: ReviewRun,
) -> ReviewEvidence | None:
    """Non-production future-contract helper; P1d.2 promotion is disabled.

    The validation remains intentionally available for isolated contract tests
    and future frozen design work.  It cannot produce ReviewEvidence in this
    release and is never called by ``review_episode``.
    """
    _validate_promotion_lineage(finding, review_run, episode)
    validate_finding_grounding(finding, resolver)
    validate_causal_attribution(finding)
    validate_promotable_semantics(finding)
    validate_model_provenance(
        finding.interpretation_producer,
        model=finding.model,
        model_version=finding.model_version,
        prompt_version=finding.prompt_version,
        schema_version=review_run.schema_version,
    )
    return None


# ---------------------------------------------------------------------------
# Offline/manual orchestration
# ---------------------------------------------------------------------------


def _default_scope(episode: Episode) -> BehaviorScope:
    return BehaviorScope(
        channel=episode.scope_id,
        directedness="directed" if episode.participants else "unknown",
        topic_hint=episode.topic_hint,
    )


def _bind_candidate_to_authoritative_review(
    finding: ReviewFinding,
    *,
    review_run_id: str,
    episode_id: str,
) -> ReviewFinding:
    """Rebind untrusted engine/model candidate lineage before validation.

    Candidate engines may propose content only.  They never choose the ReviewRun
    or Episode that owns a persisted interpretation.
    """
    return replace(
        finding,
        review_run_id=review_run_id,
        episode_id=episode_id,
    )


def review_episode(
    episode: Episode,
    outcomes: Iterable[OutcomeObservation],
    store: ReviewStore,
    *,
    fact_envelopes: Mapping[tuple[EvidenceSourceType, str], object] | None = None,
    deterministic_engine: DeterministicReviewEngine | None = None,
    model_engine: StructuredModelReviewEngine | None = None,
    scope_factory: Callable[[Episode, ReviewFinding], BehaviorScope] | None = None,
    producer: str = "review_orchestrator",
    review_run_id: str | None = None,
    created_at: datetime | None = None,
) -> ReviewRun | None:
    """Run one offline/manual shadow Review for a single Episode.

    Returns ``None`` when eligibility is SKIP/DEFER.  Otherwise persists one
    immutable ReviewRun.  ReviewEvidence production is disabled and fail-closed
    pending an explicitly frozen promotion contract.  This is deliberately an
    explicit/offline call: it never blocks current-turn Host execution and never
    mutates Episode/Iris.
    """
    outcome_tuple = tuple(outcomes)
    decision = evaluate_review_eligibility(episode, outcome_tuple)
    if decision.decision is not ReviewEligibility.REVIEW:
        return None

    engine = deterministic_engine or DeterministicReviewEngine()
    fact_envelopes = dict(fact_envelopes or {})
    snapshot = ReviewInputSnapshot(episode, outcome_tuple, fact_envelopes)
    resolver = ReviewFactResolver(snapshot)
    input_hash = compute_input_snapshot_hash(episode, outcome_tuple, fact_envelopes)
    run_id = review_run_id or f"run:{uuid4().hex}"
    findings: list[ReviewFinding] = []
    failed = False

    try:
        deterministic_findings = tuple(engine.generate_findings(episode, outcome_tuple, review_run_id=run_id))
        findings.extend(
            _bind_candidate_to_authoritative_review(
                finding,
                review_run_id=run_id,
                episode_id=episode.episode_id,
            )
            for finding in deterministic_findings
        )
        if model_engine is not None:
            model_findings = tuple(model_engine.generate_candidate_findings(snapshot, deterministic_findings))
            findings.extend(
                _bind_candidate_to_authoritative_review(
                    finding,
                    review_run_id=run_id,
                    episode_id=episode.episode_id,
                )
                for finding in model_findings
            )
        for finding in findings:
            validate_finding_grounding(finding, resolver)
            validate_model_provenance(
                finding.interpretation_producer,
                model=finding.model,
                model_version=finding.model_version,
                prompt_version=finding.prompt_version,
                schema_version="1",
            )
    except Exception:
        failed = True
        findings = []

    if failed:
        status = ReviewStatus.FAILED
    elif not findings:
        status = ReviewStatus.INSUFFICIENT_EVIDENCE
    else:
        status = ReviewStatus.COMPLETED

    run = ReviewRun(
        review_run_id=run_id,
        episode_id=episode.episode_id,
        status=status,
        input_snapshot_hash=input_hash,
        created_at=created_at or datetime.now().astimezone(),
        findings=tuple(findings),
        producer=producer,
        schema_version="1",
        provenance=("p1d_review",),
    )
    store.record_review_run(run)

    # Review interpretation and Finding persistence are active.  Production
    # ReviewEvidence is intentionally disabled/fail-closed in P1d.2; no caller
    # supplied engine or producer label can cross this boundary.
    return run
