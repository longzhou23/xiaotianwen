"""P2r.0.3b factual capture integration.

This module observes immutable AstrBot send receipts and inbound message
chains.  P2r.0 remains factual capture; an optional P2r.1a service may consume
the same attached inbound event only after the P2r.0 fact is committed.  No
archive assembly, reply-link resolution, or ReviewEvidence production belongs
here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import BehaviorExecutionRecord, TraceStage
from .episode import EpisodeEventKind, EpisodeEventRef
from .iris_adapter import CognitiveRuntime
from .reply_link_authority import (
    MESSAGE_OPERATION,
    SEND_SUCCEEDED_WITH_IDENTITY,
    HostOutputMessageIdentityFactV1,
    InboundReplyReferenceFactV1,
    P2r0IntegrityError,
    P2r0Store,
    PlatformMessageIdentityV1,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CaptureResult:
    """Counts returned by one non-controlling capture observation."""

    host_facts: int = 0
    inbound_facts: int = 0
    ambiguous_reply: bool = False


def _scalar(value: object) -> str | None:
    if type(value) is int:
        return str(value)
    if type(value) is str:
        value = value.strip()
        return value or None
    return None


def _event_value(event: object, name: str) -> object:
    getter = getattr(event, name, None)
    if not callable(getter):
        return None
    try:
        return getter()
    except Exception:  # noqa: BLE001 - fail closed when host API is absent
        return None


def _enum_value(value: object) -> object:
    return getattr(value, "value", value)


def _host_receipt_types() -> tuple[type[Any], ...]:
    """Load H0 classes lazily so the plugin remains importable in old hosts."""
    try:
        from astrbot.core.platform.send_receipt import HostSendResultV1

        return (HostSendResultV1,)
    except Exception:  # noqa: BLE001 - fail closed when host API is absent
        return ()


def _reply_type() -> type[Any] | None:
    try:
        from astrbot.core.message.components import Reply

        return Reply
    except Exception:  # noqa: BLE001 - fail closed when host API is absent
        return None


class P2r0CaptureService:
    """Runtime-owned, fail-closed factual capture service."""

    owner = "P2r0 Factual Capture"

    def __init__(
        self,
        store: P2r0Store,
        runtime: CognitiveRuntime,
        semantic_authority_service: object | None = None,
    ) -> None:
        if type(store) is not P2r0Store:
            raise TypeError("capture service requires the authoritative P2r0Store")
        if not isinstance(runtime, CognitiveRuntime):
            raise TypeError("capture service requires the cognitive runtime")
        self._store = store
        self._runtime = runtime
        self._semantic_authority_service = semantic_authority_service
        observer = runtime.episode_observer
        bind_reply_resolver = getattr(
            observer, "bind_native_host_reply_resolver", None
        )
        if callable(bind_reply_resolver):
            bind_reply_resolver(self.resolve_native_host_reply_event_ref)

    @property
    def store(self) -> P2r0Store:
        return self._store

    @property
    def semantic_authority_service(self) -> object | None:
        return self._semantic_authority_service

    def bind_semantic_authority_service(self, service: object | None) -> None:
        """Bind the runtime-owned semantic worker after provider startup.

        AstrBot may finish loading providers after plugin initialization.  The
        plugin can therefore attach its already validated worker before the
        next inbound capture without replacing the factual P2r0 store.
        """
        self._semantic_authority_service = service

    def resolve_native_host_reply_event_ref(self, source_event_id: str) -> str | None:
        """Resolve one committed inbound Reply to its exact Host EventRef.

        This is a derived read over the authoritative P2r0 facts.  It creates
        no second ledger and deliberately requires the full frozen
        PlatformMessageIdentityV1 equality carried by those facts.
        """
        if type(source_event_id) is not str or not source_event_id:
            return None
        inbound = tuple(
            fact
            for fact in self._store.inbound_reply_facts
            if fact.source_event_id == source_event_id
        )
        if len(inbound) != 1:
            return None
        target_identity = inbound[0].reply_target_platform_message_identity
        hosts = tuple(
            fact
            for fact in self._store.host_output_facts
            if fact.platform_message_identity == target_identity
        )
        if len(hosts) != 1:
            return None
        return hosts[0].host_output_event_ref_id

    def capture_host_send_result(self, event: object, result: object) -> CaptureResult:
        """Persist eligible MESSAGE identities from one finalized H0 result.

        The observer never raises into AstrBot's send lifecycle.  A malformed
        receipt, missing required lifecycle lineage, or missing Episode ref
        simply produces no authoritative P2r0 Host fact.
        """
        if not _host_receipt_types() or not isinstance(result, _host_receipt_types()):
            return CaptureResult()
        try:
            trace, host_ref, dispatch = self._host_lineage(event)
            facts: list[HostOutputMessageIdentityFactV1] = []
            operations = getattr(result, "operations", ())
            if type(operations) is not tuple:
                return CaptureResult()
            for operation in operations:
                if (
                    _enum_value(getattr(operation, "operation_kind", None)) != MESSAGE_OPERATION
                    or _enum_value(getattr(operation, "status", None)) != SEND_SUCCEEDED_WITH_IDENTITY
                ):
                    continue
                platform_id = _scalar(getattr(operation, "platform_id", None))
                account_id = _scalar(getattr(operation, "account_id", None))
                conversation_id = _scalar(getattr(operation, "conversation_id", None))
                message_id = _scalar(getattr(operation, "platform_message_id", None))
                if not all((platform_id, account_id, conversation_id, message_id)):
                    continue
                identity = PlatformMessageIdentityV1(
                    platform_id, account_id, conversation_id, message_id
                )
                facts.append(
                    HostOutputMessageIdentityFactV1.create(
                        platform_message_identity=identity,
                        operation_index=getattr(operation, "operation_index", -1),
                        host_send_result_schema_version=getattr(result, "schema_version", ""),
                        platform_send_receipt_schema_version=getattr(
                            operation, "schema_version", ""
                        ),
                        source_event_id=trace.trace.event_id,
                        trace_id=trace.trace.trace_id,
                        host_output_event_ref_id=host_ref.ref_id,
                        dispatch_execution_record_id=(
                            f"{dispatch.trace.trace_id}:{dispatch.revision}" if dispatch else None
                        ),
                    )
                )
            for fact in facts:
                self._store.record_host_output_fact(fact)
            return CaptureResult(host_facts=len(facts))
        except Exception as exc:  # noqa: BLE001 - observer must never control send
            logger.warning("P2r0 Host factual capture rejected: %s", exc)
            return CaptureResult()

    def capture_inbound(self, event: object) -> CaptureResult:
        """Capture one inbound reply target from the complete original chain."""
        try:
            source = self._source_identity(event)
            attached = self._attached_preprocess(event)
            source_event_id = self._event_id_from_attached(attached)
            reply_cls = _reply_type()
            if reply_cls is None:
                return CaptureResult()
            messages = _event_value(event, "get_messages")
            if type(messages) is not list:
                return CaptureResult()
            target_ids: list[str] = []
            for component in messages:
                if not isinstance(component, reply_cls):
                    continue
                target = _scalar(getattr(component, "id", None))
                if target is None:
                    return CaptureResult()
                if target not in target_ids:
                    target_ids.append(target)
            if len(target_ids) == 0:
                return CaptureResult()
            if len(target_ids) > 1:
                return CaptureResult(ambiguous_reply=True)
            target_identity = PlatformMessageIdentityV1(
                source.platform_id,
                source.account_id,
                source.conversation_id,
                target_ids[0],
            )
            fact = InboundReplyReferenceFactV1.create(
                source_event_id=source_event_id,
                source_platform_message_identity=source,
                reply_target_platform_message_identity=target_identity,
            )
            self._store.record_inbound_reply_fact(fact)
            semantic_service = self._semantic_authority_service
            if semantic_service is not None:
                try:
                    resolved_event = getattr(getattr(attached, "experience", None), "event", None)
                    schedule = getattr(semantic_service, "schedule_after_inbound_commit", None)
                    if callable(schedule):
                        # Production E1 is asynchronous.  The bounded worker
                        # owns provider execution and persistence; this hook
                        # only submits the already-committed immutable input.
                        schedule(
                            resolved_event=resolved_event,
                            inbound_fact=fact,
                            source_platform_message_identity=source,
                        )
                    else:
                        evaluate = getattr(semantic_service, "evaluate_after_inbound_commit", None)
                        if callable(evaluate):
                            evaluate(
                                resolved_event=resolved_event,
                                inbound_fact=fact,
                                source_platform_message_identity=source,
                            )
                except Exception as exc:  # noqa: BLE001 - semantic capture never controls host
                    logger.warning("P2r1a semantic authority capture rejected: %s", exc)
            return CaptureResult(inbound_facts=1)
        except Exception as exc:  # noqa: BLE001 - malformed input is fail-closed
            logger.warning("P2r0 inbound factual capture rejected: %s", exc)
            return CaptureResult()

    capture_host_receipt = capture_host_send_result
    capture_inbound_reply = capture_inbound

    def _source_identity(self, event: object) -> PlatformMessageIdentityV1:
        platform_id = _scalar(_event_value(event, "get_platform_id"))
        account_id = _scalar(_event_value(event, "get_self_id"))
        group_id = _scalar(_event_value(event, "get_group_id"))
        conversation_id = group_id or _scalar(_event_value(event, "get_sender_id"))
        message_obj = getattr(event, "message_obj", None)
        message_id = _scalar(getattr(message_obj, "message_id", None))
        if not all((platform_id, account_id, conversation_id, message_id)):
            raise P2r0IntegrityError("inbound platform identity is incomplete")
        return PlatformMessageIdentityV1(platform_id, account_id, conversation_id, message_id)

    def _source_event_id(self, event: object) -> str:
        return self._event_id_from_attached(self._attached_preprocess(event))

    def _attached_preprocess(self, event: object) -> object:
        attached = self._runtime.pre_adapter.attached(event)  # type: ignore[arg-type]
        if attached is None:
            attached = self._runtime.pre_adapter.attach(event)  # type: ignore[arg-type]
        return attached

    @staticmethod
    def _event_id_from_attached(attached: object) -> str:
        event_id = getattr(getattr(attached, "experience", None), "event", None)
        event_id = getattr(event_id, "event_id", None)
        if type(event_id) is not str or not event_id:
            raise P2r0IntegrityError("inbound event has no P1 cross-reference")
        return event_id

    def _host_lineage(
        self, event: object
    ) -> tuple[BehaviorExecutionRecord, EpisodeEventRef, BehaviorExecutionRecord | None]:
        current = getattr(event, "get_extra", lambda _key: None)(
            "iris_cognitive_execution_record"
        )
        if not isinstance(current, BehaviorExecutionRecord):
            raise P2r0IntegrityError("receipt has no current cognitive execution record")
        trace_id = current.trace.trace_id
        source_event_id = current.trace.event_id
        if type(trace_id) is not str or not trace_id:
            raise P2r0IntegrityError("receipt trace identity is incomplete")
        if type(source_event_id) is not str or not source_event_id:
            raise P2r0IntegrityError("receipt source event identity is incomplete")
        if type(current.revision) is not int or current.revision < 1:
            raise P2r0IntegrityError("receipt execution revision is invalid")
        records = [
            item
            for item in self._runtime.execution_observatory.recent()
            if isinstance(item, BehaviorExecutionRecord)
            and item.trace.trace_id == trace_id
            and item.trace.event_id == source_event_id
        ]
        rev1 = [
            item
            for item in records
            if item.stage is TraceStage.HOST_OUTPUT
            and item.revision == 1
        ]
        if len(rev1) != 1:
            raise P2r0IntegrityError("exact rev1 Host output record is unavailable")
        # The event extra must be the registry's immutable record, not a
        # caller-constructed record that merely copies its identifiers.
        if not any(item is current for item in records):
            raise P2r0IntegrityError("receipt record is not an authoritative observation")
        if current.stage is TraceStage.DISPATCH and current.revision == 2:
            dispatch = current
        elif current.stage is TraceStage.HOST_OUTPUT and current.revision == 1:
            # AstrBot's streaming lifecycle emits the receipt hook directly
            # after HOST_OUTPUT and intentionally has no DISPATCH revision.
            if rev1[0] is not current:
                raise P2r0IntegrityError("receipt rev1 record is not authoritative")
            dispatch = None
        else:
            raise P2r0IntegrityError("receipt current record has unsupported stage/revision")
        observer = self._runtime.episode_observer
        store = getattr(observer, "store", None)
        if store is None:
            raise P2r0IntegrityError("Episode store is unavailable")
        episode = store.find_episode_by_trace_id(trace_id)
        if episode is None:
            raise P2r0IntegrityError("Host trace has no Episode")
        matches = [
            ref
            for ref in episode.event_refs
            if ref.kind is EpisodeEventKind.HOST_OUTPUT
            and ref.trace_id == trace_id
            and ref.source_event_id == source_event_id
            and ref.execution_record_id == f"{trace_id}:1"
        ]
        if len(matches) != 1:
            raise P2r0IntegrityError("exact Episode HOST_OUTPUT ref is unavailable")
        return rev1[0], matches[0], dispatch


def create_runtime_capture_service(
    data_dir: str | Path,
    runtime: CognitiveRuntime,
    semantic_authority_service: object | None = None,
) -> P2r0CaptureService:
    """Create runtime capture with the production semantic service composition.

    The production factory intentionally accepts no evaluator/service
    injection.  Tests and future contract work can compose the service by
    constructing :class:`P2r0CaptureService` directly.
    """
    root = Path(data_dir) / "cognitive" / "p2r0-reply-link-facts"
    root.mkdir(parents=True, exist_ok=True)
    store = P2r0Store(root / "facts.jsonl")
    semantic_service = semantic_authority_service
    if semantic_service is None:
        try:
            from .inbound_semantic_authority import (
                create_runtime_semantic_authority_service,
            )

            semantic_service = create_runtime_semantic_authority_service(data_dir)
        except Exception:
            logger.exception("P2r1a semantic authority store unavailable; P2r0 remains enabled")
    return P2r0CaptureService(store, runtime, semantic_service)


ReplyLinkCaptureService = P2r0CaptureService


__all__ = [
    "CaptureResult",
    "P2r0CaptureService",
    "ReplyLinkCaptureService",
    "create_runtime_capture_service",
]
