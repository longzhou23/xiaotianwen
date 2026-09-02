"""Frozen P1c Review/ReviewEvidence contracts implemented for P1d."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from .contracts import canonical_tuple


class ReviewEligibility(str, Enum):
    SKIP = "SKIP"
    REVIEW = "REVIEW"
    DEFER = "DEFER"


class ReviewStatus(str, Enum):
    COMPLETED = "COMPLETED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    FAILED = "FAILED"


class FindingType(str, Enum):
    LOCAL_OBSERVATION = "LOCAL_OBSERVATION"
    INTERPRETATION = "INTERPRETATION"
    CAUSAL_HYPOTHESIS = "CAUSAL_HYPOTHESIS"


class ReviewDimension(str, Enum):
    TRIGGER = "TRIGGER"
    PARTICIPATION = "PARTICIPATION"
    INTENT = "INTENT"
    GROUNDING = "GROUNDING"
    REALIZATION = "REALIZATION"
    TOOL_USE = "TOOL_USE"
    TIMING = "TIMING"
    SILENCE = "SILENCE"
    ATTRIBUTION = "ATTRIBUTION"


class InterpretationProducer(str, Enum):
    DETERMINISTIC = "DETERMINISTIC"
    MODEL = "MODEL"


class EvidenceKind(str, Enum):
    EXPLICIT = "EXPLICIT"
    STRUCTURAL = "STRUCTURAL"
    CORRELATIONAL = "CORRELATIONAL"


class EvidenceSourceType(str, Enum):
    EPISODE_EVENT = "EPISODE_EVENT"
    OUTCOME_OBSERVATION = "OUTCOME_OBSERVATION"
    BEHAVIOR_TRACE = "BEHAVIOR_TRACE"
    HOST_RESULT = "HOST_RESULT"
    TOOL_RESULT = "TOOL_RESULT"


class CausalAttribution(str, Enum):
    NONE = "NONE"
    POSSIBLE = "POSSIBLE"
    SUPPORTED = "SUPPORTED"
    DIRECT = "DIRECT"


class Confidence(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class AttributionTargetType(str, Enum):
    BEHAVIOR_TRACE = "BEHAVIOR_TRACE"
    HOST_RESULT = "HOST_RESULT"
    TOOL_RESULT = "TOOL_RESULT"
    OUTCOME_OBSERVATION = "OUTCOME_OBSERVATION"
    EPISODE_EVENT = "EPISODE_EVENT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ReviewEligibilityDecision:
    episode_id: str
    decision: ReviewEligibility
    reason: str
    evidence_refs: tuple[str, ...] = ()
    decided_at: datetime = datetime.now().astimezone()
    producer: str = "deterministic_eligibility"

    def __post_init__(self) -> None:
        if not self.episode_id or not self.reason:
            raise ValueError("episode_id and reason are required")
        object.__setattr__(self, "evidence_refs", canonical_tuple(self.evidence_refs))


@dataclass(frozen=True, slots=True)
class ReviewEvidenceRef:
    ref_id: str
    source_type: EvidenceSourceType
    evidence_kind: EvidenceKind

    def __post_init__(self) -> None:
        if not self.ref_id:
            raise ValueError("ref_id is required")


@dataclass(frozen=True, slots=True)
class AttributionRef:
    target_type: AttributionTargetType
    ref_id: str

    def __post_init__(self) -> None:
        if not self.ref_id:
            raise ValueError("ref_id is required")


@dataclass(frozen=True, slots=True)
class BehaviorScope:
    channel: str
    directedness: str
    intent_domain: str | None = None
    topic_hint: str | None = None
    tool_used: bool | None = None

    def __post_init__(self) -> None:
        if not self.channel or not self.directedness:
            raise ValueError("channel and directedness are required")


@dataclass(frozen=True, slots=True)
class ReviewFinding:
    finding_id: str
    review_run_id: str
    episode_id: str
    dimension: ReviewDimension
    finding_type: FindingType
    claim: str
    evidence_refs: tuple[ReviewEvidenceRef, ...]
    attributed_to: AttributionRef
    confidence: Confidence
    causal_attribution: CausalAttribution
    interpretation_producer: InterpretationProducer
    created_at: datetime = datetime.now().astimezone()
    producer: str = "review_engine"
    model: str | None = None
    model_version: str | None = None
    prompt_version: str | None = None
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.finding_id or not self.review_run_id or not self.episode_id:
            raise ValueError("finding_id, review_run_id, episode_id are required")
        if not self.evidence_refs:
            raise ValueError("evidence_refs cannot be empty")
        if not self.claim:
            raise ValueError("claim cannot be empty")
        object.__setattr__(self, "evidence_refs", canonical_tuple(self.evidence_refs))
        object.__setattr__(self, "provenance", canonical_tuple(self.provenance))


@dataclass(frozen=True, slots=True)
class LocalEvidenceProposition:
    dimension: ReviewDimension
    context_refs: tuple[str, ...] = ()
    behavior_refs: tuple[str, ...] = ()
    observation_refs: tuple[str, ...] = ()
    statement: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "context_refs", canonical_tuple(self.context_refs))
        object.__setattr__(self, "behavior_refs", canonical_tuple(self.behavior_refs))
        object.__setattr__(self, "observation_refs", canonical_tuple(self.observation_refs))
        if not self.statement:
            raise ValueError("statement cannot be empty")


@dataclass(frozen=True, slots=True)
class ReviewEvidence:
    evidence_id: str
    source_review_run_id: str
    source_finding_id: str
    source_episode_id: str
    dimension: ReviewDimension
    proposition: LocalEvidenceProposition
    scope: BehaviorScope
    evidence_refs: tuple[ReviewEvidenceRef, ...]
    confidence: Confidence
    causal_attribution: CausalAttribution
    attributed_to: AttributionRef
    interpretation_producer: InterpretationProducer
    created_at: datetime = datetime.now().astimezone()
    producer: str = "review_engine"
    model: str | None = None
    model_version: str | None = None
    prompt_version: str | None = None
    schema_version: str = "1"
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.evidence_id or not self.source_review_run_id or not self.source_finding_id or not self.source_episode_id:
            raise ValueError("evidence identity fields are required")
        object.__setattr__(self, "evidence_refs", canonical_tuple(self.evidence_refs))
        object.__setattr__(self, "provenance", canonical_tuple(self.provenance))


@dataclass(frozen=True, slots=True)
class ReviewRun:
    review_run_id: str
    episode_id: str
    status: ReviewStatus
    input_snapshot_hash: str
    created_at: datetime = datetime.now().astimezone()
    model: str | None = None
    model_version: str | None = None
    prompt_version: str | None = None
    schema_version: str = "1"
    raw_output_digest: str | None = None
    findings: tuple[ReviewFinding, ...] = ()
    producer: str = "review_engine"
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.review_run_id or not self.episode_id or not self.input_snapshot_hash:
            raise ValueError("review_run_id, episode_id, input_snapshot_hash are required")
        object.__setattr__(self, "findings", canonical_tuple(self.findings))
        object.__setattr__(self, "provenance", canonical_tuple(self.provenance))
