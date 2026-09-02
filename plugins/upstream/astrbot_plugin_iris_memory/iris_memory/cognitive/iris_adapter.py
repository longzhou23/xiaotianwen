"""Non-destructive pre/post adapter between Cognitive Runtime and Iris.

The pre side creates structured metadata for new writes.  The post side
creates read-time memory views.  Neither side edits existing L1, L2, or L3
records, and neither side performs automatic historical repair.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import logging
from typing import TYPE_CHECKING, Any, Iterable

from .contracts import (
    CanonicalExperience,
    BehaviorExecutionRecord,
    BehaviorLoopResult,
    BehaviorTrace,
    DeliveryStatus,
    DivergenceType,
    EntityReference,
    ExitReason,
    HostResult,
    GroundingEnforcement,
    IrisPreprocessResult,
    LegacyProactiveSignals,
    OutputState,
    OutputProducer,
    Perspective,
    ResolvedEvent,
    RuntimeMode,
    RuntimeMemoryView,
    ShadowComparison,
    TraceStage,
)
from .identity import EntityRegistry, IdentityResolver
from .perspective import PerspectiveResolver
from .behavior import CognitiveBehaviorRuntime
from .execution_observatory import ExecutionRecordObservatory

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent
    from iris_memory.l2_memory.models import MemorySearchResult


_EVENT_EXTRA_KEY = "iris_cognitive_preprocess"
logger = logging.getLogger(__name__)


def _utc_from_raw(raw: dict[str, Any]) -> datetime:
    timestamp = raw.get("time") or raw.get("timestamp")
    if isinstance(timestamp, (int, float)):
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return datetime.now(timezone.utc)


def _platform_name(event: "AstrMessageEvent") -> str:
    candidate = getattr(event, "get_platform_name", None)
    if callable(candidate):
        value = candidate()
        if isinstance(value, str) and value.strip():
            return value.strip().casefold()
    platform_meta = getattr(event, "platform_meta", None)
    value = getattr(platform_meta, "name", None)
    return value.strip().casefold() if isinstance(value, str) and value.strip() else "unknown"


def _event_self_id(event: "AstrMessageEvent") -> str:
    getter = getattr(event, "get_self_id", None)
    if not callable(getter):
        return ""
    value = getter()
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _canonical_message_id(platform: str, message_id: Any) -> str:
    """Normalize a platform message id into a stable canonical event id.

    Accepts int/str and tolerates an already-normalized ``platform:id`` value.
    """
    value = str(message_id or "").strip()
    if not value:
        return ""
    prefix = f"{platform}:"
    if value.startswith(prefix):
        return value
    return f"{prefix}{value}"


class IrisPreAdapter:
    """Adapter-owned Event Normalize → Identity → Perspective metadata path."""

    owner = "Iris Adapter"

    def __init__(self, identity: IdentityResolver, perspective: PerspectiveResolver) -> None:
        self.identity = identity
        self.perspective = perspective

    def preprocess_event(self, event: "AstrMessageEvent") -> IrisPreprocessResult:
        from iris_memory.platform import get_adapter

        adapter = get_adapter(event)
        raw = adapter.get_raw_message(event) or {}
        platform = _platform_name(event)
        user_id = str(adapter.get_user_id(event) or "")
        self_id = _event_self_id(event)
        actor = (
            self.identity.resolve_event_self(platform, user_id)
            if self_id and user_id == self_id
            else self.identity.resolve_actor(platform, user_id, adapter.get_user_name(event) or "")
        )

        reply_to = None
        reply_event_id = None
        reply = adapter.get_reply_info(event)
        if reply.has_reply and reply.user_id:
            reply_id = str(reply.user_id)
            reply_to = (
                self.identity.resolve_event_self(platform, reply_id)
                if self_id and reply_id == self_id
                else self.identity.resolve_actor(platform, reply_id, reply.user_name)
            )
        if reply.has_reply and reply.message_id:
            reply_event_id = _canonical_message_id(platform, reply.message_id)

        mentioned: list[EntityReference] = []
        self_mention_uid_matched = False
        for mentioned_id, mentioned_name in adapter.get_mentioned_users(event):
            mention_id = str(mentioned_id)
            self_mention_uid_matched = self_mention_uid_matched or bool(self_id and mention_id == self_id)
            entity = (
                self.identity.resolve_event_self(platform, mention_id)
                if self_id and mention_id == self_id
                else self.identity.resolve_actor(platform, mention_id, mentioned_name)
            )
            if entity is not None:
                mentioned.append(entity)

        session_id = str(adapter.get_session_id(event) or "")
        message_id = str(raw.get("message_id") or "")
        event_id = _canonical_message_id(platform, message_id) if message_id else f"runtime:{id(event)}"
        is_group = bool(adapter.is_group_message(event))
        resolved_event = ResolvedEvent(
            event_id=event_id,
            source=platform,
            occurred_at=_utc_from_raw(raw),
            session_id=session_id or "runtime:unknown-session",
            mode="casual_group_chat" if is_group else "private",
            content=str(getattr(event, "message_str", "") or ""),
            actor=actor,
            mentioned_entities=tuple(mentioned),
            reply_to=reply_to,
            raw_metadata={
                "message_id": message_id,
                "platform": platform,
                "reply_event_id": reply_event_id,
                "identity_diagnostic": {
                    "self_uid_observed": bool(self_id),
                    "self_mention_uid_matched": self_mention_uid_matched,
                    "self_reply_uid_matched": bool(
                        self_id and reply_to and reply_to.entity_id == self.identity.self_entity
                    ),
                },
            },
        )
        perspective = self.perspective.resolve(actor)
        experience = CanonicalExperience(
            id=f"experience:{event_id}",
            event=resolved_event,
            subject=actor,
            perspective=perspective,
            provenance=("event_normalizer", "identity_resolver", "perspective_resolver"),
        )
        metadata = {
            "owner": self.owner,
            "experience_id": experience.id,
            "event_id": resolved_event.event_id,
            "subject_entity": actor.entity_id if actor else None,
            "perspective": perspective.value,
            "resolved_entities": [entity.entity_id for entity in mentioned],
            "identity_diagnostic": {
                "self_uid_observed": bool(self_id),
                "self_mention_uid_matched": self_mention_uid_matched,
                "self_reply_uid_matched": bool(self_id and reply_to and reply_to.entity_id == self.identity.self_entity),
            },
            "provenance": list(experience.provenance),
        }
        return IrisPreprocessResult(experience=experience, metadata=metadata)

    def attach(self, event: "AstrMessageEvent") -> IrisPreprocessResult:
        existing = self.attached(event)
        if existing is not None:
            return existing
        result = self.preprocess_event(event)
        event.set_extra(_EVENT_EXTRA_KEY, result)
        return result

    @staticmethod
    def attached(event: "AstrMessageEvent") -> IrisPreprocessResult | None:
        candidate = event.get_extra(_EVENT_EXTRA_KEY)
        return candidate if isinstance(candidate, IrisPreprocessResult) else None


class IrisPostAdapter:
    """Adapter-owned post-retrieval canonicalization and perspective projection."""

    owner = "Iris Adapter"

    def __init__(self, perspective: PerspectiveResolver) -> None:
        self.perspective = perspective

    def project_memory(
        self,
        *,
        memory_id: str,
        content: str,
        metadata: dict[str, Any] | None,
    ) -> RuntimeMemoryView:
        metadata = metadata or {}
        cognitive = metadata.get("cognitive_runtime")
        if not isinstance(cognitive, dict):
            cognitive = {}
        subject_id = cognitive.get("subject_entity")
        subject = None
        if isinstance(subject_id, str) and subject_id:
            try:
                subject = EntityReference(
                    subject_id,
                    "iris_pre_metadata",
                    1.0,
                    (f"memory_id:{memory_id}",),
                )
            except ValueError:
                subject = None
        perspective = self.perspective.resolve(subject)
        return RuntimeMemoryView(
            memory_id=memory_id,
            raw_content=content,
            content=self.perspective.project(content, perspective),
            subject=subject,
            perspective=perspective,
            provenance=("raw_iris_memory", "iris_post_adapter"),
        )

    def format_l2_context(self, results: Iterable["MemorySearchResult"]) -> str:
        views = [
            self.project_memory(
                memory_id=result.entry.id,
                content=result.entry.content,
                metadata=result.entry.metadata,
            )
            for result in results
        ]
        if not views:
            return ""
        lines = ["## 相关记忆"]
        for index, view in enumerate(views, 1):
            prefix = "[你的经历] " if view.perspective is Perspective.AUTOBIOGRAPHICAL else ""
            lines.append(f"{index}. {prefix}{view.content}")
        return "\n".join(lines)


class CognitiveRuntime:
    """Composition root; identity remains the registry writer and Iris owns raw data."""

    owner = "Cognitive Runtime"

    _GUARD_EXITS = {
        ExitReason.NO_INTENT,
        ExitReason.GROUNDING_FAILED,
        ExitReason.SILENCE_SELECTED,
    }
    _MAX_EXPERIENCES = 4096

    def __init__(
        self,
        *,
        runtime_mode: RuntimeMode = RuntimeMode.SHADOW,
        record_traces: bool = True,
        episode_observer: Any | None = None,
        execution_observatory: ExecutionRecordObservatory | None = None,
    ) -> None:
        self.runtime_mode = runtime_mode
        self._record_traces = record_traces
        self.registry = EntityRegistry()
        self.identity = IdentityResolver(self.registry)
        self.perspective = PerspectiveResolver(self.registry.config)
        self.pre_adapter = IrisPreAdapter(self.identity, self.perspective)
        self.post_adapter = IrisPostAdapter(self.perspective)
        self.behavior = CognitiveBehaviorRuntime(self_entity=self.identity.self_entity)
        self._experiences: dict[str, CanonicalExperience] = {}
        self.episode_observer = episode_observer
        self.execution_observatory = execution_observatory or ExecutionRecordObservatory()

    def run_behavior(
        self,
        experience: CanonicalExperience,
        legacy_signals: LegacyProactiveSignals | None = None,
        *,
        runtime_mode: RuntimeMode | None = None,
    ) -> BehaviorLoopResult:
        # Snapshot at method entry: no later code may read live global mode.
        trace_mode = runtime_mode if runtime_mode is not None else self.runtime_mode
        lite = self.behavior.observe(experience)
        self._experiences[experience.event.event_id] = experience
        self._trim_experiences()
        result = self.behavior.run(experience, legacy_signals)
        resolved = []
        if experience.event.actor:
            resolved.append(experience.event.actor.entity_id)
        resolved.extend(entity.entity_id for entity in experience.event.mentioned_entities)
        if experience.event.reply_to:
            resolved.append(experience.event.reply_to.entity_id)
        trace = replace(
            result.trace,
            runtime_mode=trace_mode,
            identity={
                "self_entity": self.identity.self_entity,
                "resolved_entities": tuple(dict.fromkeys(resolved)),
                "diagnostic": experience.event.raw_metadata.get("identity_diagnostic", {}),
            },
            situation_lite=lite,
        )
        result = replace(result, trace=trace)
        # This records a proposal, not a claim about what the host will do.
        self._record_trace(trace, None, None, TraceStage.PROPOSAL, 0)
        if self.episode_observer is not None:
            self.episode_observer.observe_behavior_trace(experience, trace)
        return result

    def _trim_experiences(self) -> None:
        """Bound the runtime correlation cache; this is not Episode storage."""
        while len(self._experiences) > self._MAX_EXPERIENCES:
            self._experiences.pop(next(iter(self._experiences)))

    def should_guard_block(self, result: BehaviorLoopResult) -> bool:
        """Guard is deliberately narrower than Authoritative and disabled by default."""
        return (
            result.trace.runtime_mode is RuntimeMode.GUARD
            and result.trace.exit_reason in self._GUARD_EXITS
        )

    def observe_host_output(
        self,
        result: BehaviorLoopResult,
        response_text: str,
        *,
        legacy_fallthrough: bool,
        producer: OutputProducer = OutputProducer.LEGACY_HOST,
        applied_enforcement: GroundingEnforcement = GroundingEnforcement.NOT_APPLIED,
    ) -> BehaviorExecutionRecord:
        """Observe Host output only; non-empty text is not a dispatch/delivery ACK."""
        output_nonempty = bool(response_text.strip())
        trace = result.trace
        host = HostResult(
            legacy_fallthrough=legacy_fallthrough,
            output_generated=True,
            output_nonempty=output_nonempty,
            dispatch_observed=False,
            output_state=OutputState.OUTPUT_READY if output_nonempty else OutputState.NO_OUTPUT,
            producer=producer,
            applied_enforcement=applied_enforcement,
            delivery_status=DeliveryStatus.UNOBSERVED,
        )
        record = self._execution_record(trace, host, TraceStage.HOST_OUTPUT, 1)
        if output_nonempty:
            experience = self._experiences.get(trace.event_id)
            if experience is not None:
                action = "HOST_OUTPUT"
                if producer is OutputProducer.COGNITIVE_REALIZER and result.realizer_request is not None:
                    action = result.realizer_request.intent.action.value
                # The Host action time is the observation time, never the user input time.
                self.behavior.situation.record_self_action(
                    experience, action, occurred_at=record.updated_at
                )
        self._record_trace(trace, host, record.comparison, record.stage, record.revision)
        self._record_execution(record)
        if self.episode_observer is not None:
            self.episode_observer.observe_host_output(record)
        return record

    def observe_dispatch(self, record: BehaviorExecutionRecord) -> BehaviorExecutionRecord:
        """`after_message_sent` observes Host dispatch, never platform delivery."""
        if record.host_result.dispatch_observed:
            # Duplicate after-message-sent hooks must not create extra revisions.
            return record
        host = replace(
            record.host_result,
            dispatch_observed=True,
            # AstrBot's hook is a Host dispatch observation, not an ACK from QQ.
            delivery_status=DeliveryStatus.UNOBSERVED,
        )
        updated = self._execution_record(record.trace, host, TraceStage.DISPATCH, record.revision + 1)
        self._record_trace(updated.trace, updated.host_result, updated.comparison, updated.stage, updated.revision)
        self._record_execution(updated)
        if self.episode_observer is not None:
            self.episode_observer.observe_dispatch(updated)
        return updated

    def record_guard_block(self, result: BehaviorLoopResult) -> BehaviorExecutionRecord:
        host = HostResult(
            legacy_fallthrough=False,
            output_generated=False,
            output_nonempty=False,
            dispatch_observed=False,
            delivery_status=DeliveryStatus.UNOBSERVED,
        )
        record = self._execution_record(result.trace, host, TraceStage.HOST_OUTPUT, 1)
        self._record_trace(result.trace, host, record.comparison, record.stage, record.revision)
        self._record_execution(record)
        return record

    def observe_host_silence(
        self, result: BehaviorLoopResult, *, legacy_fallthrough: bool
    ) -> BehaviorExecutionRecord:
        """Record an observed Host stop without claiming a platform action happened."""
        host = HostResult(
            legacy_fallthrough=legacy_fallthrough,
            output_generated=False,
            output_nonempty=False,
            dispatch_observed=False,
            delivery_status=DeliveryStatus.UNOBSERVED,
        )
        record = self._execution_record(result.trace, host, TraceStage.HOST_OUTPUT, 1)
        self._record_trace(result.trace, host, record.comparison, record.stage, record.revision)
        self._record_execution(record)
        return record

    def _record_execution(self, record: BehaviorExecutionRecord) -> None:
        """Best-effort diagnostic registration after a record is complete."""
        try:
            self.execution_observatory.record(record)
        except Exception as exc:  # pragma: no cover - defensive observability boundary
            logger.warning("Cognitive execution observatory write failed: %s", exc)

    @staticmethod
    def _compare(trace: BehaviorTrace, host: HostResult) -> ShadowComparison:
        cognitive_would_reply = trace.proposed_output_state is OutputState.OUTPUT_PROPOSED
        cognitive_would_participate = bool(
            trace.participation and trace.participation.decision.value == "PARTICIPATE"
        )
        legacy_replied = host.output_nonempty
        if legacy_replied is None:
            divergence = DivergenceType.UNRESOLVED
        elif trace.exit_reason is ExitReason.GROUNDING_FAILED and legacy_replied:
            divergence = DivergenceType.GROUNDING_DISAGREEMENT
        elif cognitive_would_reply and legacy_replied:
            divergence = DivergenceType.MATCH_REPLY
        elif not cognitive_would_reply and not legacy_replied:
            divergence = DivergenceType.MATCH_SILENCE
        elif not cognitive_would_reply and legacy_replied:
            divergence = DivergenceType.LEGACY_REPLY_COGNITIVE_SILENCE
        else:
            divergence = DivergenceType.LEGACY_SILENCE_COGNITIVE_REPLY
        return ShadowComparison(
            cognitive_would_participate=cognitive_would_participate,
            cognitive_would_reply=cognitive_would_reply,
            cognitive_exit_reason=trace.exit_reason,
            legacy_replied=legacy_replied,
            legacy_output_present=host.output_nonempty,
            divergence=divergence,
        )

    @staticmethod
    def _execution_record(
        trace: BehaviorTrace,
        host: HostResult,
        stage: TraceStage,
        revision: int,
    ) -> BehaviorExecutionRecord:
        return BehaviorExecutionRecord(trace, host, CognitiveRuntime._compare(trace, host), stage, revision)

    def _record_trace(
        self,
        trace: BehaviorTrace,
        host: HostResult | None,
        comparison: ShadowComparison | None,
        stage: TraceStage,
        revision: int,
    ) -> None:
        """Diagnostic sink only; it is never authoritative Episode/Review storage."""
        if not self._record_traces:
            return
        try:
            from iris_memory.core import get_run_log_manager

            get_run_log_manager().record(
                "proactive",
                f"Cognitive {trace.runtime_mode.value} {trace.exit_reason.value if trace.exit_reason else 'ACTION_PROPOSED'}",
                success=host.output_nonempty if host is not None else False,
                trace_id=trace.trace_id,
                stage=stage.value,
                revision=revision,
                created_at=trace.created_at.isoformat(),
                event_id=trace.event_id,
                runtime_mode=trace.runtime_mode.value,
                identity=dict(trace.identity),
                situation_lite={
                    "scope_id": trace.situation_lite.scope_id,
                    "message_velocity": trace.situation_lite.message_velocity,
                    "self_recently_spoke": trace.situation_lite.self_recently_spoke,
                } if trace.situation_lite else None,
                trigger=trace.trigger.reason,
                participation=trace.participation.decision.value if trace.participation else None,
                intent=trace.intent.action.value if trace.intent and trace.intent.action else None,
                grounding={
                    "status": trace.grounding.status.value,
                    "requested_enforcement": trace.grounding.requested_enforcement.value,
                } if trace.grounding else None,
                exit_reason=trace.exit_reason.value if trace.exit_reason else None,
                proposed_output_state=trace.proposed_output_state.value,
                host={
                    "legacy_fallthrough": host.legacy_fallthrough,
                    "output_generated": host.output_generated,
                    "output_nonempty": host.output_nonempty,
                    "output_state": host.output_state.value,
                    "dispatch_observed": host.dispatch_observed,
                    "producer": host.producer.value,
                    "applied_enforcement": host.applied_enforcement.value,
                    "delivery_status": host.delivery_status.value,
                } if host else None,
                comparison=comparison.divergence.value if comparison else None,
            )
        except Exception as exc:
            logger.warning("Cognitive trace diagnostic write failed for %s/%s: %s", trace.trace_id, stage.value, exc)


_runtime: CognitiveRuntime | None = None


def get_cognitive_runtime() -> CognitiveRuntime:
    global _runtime
    if _runtime is None:
        _runtime = CognitiveRuntime()
    return _runtime


def reset_cognitive_runtime() -> None:
    """Test-only reset; production state is created once per plugin process."""
    global _runtime
    _runtime = None
