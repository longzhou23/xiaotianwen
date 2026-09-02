"""Frozen contracts for the first Cognitive Runtime compatibility slice.

The contracts deliberately model only P0 foundations.  They do not contain
relationship, affect, behavioural-prior, persona, repair-writeback, or LLM
decision state.  Declared tuple/mapping/set container fields are canonicalized
at the constructor boundary so another module cannot silently mutate a state it
does not own.  Unknown ``Any`` custom objects are not recursively deep-frozen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Protocol, runtime_checkable
from uuid import uuid4


class CognitiveContractError(ValueError):
    """Raised when data cannot satisfy a Cognitive Runtime contract."""


class EntityType(str, Enum):
    AGENT = "agent"
    PERSON = "person"
    GROUP = "group"
    UNKNOWN = "unknown"


class IdentityClaimStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    POSSIBLE = "POSSIBLE"
    REJECTED = "REJECTED"
    REVOKED = "REVOKED"


class Perspective(str, Enum):
    AUTOBIOGRAPHICAL = "autobiographical"
    INTERPERSONAL = "interpersonal"
    SHARED_GROUP = "shared_group"
    WORLD_FACT = "world_fact"
    HEARSAY = "hearsay"
    UNRESOLVED = "unresolved"


class ExitReason(str, Enum):
    """Explicit fail-closed exits reserved by the runtime architecture."""

    TRIGGER_NO = "TRIGGER_NO"
    NO_PARTICIPATION = "NO_PARTICIPATION"
    WAIT_SELECTED = "WAIT_SELECTED"
    NO_INTENT = "NO_INTENT"
    GROUNDING_FAILED = "GROUNDING_FAILED"
    GROUNDING_DEGRADED = "GROUNDING_DEGRADED"
    SILENCE_SELECTED = "SILENCE_SELECTED"
    REALIZATION_FAILED = "REALIZATION_FAILED"
    RUNTIME_ERROR = "RUNTIME_ERROR"
    # Deprecated compatibility value.  New proposal traces must never use SEND;
    # it only describes a legacy observation that non-empty output reached Host.
    SEND = "SEND"


class ParticipationDecision(str, Enum):
    PARTICIPATE = "PARTICIPATE"
    SILENCE = "SILENCE"
    WAIT = "WAIT"


class SocialAction(str, Enum):
    ACKNOWLEDGE = "ACKNOWLEDGE"
    ASK = "ASK"
    CLARIFY = "CLARIFY"
    INFORM = "INFORM"
    SHARE = "SHARE"
    TEASE = "TEASE"
    CONTINUE_JOKE = "CONTINUE_JOKE"
    DISAGREE = "DISAGREE"
    SUPPORT = "SUPPORT"
    CORRECT = "CORRECT"
    REACT = "REACT"
    SILENCE = "SILENCE"


class IntentDomain(str, Enum):
    """Small deterministic domain hint; it is not a knowledge source."""

    OBSERVATION_SCHEDULE = "OBSERVATION_SCHEDULE"
    INSTALLATION_PROCEDURE = "INSTALLATION_PROCEDURE"
    SELF_IDENTITY = "SELF_IDENTITY"
    RECOMMENDATION = "RECOMMENDATION"
    GENERAL_INFORMATION = "GENERAL_INFORMATION"
    UNKNOWN = "UNKNOWN"


class GroundingStatus(str, Enum):
    SUFFICIENT = "SUFFICIENT"
    INSUFFICIENT = "INSUFFICIENT"
    DEGRADED = "DEGRADED"


class GroundingEnforcement(str, Enum):
    """How much of a grounding policy is enforced beyond a prompt instruction."""

    NOT_APPLIED = "NOT_APPLIED"
    PROMPT_CONSTRAINED = "PROMPT_CONSTRAINED"
    STRUCTURED = "STRUCTURED"
    POST_VALIDATED = "POST_VALIDATED"
    TOOL_GROUNDED = "TOOL_GROUNDED"


class RuntimeMode(str, Enum):
    """How a cognitive proposal may affect the existing AstrBot host path."""

    SHADOW = "SHADOW"
    GUARD = "GUARD"
    AUTHORITATIVE = "AUTHORITATIVE"


@dataclass(frozen=True, slots=True)
class EventExecutionContext:
    """Immutable event-start mode snapshot; all lifecycle reads use this context."""

    event_id: str
    runtime_mode: RuntimeMode
    started_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.event_id or not self.event_id.strip():
            raise CognitiveContractError("event execution context requires an event id")
        if self.started_at is None:
            object.__setattr__(self, "started_at", datetime.now(timezone.utc))


class OutputState(str, Enum):
    """Generation lifecycle; none of these values prove QQ delivery."""

    NO_OUTPUT = "NO_OUTPUT"
    OUTPUT_PROPOSED = "OUTPUT_PROPOSED"
    OUTPUT_READY = "OUTPUT_READY"


class OutputProducer(str, Enum):
    """Authority that actually produced the observed Host output."""

    LEGACY_HOST = "LEGACY_HOST"
    COGNITIVE_REALIZER = "COGNITIVE_REALIZER"
    UNKNOWN_HOST = "UNKNOWN_HOST"


class TraceStage(str, Enum):
    """Append-only lifecycle stage for diagnostic records, not Episode state."""

    PROPOSAL = "PROPOSAL"
    HOST_OUTPUT = "HOST_OUTPUT"
    DISPATCH = "DISPATCH"
    DELIVERY = "DELIVERY"


class DeliveryStatus(str, Enum):
    """Platform-delivery observation, deliberately separate from output creation."""

    UNOBSERVED = "UNOBSERVED"
    DELIVERED = "DELIVERED"
    DELIVERY_FAILED = "DELIVERY_FAILED"


class DivergenceType(str, Enum):
    MATCH_SILENCE = "MATCH_SILENCE"
    MATCH_REPLY = "MATCH_REPLY"
    LEGACY_REPLY_COGNITIVE_SILENCE = "LEGACY_REPLY_COGNITIVE_SILENCE"
    LEGACY_SILENCE_COGNITIVE_REPLY = "LEGACY_SILENCE_COGNITIVE_REPLY"
    GROUNDING_DISAGREEMENT = "GROUNDING_DISAGREEMENT"
    UNRESOLVED = "UNRESOLVED"


def deep_freeze(value: Any) -> Any:
    """Recursively detach contracts from mutable caller-owned values."""
    if isinstance(value, Mapping):
        return MappingProxyType({deep_freeze(key): deep_freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(deep_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(deep_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(deep_freeze(item) for item in value)
    if isinstance(value, frozenset):
        return frozenset(deep_freeze(item) for item in value)
    return value


def canonical_tuple(value: Any) -> tuple[Any, ...]:
    """Canonicalize a caller-provided sequence into a deeply frozen tuple."""
    return tuple(deep_freeze(item) for item in value)


def to_json_safe(value: Any) -> Any:
    """Convert runtime contract containers into JSON-safe plain Python data."""
    if isinstance(value, Mapping):
        return {to_json_safe(k): to_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [to_json_safe(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    return value



def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Prevent mutable mappings from becoming hidden cross-owner state."""
    frozen = deep_freeze(value)
    if not isinstance(frozen, Mapping):  # pragma: no cover - defensive typing guard
        raise CognitiveContractError("expected a mapping to freeze")
    return frozen


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _validate_entity_id(entity_id: str) -> str:
    if not isinstance(entity_id, str) or not entity_id.strip():
        raise CognitiveContractError("entity_id must be a non-empty string")
    value = entity_id.strip()
    if ":" not in value:
        raise CognitiveContractError("entity_id must use '<type>:<id>' form")
    namespace, local_id = value.split(":", 1)
    if namespace not in {item.value for item in EntityType} or not local_id:
        raise CognitiveContractError("entity_id has an unsupported namespace")
    return value


@dataclass(frozen=True, slots=True)
class EntityReference:
    """A resolved reference with the evidence that supports it."""

    entity_id: str
    source: str
    confidence: float
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "entity_id", _validate_entity_id(self.entity_id))
        if not self.source:
            raise CognitiveContractError("entity reference source is required")
        if not 0.0 <= self.confidence <= 1.0:
            raise CognitiveContractError("entity reference confidence must be in [0, 1]")
        object.__setattr__(self, "evidence", canonical_tuple(self.evidence))


@dataclass(frozen=True, slots=True)
class CanonicalEntity:
    """Identity-owned stable entity record; names remain aliases, never IDs."""

    id: str
    aliases: tuple[str, ...] = ()
    platform_ids: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _validate_entity_id(self.id))
        aliases = tuple(alias.strip() for alias in self.aliases if alias and alias.strip())
        object.__setattr__(self, "aliases", aliases)
        object.__setattr__(self, "platform_ids", _freeze_mapping(self.platform_ids))


@dataclass(frozen=True, slots=True)
class IdentityClaim:
    """Reversible evidence for a mention-to-entity association."""

    mention: str
    candidate_entity: str
    evidence: tuple[str, ...]
    confidence: float
    source: str
    status: IdentityClaimStatus = IdentityClaimStatus.POSSIBLE
    created_at: datetime = field(default_factory=_utcnow)

    def __post_init__(self) -> None:
        if not self.mention or not self.mention.strip():
            raise CognitiveContractError("identity claim mention is required")
        object.__setattr__(self, "candidate_entity", _validate_entity_id(self.candidate_entity))
        evidence = canonical_tuple(self.evidence)
        if not evidence:
            raise CognitiveContractError("identity claim evidence is required")
        object.__setattr__(self, "evidence", evidence)
        if not self.source:
            raise CognitiveContractError("identity claim source is required")
        if not 0.0 <= self.confidence <= 1.0:
            raise CognitiveContractError("identity claim confidence must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class IdentityConfig:
    """Identity-owned configuration.  Persona remains owned by AstrBot."""

    self_entity: str = "agent:xiaotianwen"
    self_aliases: tuple[str, ...] = ("小天文",)

    def __post_init__(self) -> None:
        self_entity = _validate_entity_id(self.self_entity)
        if not self_entity.startswith("agent:"):
            raise CognitiveContractError("self_entity must use the agent namespace")
        object.__setattr__(self, "self_entity", self_entity)
        object.__setattr__(
            self,
            "self_aliases",
            tuple(alias.strip() for alias in self.self_aliases if alias and alias.strip()),
        )


@runtime_checkable
class IdentityStore(Protocol):
    """Replaceable read/write boundary; P0 supplies only an in-process store."""

    @property
    def self_entity(self) -> str: ...

    def resolve_alias(self, mention: str) -> EntityReference | None: ...

    def resolve_platform_id(self, platform: str, platform_id: str) -> EntityReference | None: ...

    def entities(self) -> tuple[CanonicalEntity, ...]: ...

    def all_claims(self) -> tuple[IdentityClaim, ...]: ...


@dataclass(frozen=True, slots=True)
class ResolvedEvent:
    """Normalized event data before it is handed to Iris or a runtime view."""

    event_id: str
    source: str
    occurred_at: datetime
    session_id: str
    mode: str
    content: str
    actor: EntityReference | None
    mentioned_entities: tuple[EntityReference, ...] = ()
    reply_to: EntityReference | None = None
    raw_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.event_id or not self.source or not self.session_id:
            raise CognitiveContractError("event_id, source, and session_id are required")
        if not self.mode:
            raise CognitiveContractError("event mode is required")
        object.__setattr__(self, "mentioned_entities", tuple(self.mentioned_entities))
        object.__setattr__(self, "raw_metadata", _freeze_mapping(self.raw_metadata))


@dataclass(frozen=True, slots=True)
class CanonicalExperience:
    """Immutable, adapter-owned representation of a normalized experience."""

    id: str
    event: ResolvedEvent
    subject: EntityReference | None
    perspective: Perspective
    provenance: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.id:
            raise CognitiveContractError("experience id is required")
        provenance = canonical_tuple(self.provenance)
        if not provenance:
            raise CognitiveContractError("experience provenance is required")
        object.__setattr__(self, "provenance", provenance)


@dataclass(frozen=True, slots=True)
class IrisPreprocessResult:
    """Structured adapter metadata for a new Iris write; raw content is untouched."""

    experience: CanonicalExperience
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class RuntimeMemoryView:
    """Read-only projection of an Iris result; never a raw-memory writeback."""

    memory_id: str
    raw_content: str
    content: str
    subject: EntityReference | None
    perspective: Perspective
    provenance: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.memory_id:
            raise CognitiveContractError("memory_id is required")
        provenance = canonical_tuple(self.provenance)
        if not provenance:
            raise CognitiveContractError("memory view provenance is required")
        object.__setattr__(self, "provenance", provenance)


@dataclass(frozen=True, slots=True)
class TriggerDecision:
    """Trigger-only result; it intentionally does not choose a social action."""

    should_start_loop: bool
    reason: str
    score: int
    exit_reason: ExitReason | None = None

    def __post_init__(self) -> None:
        if not self.reason:
            raise CognitiveContractError("trigger reason is required")
        if self.should_start_loop and self.exit_reason is not None:
            raise CognitiveContractError("trigger YES cannot contain an exit reason")
        if not self.should_start_loop and self.exit_reason != ExitReason.TRIGGER_NO:
            raise CognitiveContractError("trigger NO must use TRIGGER_NO")


@dataclass(frozen=True, slots=True)
class Situation:
    """Situation-builder-owned short-lived state; it is not a Memory record."""

    episode_id: str
    shared_focus_type: str
    shared_focus_summary: str
    mode: str
    active_entities: tuple[str, ...]
    current_topic: tuple[str, ...]
    self_already_spoke: bool
    self_last_action: str | None
    self_last_action_at: datetime | None
    unresolved_items: tuple[str, ...]
    updated_at: datetime

    def __post_init__(self) -> None:
        if not self.episode_id or not self.mode:
            raise CognitiveContractError("situation episode_id and mode are required")
        object.__setattr__(
            self,
            "active_entities",
            tuple(_validate_entity_id(entity) for entity in self.active_entities),
        )
        object.__setattr__(self, "current_topic", canonical_tuple(self.current_topic))
        object.__setattr__(self, "unresolved_items", canonical_tuple(self.unresolved_items))


@dataclass(frozen=True, slots=True)
class SituationLite:
    """Cheap per-event view, owned only by Situation Builder in this process."""

    scope_id: str
    channel: str
    active_entities: tuple[str, ...]
    reply_chain: tuple[str, ...]
    recent_self_action: str | None
    self_recently_spoke: bool | None
    current_topic_hint: str | None
    message_velocity: int
    last_activity_at: datetime
    ongoing_episode_hint: str | None
    last_self_action_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.scope_id or not self.channel:
            raise CognitiveContractError("SituationLite scope_id and channel are required")
        if self.message_velocity < 1:
            raise CognitiveContractError("SituationLite message_velocity must be positive")
        object.__setattr__(self, "active_entities", tuple(_validate_entity_id(v) for v in self.active_entities))
        object.__setattr__(self, "reply_chain", tuple(_validate_entity_id(v) for v in self.reply_chain))


@dataclass(frozen=True, slots=True)
class LegacyProactiveSignals:
    """Read-only projection of existing Iris proactive state; no rewrite authority."""

    activation_signal: str | None = None
    willingness: str | None = None
    threshold: Mapping[str, Any] = field(default_factory=dict)
    cooldown: bool = False
    consecutive_reply_penalty: int = 0
    skip_signal: bool = False
    topic_drift_signal: bool = False
    post_evaluation_signal: bool = False

    def __post_init__(self) -> None:
        if self.consecutive_reply_penalty < 0:
            raise CognitiveContractError("consecutive_reply_penalty cannot be negative")
        object.__setattr__(self, "threshold", _freeze_mapping(self.threshold))


@dataclass(frozen=True, slots=True)
class TriggerSnapshot:
    """Trigger input: prior committed read-only state plus this exact event."""

    previous_committed_state: Mapping[str, Any]
    experience: CanonicalExperience
    situation: SituationLite
    legacy_signals: LegacyProactiveSignals

    def __post_init__(self) -> None:
        object.__setattr__(self, "previous_committed_state", _freeze_mapping(self.previous_committed_state))


@dataclass(frozen=True, slots=True)
class SituationFull:
    """Trigger-YES-only read view; it does not own or summarize long-term memory."""

    experience: CanonicalExperience
    lite: SituationLite
    runtime_memory_view: tuple[RuntimeMemoryView, ...]
    committed_affect: Mapping[str, Any]
    committed_relationship: Mapping[str, Any]
    behavioral_prior: Mapping[str, Any]
    persona_read_only: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "runtime_memory_view", tuple(self.runtime_memory_view))
        object.__setattr__(self, "committed_affect", _freeze_mapping(self.committed_affect))
        object.__setattr__(self, "committed_relationship", _freeze_mapping(self.committed_relationship))
        object.__setattr__(self, "behavioral_prior", _freeze_mapping(self.behavioral_prior))
        object.__setattr__(self, "persona_read_only", _freeze_mapping(self.persona_read_only))


@dataclass(frozen=True, slots=True)
class ParticipationResult:
    decision: ParticipationDecision
    reason: str
    exit_reason: ExitReason | None = None

    def __post_init__(self) -> None:
        if not self.reason:
            raise CognitiveContractError("participation reason is required")
        expected = {
            ParticipationDecision.WAIT: ExitReason.WAIT_SELECTED,
        }.get(self.decision)
        if expected is not None and self.exit_reason != expected:
            raise CognitiveContractError("non-participation must have its matching exit reason")
        if self.decision is ParticipationDecision.SILENCE and self.exit_reason not in {
            ExitReason.SILENCE_SELECTED,
            ExitReason.NO_PARTICIPATION,
        }:
            raise CognitiveContractError("silence must have an explicit non-participation exit reason")
        if self.decision is ParticipationDecision.PARTICIPATE and self.exit_reason is not None:
            raise CognitiveContractError("participation YES cannot contain an exit reason")


@dataclass(frozen=True, slots=True)
class Intent:
    """Action-only dialogue intent.  It is not permission to assert a fact."""

    action: SocialAction | None
    target_entity: EntityReference | None
    reason: str
    basis: tuple[str, ...]
    confidence: float
    exit_reason: ExitReason | None = None
    domain: IntentDomain = IntentDomain.UNKNOWN

    def __post_init__(self) -> None:
        if not self.reason:
            raise CognitiveContractError("intent reason is required")
        basis = canonical_tuple(self.basis)
        if not basis:
            raise CognitiveContractError("intent basis is required")
        object.__setattr__(self, "basis", basis)
        if not 0.0 <= self.confidence <= 1.0:
            raise CognitiveContractError("intent confidence must be in [0, 1]")
        if self.action is None and self.exit_reason != ExitReason.NO_INTENT:
            raise CognitiveContractError("empty intent must fail with NO_INTENT")
        if self.action is not None and self.exit_reason is not None:
            raise CognitiveContractError("actionable intent cannot contain an exit reason")


@dataclass(frozen=True, slots=True)
class GroundingResult:
    """Evidence guard between an action and realization."""

    semantic_requirement: str
    status: GroundingStatus
    basis: tuple[str, ...]
    allowed_claims: tuple[str, ...]
    blocked_claims: tuple[str, ...]
    required_tool: str | None
    confidence: float
    requested_enforcement: GroundingEnforcement = GroundingEnforcement.PROMPT_CONSTRAINED

    def __post_init__(self) -> None:
        basis = canonical_tuple(self.basis)
        allowed_claims = canonical_tuple(self.allowed_claims)
        blocked_claims = canonical_tuple(self.blocked_claims)
        if not self.semantic_requirement or not basis:
            raise CognitiveContractError("grounding requirement and basis are required")
        object.__setattr__(self, "basis", basis)
        object.__setattr__(self, "allowed_claims", allowed_claims)
        object.__setattr__(self, "blocked_claims", blocked_claims)
        if not 0.0 <= self.confidence <= 1.0:
            raise CognitiveContractError("grounding confidence must be in [0, 1]")
        if self.status is GroundingStatus.INSUFFICIENT and not self.required_tool:
            raise CognitiveContractError("insufficient grounding must name its required tool")

    @property
    def enforcement(self) -> GroundingEnforcement:
        """Compatibility alias for requested, never applied, enforcement."""
        return self.requested_enforcement


@dataclass(frozen=True, slots=True)
class RealizerRequest:
    """Boundary contract for the existing generator; Persona only realizes style."""

    intent: Intent
    grounding: GroundingResult
    situation: SituationFull
    allowed_claims: tuple[str, ...]
    blocked_claims: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.intent.action is None:
            raise CognitiveContractError("RealizerRequest requires an actionable intent")
        object.__setattr__(self, "allowed_claims", canonical_tuple(self.allowed_claims))
        object.__setattr__(self, "blocked_claims", canonical_tuple(self.blocked_claims))


@dataclass(frozen=True, slots=True)
class BehaviorTrace:
    """Immutable CognitiveProposal; it never records Host facts or delivery."""

    event_id: str
    trigger: TriggerDecision
    participation: ParticipationResult | None
    intent: Intent | None
    grounding: GroundingResult | None
    exit_reason: ExitReason | None
    runtime_mode: RuntimeMode = RuntimeMode.SHADOW
    created_at: datetime = field(default_factory=_utcnow)
    identity: Mapping[str, Any] = field(default_factory=dict)
    situation_lite: SituationLite | None = None
    proposed_output_state: OutputState = OutputState.NO_OUTPUT
    trace_id: str = field(default_factory=lambda: f"trace:{uuid4().hex}", init=False)

    def __post_init__(self) -> None:
        if not self.trace_id or not self.event_id:
            raise CognitiveContractError("trace_id and event_id are required")
        object.__setattr__(self, "identity", _freeze_mapping(self.identity))


@dataclass(frozen=True, slots=True)
class HostResult:
    """What the host pipeline actually exposed; it never assumes QQ delivery."""

    legacy_fallthrough: bool
    output_generated: bool | None
    output_nonempty: bool | None
    dispatch_observed: bool
    output_state: OutputState = OutputState.NO_OUTPUT
    producer: OutputProducer = OutputProducer.UNKNOWN_HOST
    applied_enforcement: GroundingEnforcement = GroundingEnforcement.NOT_APPLIED
    delivery_status: DeliveryStatus = DeliveryStatus.UNOBSERVED

    def __post_init__(self) -> None:
        if self.delivery_status is DeliveryStatus.DELIVERED and not self.dispatch_observed:
            raise CognitiveContractError("DELIVERED requires an observed dispatch")
        if self.output_state is OutputState.OUTPUT_READY and not self.output_nonempty:
            raise CognitiveContractError("OUTPUT_READY requires non-empty Host output")
        if self.output_state is OutputState.NO_OUTPUT and self.output_nonempty is True:
            raise CognitiveContractError("NO_OUTPUT cannot claim non-empty Host output")


@dataclass(frozen=True, slots=True)
class ShadowComparison:
    """Small deterministic comparison, not an outcome score or learning signal."""

    cognitive_would_participate: bool
    cognitive_would_reply: bool
    cognitive_exit_reason: ExitReason | None
    legacy_replied: bool | None
    legacy_output_present: bool | None
    divergence: DivergenceType

    def __post_init__(self) -> None:
        if (
            self.legacy_replied is not None
            and self.legacy_output_present is not None
            and self.legacy_replied != self.legacy_output_present
        ):
            raise CognitiveContractError("ShadowComparison legacy fields are inconsistent")


@dataclass(frozen=True, slots=True)
class BehaviorExecutionRecord:
    """Cognitive proposal plus separately observed host behavior."""

    trace: BehaviorTrace
    host_result: HostResult
    comparison: ShadowComparison
    stage: TraceStage
    revision: int
    updated_at: datetime = field(default_factory=_utcnow)


@dataclass(frozen=True, slots=True)
class BehaviorLoopResult:
    trace: BehaviorTrace
    realizer_request: RealizerRequest | None = None
