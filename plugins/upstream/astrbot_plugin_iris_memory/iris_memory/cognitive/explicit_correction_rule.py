"""P2r.1 deterministic explicit-correction promotion rule.

This module consumes only already committed, immutable P1/P2/P2r0 and
capture-time semantic authority.  It never calls a model and never writes
production ReviewEvidence.  The returned value is the existing P2a synthetic
candidate type, intended for isolated contract plumbing until a separately
frozen production enablement decision exists.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass

from .inbound_semantic_authority import (
    InboundSemanticActAuthorityStoreV1,
    InboundSemanticDecision,
    InboundSemanticKind,
)
from .outcome import OutcomeExplicitness, OutcomeKind
from .promotion_infrastructure import (
    CanonicalHashV1,
    P2CanonicalArtifactEncoderV1,
    P2PromotionStore,
    P2ReviewRunWithSnapshotV1,
    PromotionSnapshotArchiveV1,
    SyntheticPromotionCandidateV1,
    _decode_run,
    _decode_unpadded_base64url,
    _strict_envelope,
    build_synthetic_candidate,
)
from .reply_link_authority import (
    ExactHostReplyLinkStatus,
    P2r0Store,
    P2rReplyLinkFactArchiveV1,
    resolve_finding_host_fact,
)
from .review import (
    AttributionTargetType,
    BehaviorScope,
    CausalAttribution,
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
)
from .review_service import ReviewInputSnapshot, _canonical_json

RULE_ID = "EXPLICIT_CORRECTION_OF_EXACT_HOST_OUTPUT_V1"
RULE_STATEMENT = (
    "An explicit user correction was observed in a direct reply to this exact Host output."
)


class ExplicitCorrectionRuleIntegrityError(ValueError):
    """The frozen rule could not establish one exact authoritative chain."""


@dataclass(frozen=True, slots=True)
class ExplicitCorrectionPromotionCandidateV1:
    """A non-persisted candidate produced by the frozen P2r.1 rule."""

    rule_id: str
    synthetic_candidate: SyntheticPromotionCandidateV1
    outcome_observation_id: str
    source_event_id: str
    semantic_authority_id: str
    inbound_reply_fact_id: str
    host_output_fact_id: str
    exact_reply_link_id: str

    def __post_init__(self) -> None:
        if self.rule_id != RULE_ID:
            raise ExplicitCorrectionRuleIntegrityError("unexpected explicit-correction rule id")
        for value in (
            self.outcome_observation_id,
            self.source_event_id,
            self.semantic_authority_id,
            self.inbound_reply_fact_id,
            self.host_output_fact_id,
            self.exact_reply_link_id,
        ):
            if type(value) is not str or not value:
                raise ExplicitCorrectionRuleIntegrityError("candidate lineage identity is incomplete")

    @property
    def evidence(self) -> ReviewEvidence:
        return self.synthetic_candidate.evidence

    @property
    def finding(self) -> ReviewFinding:
        return self.synthetic_candidate.finding


def _authoritative_p2_run(
    run: ReviewRun,
    snapshot: ReviewInputSnapshot,
    p2_store: P2PromotionStore,
) -> tuple[P2ReviewRunWithSnapshotV1, P2CanonicalArtifactEncoderV1]:
    """Reconstruct the exact committed P2 Run and archived P1 bytes."""

    raw_commit = p2_store._run_commits.get(run.review_run_id)
    if not isinstance(raw_commit, Mapping):
        raise ExplicitCorrectionRuleIntegrityError("authoritative P2 Run is unavailable")
    wrapper_fields = _strict_envelope(raw_commit, "P2ReviewRunWithSnapshotV1")
    profile_hash = wrapper_fields.get("encoding_profile_hash")
    profile = p2_store._profile_objects.get(profile_hash)
    if profile is None:
        raise ExplicitCorrectionRuleIntegrityError("authoritative P2 profile is unavailable")
    encoder = P2CanonicalArtifactEncoderV1(profile)
    authoritative_run = _decode_run(wrapper_fields.get("run"))
    if authoritative_run != run:
        raise ExplicitCorrectionRuleIntegrityError("caller Run is not the committed immutable Run")

    archive_fields = _strict_envelope(wrapper_fields.get("archive"), "PromotionSnapshotArchiveV1")
    raw_wrapper = archive_fields.get("canonical_snapshot_json_utf8")
    if not isinstance(raw_wrapper, Mapping) or set(raw_wrapper) != {"$bytes"}:
        raise ExplicitCorrectionRuleIntegrityError("P2 snapshot archive bytes are unavailable")
    raw_snapshot = _decode_unpadded_base64url(raw_wrapper.get("$bytes"))
    p2_archive = PromotionSnapshotArchiveV1(
        archive_fields.get("schema_version"),
        archive_fields.get("review_run_id"),
        archive_fields.get("episode_id"),
        archive_fields.get("input_snapshot_hash"),
        raw_snapshot,
        archive_fields.get("archive_payload_hash"),
    )
    p2_run = P2ReviewRunWithSnapshotV1(
        wrapper_fields.get("schema_version"),
        authoritative_run,
        p2_archive,
        wrapper_fields.get("encoding_profile_hash"),
        wrapper_fields.get("logical_commit_hash"),
    )
    p2_run.validate(encoder)
    expected_snapshot = _canonical_json(snapshot.canonical_payload()).encode("utf-8")
    if raw_snapshot != expected_snapshot:
        raise ExplicitCorrectionRuleIntegrityError("caller snapshot is not the exact archived P1 snapshot")
    if "sha256:" + hashlib.sha256(raw_snapshot).hexdigest() != run.input_snapshot_hash:
        raise ExplicitCorrectionRuleIntegrityError("P1 snapshot hash lineage mismatch")
    persisted = p2_store.require_archive(run)
    if CanonicalHashV1.canonical_json_utf8(persisted) != CanonicalHashV1.canonical_json_utf8(raw_commit):
        raise ExplicitCorrectionRuleIntegrityError("P2 Run index differs from persisted authority")
    return p2_run, encoder


def _authoritative_archive(
    run: ReviewRun,
    p2_run: P2ReviewRunWithSnapshotV1,
    p2r0_store: P2r0Store,
) -> P2rReplyLinkFactArchiveV1:
    archives = tuple(item for item in p2r0_store.archives if item.review_run_id == run.review_run_id)
    if len(archives) != 1:
        raise ExplicitCorrectionRuleIntegrityError("exactly one P2r0 archive is required")
    archive = archives[0]
    if (
        archive.episode_id != run.episode_id
        or archive.input_snapshot_hash != run.input_snapshot_hash
        or archive.p2_run_snapshot_logical_commit_hash != p2_run.logical_commit_hash
    ):
        raise ExplicitCorrectionRuleIntegrityError("P2r0 archive is not on the exact P2 lineage")
    return archive


def _authoritative_finding(
    finding: ReviewFinding,
    run: ReviewRun,
    encoder: P2CanonicalArtifactEncoderV1,
) -> tuple[ReviewEvidenceRef, ReviewEvidenceRef]:
    if (
        finding.review_run_id != run.review_run_id
        or finding.episode_id != run.episode_id
        or finding.dimension is not ReviewDimension.ATTRIBUTION
        or finding.finding_type is not FindingType.LOCAL_OBSERVATION
        or finding.interpretation_producer is not InterpretationProducer.DETERMINISTIC
        or finding.producer != "deterministic_review_engine"
        or finding.causal_attribution is not CausalAttribution.NONE
        or finding.attributed_to.target_type is not AttributionTargetType.HOST_RESULT
    ):
        raise ExplicitCorrectionRuleIntegrityError("Finding is not the frozen correction-attribution shape")
    matching = tuple(item for item in run.findings if item.finding_id == finding.finding_id)
    if len(matching) != 1:
        raise ExplicitCorrectionRuleIntegrityError("Finding is not uniquely contained in the authoritative Run")
    if CanonicalHashV1.canonical_json_utf8(encoder.encode(matching[0])) != CanonicalHashV1.canonical_json_utf8(encoder.encode(finding)):
        raise ExplicitCorrectionRuleIntegrityError("caller Finding differs from authoritative Finding")
    host_refs = tuple(
        ref for ref in finding.evidence_refs if ref.source_type is EvidenceSourceType.HOST_RESULT
    )
    outcome_refs = tuple(
        ref for ref in finding.evidence_refs if ref.source_type is EvidenceSourceType.OUTCOME_OBSERVATION
    )
    if (
        len(finding.evidence_refs) != 2
        or len(host_refs) != 1
        or len(outcome_refs) != 1
        or host_refs[0].evidence_kind is not EvidenceKind.STRUCTURAL
        or outcome_refs[0].evidence_kind is not EvidenceKind.EXPLICIT
        or host_refs[0].ref_id != finding.attributed_to.ref_id
    ):
        raise ExplicitCorrectionRuleIntegrityError("Finding evidence references are not uniquely correction-shaped")
    return host_refs[0], outcome_refs[0]


def _build_candidate(
    run: ReviewRun,
    snapshot: ReviewInputSnapshot,
    finding: ReviewFinding,
    *,
    p2_store: P2PromotionStore,
    p2r0_store: P2r0Store,
    semantic_store: InboundSemanticActAuthorityStoreV1,
) -> ExplicitCorrectionPromotionCandidateV1:
    if (
        type(run) is not ReviewRun
        or type(snapshot) is not ReviewInputSnapshot
        or type(finding) is not ReviewFinding
        or type(p2_store) is not P2PromotionStore
        or p2_store.root != "production"
        or p2_store._synthetic_enabled
        or type(p2r0_store) is not P2r0Store
        or type(semantic_store) is not InboundSemanticActAuthorityStoreV1
    ):
        raise ExplicitCorrectionRuleIntegrityError("rule requires authoritative production stores and P1 artifacts")

    p2_run, encoder = _authoritative_p2_run(run, snapshot, p2_store)
    archive = _authoritative_archive(run, p2_run, p2r0_store)
    host_ref, outcome_ref = _authoritative_finding(finding, p2_run.run, encoder)
    outcomes = tuple(
        item for item in snapshot.outcomes if item.observation_id == outcome_ref.ref_id
    )
    if len(outcomes) != 1:
        raise ExplicitCorrectionRuleIntegrityError("exactly one OutcomeObservation is required")
    outcome = outcomes[0]
    if (
        outcome.kind is not OutcomeKind.EXPLICIT_CORRECTION
        or outcome.explicitness is not OutcomeExplicitness.EXPLICIT
        or outcome.target_episode_id != run.episode_id
        or type(outcome.source_event_id) is not str
        or not outcome.source_event_id
    ):
        raise ExplicitCorrectionRuleIntegrityError("Outcome is not an exact explicit correction with source event")
    source_event_id = outcome.source_event_id

    authorities = tuple(
        item for item in semantic_store.authorities if item.source_event_id == source_event_id
    )
    if len(authorities) != 1:
        raise ExplicitCorrectionRuleIntegrityError("exactly one semantic authority is required")
    authority = authorities[0]
    if (
        authority.semantic_kind is not InboundSemanticKind.EXPLICIT_CORRECTION
        or authority.decision is not InboundSemanticDecision.MATCH
    ):
        raise ExplicitCorrectionRuleIntegrityError("semantic authority is not an explicit-correction MATCH")
    profile = semantic_store.get_profile(authority.evaluator_profile_hash)
    if profile is None or profile.profile_id != authority.evaluator_profile_id:
        raise ExplicitCorrectionRuleIntegrityError("semantic authority profile lineage is unavailable")

    inbound_matches = tuple(
        item
        for item in archive.inbound_reply_facts
        if item.fact_id == authority.inbound_reply_fact_id
        and item.source_event_id == source_event_id
        and item.source_platform_message_identity == authority.source_platform_message_identity
    )
    if len(inbound_matches) != 1:
        raise ExplicitCorrectionRuleIntegrityError("semantic authority does not bind one exact inbound fact")
    inbound = inbound_matches[0]
    if not any(
        ref.source_event_id == source_event_id
        for ref in snapshot.episode.event_refs
    ):
        raise ExplicitCorrectionRuleIntegrityError("inbound source event is outside the archived Episode")

    host = resolve_finding_host_fact(finding, archive, p2_authority=p2_store)
    if host is None or host.host_output_event_ref_id != host_ref.ref_id:
        raise ExplicitCorrectionRuleIntegrityError("Finding Host attribution did not resolve exactly")
    if not any(item.fact_id == host.fact_id for item in archive.host_output_facts):
        raise ExplicitCorrectionRuleIntegrityError("resolved Host fact is outside the archive")
    reply_link = archive.derive_exact_reply_link(inbound.fact_id, host)
    if (
        reply_link.status != ExactHostReplyLinkStatus.EXACT_REPLY_LINK.value
        or reply_link.inbound_reply_fact_id != inbound.fact_id
        or reply_link.host_output_fact_id != host.fact_id
        or reply_link.archive_id != archive.archive_id
        or reply_link.episode_id != run.episode_id
    ):
        raise ExplicitCorrectionRuleIntegrityError("exact reply link is unavailable or points elsewhere")

    proposition = LocalEvidenceProposition(
        dimension=ReviewDimension.ATTRIBUTION,
        context_refs=(run.review_run_id, run.input_snapshot_hash),
        behavior_refs=(host.fact_id, reply_link.link_id),
        observation_refs=(outcome.observation_id, source_event_id, authority.authority_id, inbound.fact_id),
        statement=RULE_STATEMENT,
    )
    scope = BehaviorScope(
        channel=snapshot.episode.scope_id,
        directedness="directed" if snapshot.episode.participants else "unknown",
        topic_hint=snapshot.episode.topic_hint,
    )
    evidence = ReviewEvidence(
        evidence_id="candidate",
        source_review_run_id=run.review_run_id,
        source_finding_id=finding.finding_id,
        source_episode_id=run.episode_id,
        dimension=ReviewDimension.ATTRIBUTION,
        proposition=proposition,
        scope=scope,
        evidence_refs=finding.evidence_refs,
        confidence=finding.confidence,
        causal_attribution=CausalAttribution.NONE,
        attributed_to=finding.attributed_to,
        interpretation_producer=InterpretationProducer.DETERMINISTIC,
        created_at=finding.created_at,
        producer=RULE_ID,
        provenance=(
            f"rule:{RULE_ID}",
            f"p2_snapshot_commit:{p2_run.logical_commit_hash}",
            f"p2r0_archive:{archive.archive_id}",
            f"semantic_profile:{authority.evaluator_profile_hash}",
            f"outcome_observation:{outcome.observation_id}",
            f"source_event:{source_event_id}",
            f"semantic_authority:{authority.authority_id}",
            f"inbound_reply_fact:{inbound.fact_id}",
            f"host_output_fact:{host.fact_id}",
            f"exact_reply_link:{reply_link.link_id}",
        ),
    )
    synthetic = build_synthetic_candidate(evidence, finding, encoder)
    return ExplicitCorrectionPromotionCandidateV1(
        RULE_ID,
        synthetic,
        outcome.observation_id,
        source_event_id,
        authority.authority_id,
        inbound.fact_id,
        host.fact_id,
        reply_link.link_id,
    )


def build_explicit_correction_candidate(
    run: ReviewRun,
    snapshot: ReviewInputSnapshot,
    finding: ReviewFinding,
    *,
    p2_store: P2PromotionStore,
    p2r0_store: P2r0Store,
    semantic_store: InboundSemanticActAuthorityStoreV1,
) -> ExplicitCorrectionPromotionCandidateV1 | None:
    """Return one candidate, or ``None`` for every ambiguous/invalid join.

    The function is intentionally not wired into ``review_episode`` or any
    production store.  A future contract may feed this candidate to the
    existing isolated synthetic writer after an explicit production decision.
    """

    try:
        return _build_candidate(
            run,
            snapshot,
            finding,
            p2_store=p2_store,
            p2r0_store=p2r0_store,
            semantic_store=semantic_store,
        )
    except Exception:  # noqa: BLE001 - every ambiguity is fail-closed
        return None


evaluate_explicit_correction_rule = build_explicit_correction_candidate


__all__ = [
    "RULE_ID",
    "RULE_STATEMENT",
    "ExplicitCorrectionPromotionCandidateV1",
    "ExplicitCorrectionRuleIntegrityError",
    "build_explicit_correction_candidate",
    "evaluate_explicit_correction_rule",
]
