"""P2r0 exact reply-link authority core.

This module is deliberately a small, self-contained factual authority layer.
It does not observe AstrBot, capture Iris input, evaluate ReviewFindings, or
create ReviewEvidence.  Its only derived semantic result is an in-memory
``ExactHostReplyLinkV1`` produced from a replay-valid archive.

The wire format is closed and explicit.  Unknown objects, fields, schemas and
operations are rejected rather than being represented with ``repr`` or a
generic dataclass serializer.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType

from .promotion_infrastructure import CanonicalHashV1
from .review import AttributionTargetType


class P2r0IntegrityError(ValueError):
    """The P2r0 factual history or one of its immutable artifacts is invalid."""


class P2r0CommitOutcomeIndeterminateError(P2r0IntegrityError):
    """A COMMIT write failed after it may have reached the JSONL file."""


P2R0_CONTRACT_FROZEN = True
P2R0_STORE_SCHEMA = "p2r0.reply-link-fact-store.v1"
P2R0_TRANSACTION_SCHEMA = "p2r0.persistence-transaction.v1"
P2R0_PERSISTENCE_ROOT = "p2r0-reply-link-facts"
P2R0_TRANSACTION_ROOT = P2R0_PERSISTENCE_ROOT

P2R0_TX_PREPARE = "P2R0_TX_PREPARE"
P2R0_TX_COMMIT = "P2R0_TX_COMMIT"
P2R0_HOST_OUTPUT_FACT_CAPTURE = "P2R0_HOST_OUTPUT_FACT_CAPTURE"
P2R0_INBOUND_REPLY_FACT_CAPTURE = "P2R0_INBOUND_REPLY_FACT_CAPTURE"
P2R0_REPLY_LINK_FACT_ARCHIVE = "P2R0_REPLY_LINK_FACT_ARCHIVE"

P2R0_DOMAIN_TRANSACTION_PAYLOAD = "p2r0:persistence-transaction-payload:v1"
P2R0_DOMAIN_TRANSACTION_IDENTITY = "p2r0:persistence-transaction-identity:v1"
P2R0_DOMAIN_TRANSACTION_PREPARE = "p2r0:persistence-transaction-prepare:v1"
P2R0_DOMAIN_TRANSACTION_COMMIT = "p2r0:persistence-transaction-commit:v1"

P2R0_PROFILE_ID = "p2r0.canonical-artifacts/v1"
P2R0_PROFILE_SCHEMA = "p2r0.canonical-artifacts/v1"
P2R0_PROFILE_HASH_DOMAIN = "p2r0:canonical-artifact-encoding-profile:v1"

PLATFORM_MESSAGE_IDENTITY_SCHEMA = "p2r0.platform-message-identity.v1"
HOST_OUTPUT_FACT_SCHEMA = "p2r0.host-output-message-fact.v1"
INBOUND_REPLY_FACT_SCHEMA = "p2r0.inbound-reply-reference-fact.v1"
FACT_CAPTURE_BINDING_SCHEMA = "p2r0.fact-capture-authority-binding.v1"
ARCHIVE_SCHEMA = "p2r0.reply-link-fact-archive.v1"
EXACT_REPLY_LINK_SCHEMA = "p2r0.exact-host-reply-link.v1"

HOST_OUTPUT_FACT_DOMAIN = "p2r0:host-output-message-fact:v1"
INBOUND_REPLY_FACT_DOMAIN = "p2r0:inbound-reply-reference-fact:v1"
FACT_CAPTURE_BINDING_DOMAIN = "p2r0:fact-capture-authority-binding:v1"
ARCHIVE_PAYLOAD_DOMAIN = "p2r0:reply-link-fact-archive-payload:v1"
ARCHIVE_IDENTITY_DOMAIN = "p2r0:reply-link-fact-archive-identity:v1"
EXACT_REPLY_LINK_DOMAIN = "p2r0:exact-host-reply-link:v1"

HOST_FACT_PREFIX = "hostfact:p2r0:"
INBOUND_FACT_PREFIX = "replyfact:p2r0:"
ARCHIVE_PREFIX = "replyarchive:p2r0:"
REPLY_LINK_PREFIX = "replylink:p2r0:"
TX_PREFIX = "tx:p2r0:"

SEND_SUCCEEDED_WITH_IDENTITY = "SEND_SUCCEEDED_WITH_IDENTITY"
MESSAGE_OPERATION = "MESSAGE"


class P2r0HostOperationKind(str, Enum):
    MESSAGE = MESSAGE_OPERATION


class P2r0HostFactStatus(str, Enum):
    SEND_SUCCEEDED_WITH_IDENTITY = SEND_SUCCEEDED_WITH_IDENTITY


class ExactHostReplyLinkStatus(str, Enum):
    EXACT_REPLY_LINK = "EXACT_REPLY_LINK"
    EXACT_REPLY_LINK_UNAVAILABLE = "EXACT_REPLY_LINK_UNAVAILABLE"
    EXACT_REPLY_LINK_UNAVAILABLE_NO_INBOUND_FACT = "EXACT_REPLY_LINK_UNAVAILABLE_NO_INBOUND_FACT"
    EXACT_REPLY_LINK_UNAVAILABLE_CONFLICT = "EXACT_REPLY_LINK_UNAVAILABLE_CONFLICT"


HostOperationKind = P2r0HostOperationKind
HostFactStatus = P2r0HostFactStatus

_GENERIC_PLATFORM_NAMES = frozenset(
    {
        "aiocqhttp",
        "aiocqhttpmessageevent",
        "aiocqhttp_message_event",
        "qq",
        "onebot",
    }
)


def _text(value: object, field: str) -> str:
    if isinstance(value, Enum):
        value = value.value
    if type(value) is not str or not value.strip():
        raise P2r0IntegrityError(f"{field} must be a non-empty string")
    return value.strip()


def _identity_text(value: object, field: str) -> str:
    """Normalize one platform identity component without widening the contract."""
    if value is None or type(value) is bool:
        raise P2r0IntegrityError(f"{field} must be a concrete string or integer")
    if type(value) is int:
        return str(value)
    if type(value) is str:
        normalized = value.strip()
        if not normalized:
            raise P2r0IntegrityError(f"{field} must be a concrete string or integer")
        return normalized
    raise P2r0IntegrityError(f"{field} must be a concrete string or integer")


def _platform_text(value: object) -> str:
    result = _identity_text(value, "platform_id")
    if result.casefold() in _GENERIC_PLATFORM_NAMES:
        raise P2r0IntegrityError("platform_id must identify a concrete platform instance")
    return result


def _nonnegative_int(value: object, field: str) -> int:
    if type(value) is not int or value < 0:
        raise P2r0IntegrityError(f"{field} must be a non-negative integer")
    return value


def _canonical_json_value(value: object) -> object:
    """Return JSON data while rejecting arbitrary runtime values."""
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise P2r0IntegrityError("NaN and Infinity are not canonical JSON")
        return value
    if type(value) in (tuple, list):
        return [_canonical_json_value(item) for item in value]
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key in value:
            if type(key) is not str:
                raise P2r0IntegrityError("canonical mapping keys must be strings")
            result[key] = _canonical_json_value(value[key])
        return {key: result[key] for key in sorted(result)}
    raise P2r0IntegrityError(f"unsupported canonical value: {type(value).__name__}")


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        _canonical_json_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _deep_freeze(value: object) -> object:
    """Detach a validated wire value before it enters an index."""
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise P2r0IntegrityError("non-finite immutable value")
        return value
    if type(value) in (tuple, list):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise P2r0IntegrityError("immutable mapping keys must be strings")
            frozen[key] = _deep_freeze(item)
        return MappingProxyType(frozen)
    raise P2r0IntegrityError(f"unsupported immutable value: {type(value).__name__}")


def _strict_loads(text: str) -> object:
    def reject_constant(value: str) -> object:
        raise P2r0IntegrityError(f"non-finite JSON constant: {value}")

    def reject_duplicate(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise P2r0IntegrityError("duplicate JSON key")
            result[key] = value
        return result

    try:
        return json.loads(text, parse_constant=reject_constant, object_pairs_hook=reject_duplicate)
    except json.JSONDecodeError as exc:
        raise P2r0IntegrityError("malformed JSON") from exc


def _id(prefix: str, domain: str, payload: Mapping[str, object]) -> str:
    return prefix + CanonicalHashV1.digest(domain, payload)


def _check_hash_id(value: object, prefix: str) -> str:
    value = _text(value, "identifier")
    digest = value[len(prefix) :] if value.startswith(prefix) else ""
    if len(digest) != 64 or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise P2r0IntegrityError(f"invalid identifier for {prefix}")
    return value


def _hash_text(value: object, field: str) -> str:
    value = _text(value, field)
    if re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise P2r0IntegrityError(f"{field} must be a canonical SHA-256 hash")
    return value


@dataclass(frozen=True, slots=True)
class PlatformMessageIdentityV1:
    platform_id: str
    account_id: str
    conversation_id: str
    message_id: str

    schema_version = PLATFORM_MESSAGE_IDENTITY_SCHEMA

    def __post_init__(self) -> None:
        object.__setattr__(self, "platform_id", _platform_text(self.platform_id))
        object.__setattr__(self, "account_id", _identity_text(self.account_id, "account_id"))
        object.__setattr__(self, "conversation_id", _identity_text(self.conversation_id, "conversation_id"))
        object.__setattr__(self, "message_id", _identity_text(self.message_id, "message_id"))

    def canonical_body(self) -> Mapping[str, str]:
        return MappingProxyType(
            {
                "platform_id": self.platform_id,
                "account_id": self.account_id,
                "conversation_id": self.conversation_id,
                "message_id": self.message_id,
            }
        )

    def fingerprint(self) -> str:
        return CanonicalHashV1.hash("p2r0:platform-message-identity:v1", self.canonical_body())

    canonical_payload = canonical_body


@dataclass(frozen=True, slots=True)
class HostOutputMessageIdentityFactV1:
    schema_version: str
    fact_id: str
    platform_message_identity: PlatformMessageIdentityV1
    operation_index: int
    operation_kind: str
    status: str
    host_send_result_schema_version: str
    platform_send_receipt_schema_version: str
    source_event_id: str
    trace_id: str
    host_output_execution_record_id: str
    host_output_event_ref_id: str
    dispatch_execution_record_id: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != HOST_OUTPUT_FACT_SCHEMA:
            raise P2r0IntegrityError("unknown Host fact schema")
        if type(self.platform_message_identity) is not PlatformMessageIdentityV1:
            raise P2r0IntegrityError("Host fact requires PlatformMessageIdentityV1")
        object.__setattr__(self, "fact_id", _check_hash_id(self.fact_id, HOST_FACT_PREFIX))
        object.__setattr__(self, "operation_index", _nonnegative_int(self.operation_index, "operation_index"))
        object.__setattr__(self, "operation_kind", _text(self.operation_kind, "operation_kind"))
        object.__setattr__(self, "status", _text(self.status, "status"))
        if self.operation_kind != MESSAGE_OPERATION:
            raise P2r0IntegrityError("only MESSAGE host operations are factual")
        if self.status != SEND_SUCCEEDED_WITH_IDENTITY:
            raise P2r0IntegrityError("Host fact requires SEND_SUCCEEDED_WITH_IDENTITY")
        for name in (
            "host_send_result_schema_version",
            "platform_send_receipt_schema_version",
            "source_event_id",
            "trace_id",
            "host_output_execution_record_id",
            "host_output_event_ref_id",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        expected_host = f"{self.trace_id}:1"
        if self.host_output_execution_record_id != expected_host:
            raise P2r0IntegrityError("Host output execution identity must be trace_id:1")
        if self.dispatch_execution_record_id is not None:
            dispatch = _text(self.dispatch_execution_record_id, "dispatch_execution_record_id")
            if dispatch != f"{self.trace_id}:2":
                raise P2r0IntegrityError("Dispatch execution identity must be trace_id:2")
            object.__setattr__(self, "dispatch_execution_record_id", dispatch)
        expected = _id(HOST_FACT_PREFIX, HOST_OUTPUT_FACT_DOMAIN, self._identity_body())
        if self.fact_id != expected:
            raise P2r0IntegrityError("Host fact id is not derived from its factual payload")

    def _identity_body(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "schema_version": self.schema_version,
                "platform_message_identity": self.platform_message_identity.canonical_body(),
                "operation_index": self.operation_index,
                "operation_kind": self.operation_kind,
                "status": self.status,
                "host_send_result_schema_version": self.host_send_result_schema_version,
                "platform_send_receipt_schema_version": self.platform_send_receipt_schema_version,
                "source_event_id": self.source_event_id,
                "trace_id": self.trace_id,
                "host_output_execution_record_id": self.host_output_execution_record_id,
                "host_output_event_ref_id": self.host_output_event_ref_id,
                "dispatch_execution_record_id": self.dispatch_execution_record_id,
            }
        )

    def canonical_payload(self) -> Mapping[str, object]:
        return MappingProxyType({**dict(self._identity_body()), "fact_id": self.fact_id})

    @classmethod
    def create(
        cls,
        *,
        platform_message_identity: PlatformMessageIdentityV1,
        operation_index: int,
        host_send_result_schema_version: str,
        platform_send_receipt_schema_version: str,
        source_event_id: str,
        trace_id: str,
        host_output_event_ref_id: str,
        dispatch_execution_record_id: str | None = None,
    ) -> HostOutputMessageIdentityFactV1:
        values = {
            "schema_version": HOST_OUTPUT_FACT_SCHEMA,
            "platform_message_identity": platform_message_identity,
            "operation_index": operation_index,
            "operation_kind": MESSAGE_OPERATION,
            "status": SEND_SUCCEEDED_WITH_IDENTITY,
            "host_send_result_schema_version": host_send_result_schema_version,
            "platform_send_receipt_schema_version": platform_send_receipt_schema_version,
            "source_event_id": source_event_id,
            "trace_id": trace_id,
            "host_output_execution_record_id": f"{trace_id}:1",
            "host_output_event_ref_id": host_output_event_ref_id,
            "dispatch_execution_record_id": dispatch_execution_record_id,
        }
        identity_body = {
            "schema_version": HOST_OUTPUT_FACT_SCHEMA,
            "platform_message_identity": platform_message_identity.canonical_body(),
            "operation_index": operation_index,
            "operation_kind": MESSAGE_OPERATION,
            "status": SEND_SUCCEEDED_WITH_IDENTITY,
            "host_send_result_schema_version": host_send_result_schema_version,
            "platform_send_receipt_schema_version": platform_send_receipt_schema_version,
            "source_event_id": source_event_id,
            "trace_id": trace_id,
            "host_output_execution_record_id": f"{trace_id}:1",
            "host_output_event_ref_id": host_output_event_ref_id,
            "dispatch_execution_record_id": dispatch_execution_record_id,
        }
        return cls(fact_id=_id(HOST_FACT_PREFIX, HOST_OUTPUT_FACT_DOMAIN, identity_body), **values)

    from_capture = create
    from_send_receipt = create


@dataclass(frozen=True, slots=True)
class InboundReplyReferenceFactV1:
    schema_version: str
    fact_id: str
    source_event_id: str
    source_platform_message_identity: PlatformMessageIdentityV1
    reply_target_platform_message_identity: PlatformMessageIdentityV1

    def __post_init__(self) -> None:
        if self.schema_version != INBOUND_REPLY_FACT_SCHEMA:
            raise P2r0IntegrityError("unknown inbound reply fact schema")
        if type(self.source_platform_message_identity) is not PlatformMessageIdentityV1:
            raise P2r0IntegrityError("Inbound fact requires source platform identity")
        if type(self.reply_target_platform_message_identity) is not PlatformMessageIdentityV1:
            raise P2r0IntegrityError("Inbound fact requires target platform identity")
        object.__setattr__(self, "source_event_id", _text(self.source_event_id, "source_event_id"))
        object.__setattr__(self, "fact_id", _check_hash_id(self.fact_id, INBOUND_FACT_PREFIX))
        expected = _id(INBOUND_FACT_PREFIX, INBOUND_REPLY_FACT_DOMAIN, self.identity_body())
        if self.fact_id != expected:
            raise P2r0IntegrityError("Inbound fact id is not derived from its factual payload")

    def identity_body(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "schema_version": INBOUND_REPLY_FACT_SCHEMA,
                "source_event_id": self.source_event_id,
                "source_platform_message_identity": self.source_platform_message_identity.canonical_body(),
                "reply_target_platform_message_identity": self.reply_target_platform_message_identity.canonical_body(),
            }
        )

    def canonical_payload(self) -> Mapping[str, object]:
        return MappingProxyType({**dict(self.identity_body()), "fact_id": self.fact_id})

    @classmethod
    def create(
        cls,
        *,
        source_event_id: str,
        source_platform_message_identity: PlatformMessageIdentityV1,
        reply_target_platform_message_identity: PlatformMessageIdentityV1,
    ) -> InboundReplyReferenceFactV1:
        values = {
            "schema_version": INBOUND_REPLY_FACT_SCHEMA,
            "source_event_id": source_event_id,
            "source_platform_message_identity": source_platform_message_identity,
            "reply_target_platform_message_identity": reply_target_platform_message_identity,
        }
        body = {
            "schema_version": INBOUND_REPLY_FACT_SCHEMA,
            "source_event_id": source_event_id,
            "source_platform_message_identity": source_platform_message_identity.canonical_body(),
            "reply_target_platform_message_identity": reply_target_platform_message_identity.canonical_body(),
        }
        return cls(fact_id=_id(INBOUND_FACT_PREFIX, INBOUND_REPLY_FACT_DOMAIN, body), **values)

    from_capture = create


@dataclass(frozen=True, slots=True)
class FactCaptureAuthorityBindingV1:
    schema_version: str
    fact_id: str
    transaction_id: str

    def __post_init__(self) -> None:
        if self.schema_version != FACT_CAPTURE_BINDING_SCHEMA:
            raise P2r0IntegrityError("unknown fact-capture binding schema")
        object.__setattr__(self, "fact_id", _text(self.fact_id, "fact_id"))
        tx = _check_hash_id(self.transaction_id, TX_PREFIX)
        object.__setattr__(self, "transaction_id", tx)

    def identity_body(self) -> Mapping[str, str]:
        return MappingProxyType(
            {"schema_version": FACT_CAPTURE_BINDING_SCHEMA, "fact_id": self.fact_id, "transaction_id": self.transaction_id}
        )

    canonical_payload = identity_body

    def fingerprint(self) -> str:
        """Deterministic binding-domain fingerprint (not a persisted field)."""
        return CanonicalHashV1.hash(FACT_CAPTURE_BINDING_DOMAIN, self.identity_body())


def _archive_identity_wire(identity: PlatformMessageIdentityV1) -> Mapping[str, object]:
    return {
        "$type": "PlatformMessageIdentityV1",
        "$schema": PLATFORM_MESSAGE_IDENTITY_SCHEMA,
        "fields": dict(identity.canonical_body()),
    }


def _archive_host_body(fact: HostOutputMessageIdentityFactV1) -> Mapping[str, object]:
    return {
        "$type": "HostOutputMessageIdentityFactV1",
        "$schema": HOST_OUTPUT_FACT_SCHEMA,
        "fields": {
            "schema_version": fact.schema_version,
            "fact_id": fact.fact_id,
            "platform_message_identity": _archive_identity_wire(fact.platform_message_identity),
            "operation_index": fact.operation_index,
            "operation_kind": fact.operation_kind,
            "status": fact.status,
            "host_send_result_schema_version": fact.host_send_result_schema_version,
            "platform_send_receipt_schema_version": fact.platform_send_receipt_schema_version,
            "source_event_id": fact.source_event_id,
            "trace_id": fact.trace_id,
            "host_output_execution_record_id": fact.host_output_execution_record_id,
            "host_output_event_ref_id": fact.host_output_event_ref_id,
            "dispatch_execution_record_id": fact.dispatch_execution_record_id,
        },
    }


def _archive_inbound_body(fact: InboundReplyReferenceFactV1) -> Mapping[str, object]:
    return {
        "$type": "InboundReplyReferenceFactV1",
        "$schema": INBOUND_REPLY_FACT_SCHEMA,
        "fields": {
            "schema_version": fact.schema_version,
            "fact_id": fact.fact_id,
            "source_event_id": fact.source_event_id,
            "source_platform_message_identity": _archive_identity_wire(fact.source_platform_message_identity),
            "reply_target_platform_message_identity": _archive_identity_wire(fact.reply_target_platform_message_identity),
        },
    }


def _archive_binding_body(binding: FactCaptureAuthorityBindingV1) -> Mapping[str, object]:
    return {
        "$type": "FactCaptureAuthorityBindingV1",
        "$schema": FACT_CAPTURE_BINDING_SCHEMA,
        "fields": {
            "schema_version": binding.schema_version,
            "fact_id": binding.fact_id,
            "transaction_id": binding.transaction_id,
        },
    }


@dataclass(frozen=True, slots=True)
class P2rReplyLinkFactArchiveV1:
    schema_version: str
    archive_id: str
    review_run_id: str
    episode_id: str
    input_snapshot_hash: str
    p2_run_snapshot_logical_commit_hash: str
    p2r0_encoding_profile_hash: str
    host_output_facts: tuple[HostOutputMessageIdentityFactV1, ...]
    inbound_reply_facts: tuple[InboundReplyReferenceFactV1, ...]
    fact_capture_authority: tuple[FactCaptureAuthorityBindingV1, ...]
    archive_payload_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != ARCHIVE_SCHEMA:
            raise P2r0IntegrityError("unknown reply-link archive schema")
        object.__setattr__(self, "review_run_id", _text(self.review_run_id, "review_run_id"))
        object.__setattr__(self, "episode_id", _text(self.episode_id, "episode_id"))
        object.__setattr__(self, "input_snapshot_hash", _hash_text(self.input_snapshot_hash, "input_snapshot_hash"))
        object.__setattr__(self, "p2_run_snapshot_logical_commit_hash", _hash_text(self.p2_run_snapshot_logical_commit_hash, "p2_run_snapshot_logical_commit_hash"))
        object.__setattr__(self, "p2r0_encoding_profile_hash", _hash_text(self.p2r0_encoding_profile_hash, "p2r0_encoding_profile_hash"))
        hosts = tuple(self.host_output_facts)
        inbound = tuple(self.inbound_reply_facts)
        bindings = tuple(self.fact_capture_authority)
        if any(type(item) is not HostOutputMessageIdentityFactV1 for item in hosts):
            raise P2r0IntegrityError("archive Host facts must be canonical Host facts")
        if any(type(item) is not InboundReplyReferenceFactV1 for item in inbound):
            raise P2r0IntegrityError("archive inbound facts must be canonical inbound facts")
        if any(type(item) is not FactCaptureAuthorityBindingV1 for item in bindings):
            raise P2r0IntegrityError("archive capture bindings must be canonical bindings")
        hosts = tuple(sorted(hosts, key=lambda item: item.fact_id))
        inbound = tuple(sorted(inbound, key=lambda item: item.fact_id))
        bindings = tuple(sorted(bindings, key=lambda item: item.fact_id))
        all_ids = tuple(item.fact_id for item in hosts) + tuple(item.fact_id for item in inbound)
        if len(set(all_ids)) != len(all_ids):
            raise P2r0IntegrityError("archive contains duplicate fact identity")
        inbound_source_ids = tuple(item.source_event_id for item in inbound)
        if len(set(inbound_source_ids)) != len(inbound_source_ids):
            raise P2r0IntegrityError("archive contains multiple inbound targets for one source event")
        if tuple(item.fact_id for item in bindings) != tuple(sorted(all_ids)):
            raise P2r0IntegrityError("every archived fact needs exactly one authority binding")
        # The binding-domain fingerprint is deliberately not persisted; calling
        # it here makes the frozen domain part of archive validation rather than
        # an unused declaration.
        for binding in bindings:
            binding.fingerprint()
        object.__setattr__(self, "host_output_facts", hosts)
        object.__setattr__(self, "inbound_reply_facts", inbound)
        object.__setattr__(self, "fact_capture_authority", bindings)
        expected_payload = CanonicalHashV1.hash(ARCHIVE_PAYLOAD_DOMAIN, self.payload_body())
        if self.archive_payload_hash != expected_payload:
            raise P2r0IntegrityError("archive payload hash mismatch")
        expected_id = ARCHIVE_PREFIX + CanonicalHashV1.digest(
            ARCHIVE_IDENTITY_DOMAIN, {**dict(self.payload_body()), "archive_payload_hash": expected_payload}
        )
        if self.archive_id != expected_id:
            raise P2r0IntegrityError("archive id is not derived from the acyclic archive body")

    def payload_body(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "schema_version": ARCHIVE_SCHEMA,
                "review_run_id": self.review_run_id,
                "episode_id": self.episode_id,
                "input_snapshot_hash": self.input_snapshot_hash,
                "p2_run_snapshot_logical_commit_hash": self.p2_run_snapshot_logical_commit_hash,
                "p2r0_encoding_profile_hash": self.p2r0_encoding_profile_hash,
                # Archive identity hashes the canonical factual representation,
                # never Python dataclass objects or their object identity.
                "host_output_facts": tuple(_archive_host_body(item) for item in self.host_output_facts),
                "inbound_reply_facts": tuple(_archive_inbound_body(item) for item in self.inbound_reply_facts),
                "fact_capture_authority": tuple(_archive_binding_body(item) for item in self.fact_capture_authority),
            }
        )

    canonical_payload = payload_body

    def derive_exact_reply_link(
        self,
        inbound_reply_fact_id: str,
        host_fact_constraint: HostOutputMessageIdentityFactV1 | str | None = None,
    ) -> ExactHostReplyLinkV1:
        return ExactHostReplyLinkV1.derive(self, inbound_reply_fact_id, host_fact_constraint)

    @classmethod
    def create(
        cls,
        *,
        review_run_id: str,
        episode_id: str,
        input_snapshot_hash: str,
        p2_run_snapshot_logical_commit_hash: str,
        p2r0_encoding_profile_hash: str,
        host_output_facts: tuple[HostOutputMessageIdentityFactV1, ...] = (),
        inbound_reply_facts: tuple[InboundReplyReferenceFactV1, ...] = (),
        fact_capture_authority: tuple[FactCaptureAuthorityBindingV1, ...] = (),
    ) -> P2rReplyLinkFactArchiveV1:
        values = {
            "schema_version": ARCHIVE_SCHEMA,
            "review_run_id": review_run_id,
            "episode_id": episode_id,
            "input_snapshot_hash": input_snapshot_hash,
            "p2_run_snapshot_logical_commit_hash": p2_run_snapshot_logical_commit_hash,
            "p2r0_encoding_profile_hash": p2r0_encoding_profile_hash,
            "host_output_facts": host_output_facts,
            "inbound_reply_facts": inbound_reply_facts,
            "fact_capture_authority": fact_capture_authority,
        }
        # A valid archive is only constructible once all hashes and bindings
        # are available.  The provisional values are never exposed.
        normalized_hosts = tuple(sorted(host_output_facts, key=lambda item: item.fact_id))
        normalized_inbound = tuple(sorted(inbound_reply_facts, key=lambda item: item.fact_id))
        normalized_bindings = tuple(sorted(fact_capture_authority, key=lambda item: item.fact_id))
        provisional_body = {
            "schema_version": ARCHIVE_SCHEMA,
            "review_run_id": _text(review_run_id, "review_run_id"),
            "episode_id": _text(episode_id, "episode_id"),
            "input_snapshot_hash": _text(input_snapshot_hash, "input_snapshot_hash"),
            "p2_run_snapshot_logical_commit_hash": _text(p2_run_snapshot_logical_commit_hash, "p2_run_snapshot_logical_commit_hash"),
            "p2r0_encoding_profile_hash": _text(p2r0_encoding_profile_hash, "p2r0_encoding_profile_hash"),
            "host_output_facts": tuple(_archive_host_body(item) for item in normalized_hosts),
            "inbound_reply_facts": tuple(_archive_inbound_body(item) for item in normalized_inbound),
            "fact_capture_authority": tuple(_archive_binding_body(item) for item in normalized_bindings),
        }
        payload_hash = CanonicalHashV1.hash(ARCHIVE_PAYLOAD_DOMAIN, provisional_body)
        archive_id = ARCHIVE_PREFIX + CanonicalHashV1.digest(
            ARCHIVE_IDENTITY_DOMAIN, {**provisional_body, "archive_payload_hash": payload_hash}
        )
        return cls(archive_payload_hash=payload_hash, archive_id=archive_id, **values)

    from_facts = create

    @classmethod
    def from_p2_run_snapshot(
        cls,
        p2_run_snapshot: object,
        *,
        p2r0_encoding_profile_hash: str,
        host_output_facts: tuple[HostOutputMessageIdentityFactV1, ...] = (),
        inbound_reply_facts: tuple[InboundReplyReferenceFactV1, ...] = (),
        fact_capture_authority: tuple[FactCaptureAuthorityBindingV1, ...] = (),
    ) -> P2rReplyLinkFactArchiveV1:
        from .promotion_infrastructure import P2ReviewRunWithSnapshotV1

        if type(p2_run_snapshot) is not P2ReviewRunWithSnapshotV1:
            raise P2r0IntegrityError("archive requires an authoritative P2ReviewRunWithSnapshotV1")
        run = p2_run_snapshot.run
        return cls.create(
            review_run_id=run.review_run_id,
            episode_id=run.episode_id,
            input_snapshot_hash=run.input_snapshot_hash,
            p2_run_snapshot_logical_commit_hash=p2_run_snapshot.logical_commit_hash,
            p2r0_encoding_profile_hash=p2r0_encoding_profile_hash,
            host_output_facts=host_output_facts,
            inbound_reply_facts=inbound_reply_facts,
            fact_capture_authority=fact_capture_authority,
        )


EXACT_REPLY_LINK_STATUSES = frozenset(
    {
        "EXACT_REPLY_LINK",
        "EXACT_REPLY_LINK_UNAVAILABLE",
        "EXACT_REPLY_LINK_UNAVAILABLE_NO_INBOUND_FACT",
        "EXACT_REPLY_LINK_UNAVAILABLE_CONFLICT",
    }
)


@dataclass(frozen=True, slots=True)
class ExactHostReplyLinkV1:
    schema_version: str
    link_id: str
    archive_id: str
    archive_payload_hash: str
    review_run_id: str
    episode_id: str
    inbound_reply_fact_id: str | None
    host_output_fact_id: str | None
    matched_platform_message_identity: PlatformMessageIdentityV1 | None
    status: str

    def __post_init__(self) -> None:
        if self.schema_version != EXACT_REPLY_LINK_SCHEMA:
            raise P2r0IntegrityError("unknown exact reply-link schema")
        object.__setattr__(self, "archive_id", _check_hash_id(self.archive_id, ARCHIVE_PREFIX))
        object.__setattr__(self, "archive_payload_hash", _hash_text(self.archive_payload_hash, "archive_payload_hash"))
        object.__setattr__(self, "review_run_id", _text(self.review_run_id, "review_run_id"))
        object.__setattr__(self, "episode_id", _text(self.episode_id, "episode_id"))
        if self.inbound_reply_fact_id is not None:
            object.__setattr__(self, "inbound_reply_fact_id", _check_hash_id(self.inbound_reply_fact_id, INBOUND_FACT_PREFIX))
        if self.host_output_fact_id is not None:
            object.__setattr__(self, "host_output_fact_id", _check_hash_id(self.host_output_fact_id, HOST_FACT_PREFIX))
        if self.matched_platform_message_identity is not None and type(self.matched_platform_message_identity) is not PlatformMessageIdentityV1:
            raise P2r0IntegrityError("link matched identity must be PlatformMessageIdentityV1")
        object.__setattr__(self, "status", _text(self.status, "status"))
        if self.status not in EXACT_REPLY_LINK_STATUSES:
            raise P2r0IntegrityError("unknown exact reply-link status")
        body = self.identity_body()
        expected = REPLY_LINK_PREFIX + CanonicalHashV1.digest(EXACT_REPLY_LINK_DOMAIN, body)
        if self.link_id != expected:
            raise P2r0IntegrityError("exact reply-link id mismatch")

    def identity_body(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "schema_version": EXACT_REPLY_LINK_SCHEMA,
                "archive_id": self.archive_id,
                "archive_payload_hash": self.archive_payload_hash,
                "review_run_id": self.review_run_id,
                "episode_id": self.episode_id,
                "inbound_reply_fact_id": self.inbound_reply_fact_id,
                "host_output_fact_id": self.host_output_fact_id,
                "matched_platform_message_identity": self.matched_platform_message_identity.canonical_body() if self.matched_platform_message_identity else None,
                "status": self.status,
            }
        )

    canonical_payload = identity_body

    @classmethod
    def derive(
        cls,
        archive: P2rReplyLinkFactArchiveV1,
        inbound_reply_fact_id: str,
        host_fact_constraint: HostOutputMessageIdentityFactV1 | str | None = None,
    ) -> ExactHostReplyLinkV1:
        if type(archive) is not P2rReplyLinkFactArchiveV1:
            raise P2r0IntegrityError("exact reply-link derivation requires an archive")
        inbound = next((item for item in archive.inbound_reply_facts if item.fact_id == inbound_reply_fact_id), None)
        matches = [
            item for item in archive.host_output_facts
            if inbound is not None and item.platform_message_identity == inbound.reply_target_platform_message_identity
        ]
        if host_fact_constraint is not None:
            constrained_id = host_fact_constraint.fact_id if type(host_fact_constraint) is HostOutputMessageIdentityFactV1 else _check_hash_id(host_fact_constraint, HOST_FACT_PREFIX)
            matches = [item for item in matches if item.fact_id == constrained_id]
        if inbound is None:
            status = "EXACT_REPLY_LINK_UNAVAILABLE_NO_INBOUND_FACT"
            chosen = None
        elif len(matches) == 0:
            status = "EXACT_REPLY_LINK_UNAVAILABLE"
            chosen = None
        elif len(matches) > 1:
            status = "EXACT_REPLY_LINK_UNAVAILABLE_CONFLICT"
            chosen = None
        else:
            status = "EXACT_REPLY_LINK"
            chosen = matches[0]
        values = {
            "schema_version": EXACT_REPLY_LINK_SCHEMA,
            "archive_id": archive.archive_id,
            "archive_payload_hash": archive.archive_payload_hash,
            "review_run_id": archive.review_run_id,
            "episode_id": archive.episode_id,
            "inbound_reply_fact_id": inbound.fact_id if inbound else None,
            "host_output_fact_id": chosen.fact_id if chosen else None,
            "matched_platform_message_identity": chosen.platform_message_identity if chosen else None,
            "status": status,
        }
        hash_body = dict(values)
        matched = hash_body["matched_platform_message_identity"]
        hash_body["matched_platform_message_identity"] = matched.canonical_body() if matched is not None else None
        return cls(link_id=REPLY_LINK_PREFIX + CanonicalHashV1.digest(EXACT_REPLY_LINK_DOMAIN, hash_body), **values)


def resolve_exact_host_reply_link(
    archive: P2rReplyLinkFactArchiveV1,
    inbound_reply_fact_id: str,
    host_fact_constraint: HostOutputMessageIdentityFactV1 | str | None = None,
) -> ExactHostReplyLinkV1:
    """Pure archive-only exact-link derivation; it performs no I/O."""
    return ExactHostReplyLinkV1.derive(archive, inbound_reply_fact_id, host_fact_constraint)


derive_exact_host_reply_link = resolve_exact_host_reply_link


class P2r0FindingAttributionConflict(P2r0IntegrityError):
    """A Finding ref joins more than one distinct Host fact."""


def _p1_wire_fields(payload: object, type_name: str) -> Mapping[str, object]:
    if not isinstance(payload, Mapping) or set(payload) != {"__type__", "fields"}:
        raise P2r0IntegrityError(f"invalid P1 {type_name} wire envelope")
    if payload.get("__type__") != type_name or not isinstance(payload.get("fields"), Mapping):
        raise P2r0IntegrityError(f"invalid P1 {type_name} wire type")
    return payload["fields"]


def _authoritative_p2_context(
    archive: P2rReplyLinkFactArchiveV1,
    p2_authority: object | None,
) -> tuple[object, Mapping[str, object], object]:
    """Resolve a P2a Run from the bound persisted authority, never caller data."""
    from .promotion_infrastructure import (
        P2CanonicalArtifactEncoderV1,
        P2PromotionStore,
        P2ReviewRunWithSnapshotV1,
        PromotionSnapshotArchiveV1,
        _decode_run,
        _decode_unpadded_base64url,
        _strict_envelope,
        _validated_p1_snapshot_bytes,
    )

    if type(p2_authority) is not P2PromotionStore or p2_authority.root != "production" or p2_authority._synthetic_enabled:
        raise P2r0IntegrityError("authoritative P2 source is not the production P2PromotionStore")
    raw_commit = p2_authority._run_commits.get(archive.review_run_id)
    if not isinstance(raw_commit, Mapping):
        raise P2r0IntegrityError("authoritative P2 Run is unavailable")
    fields = _strict_envelope(raw_commit, "P2ReviewRunWithSnapshotV1")
    if fields.get("schema_version") != "p2a.review-run-with-snapshot.v1":
        raise P2r0IntegrityError("authoritative P2 wrapper schema mismatch")
    profile_hash = fields.get("encoding_profile_hash")
    profile = p2_authority._profile_objects.get(profile_hash)
    if profile is None:
        raise P2r0IntegrityError("authoritative P2 encoding profile is unavailable")
    encoder = P2CanonicalArtifactEncoderV1(profile)
    run = _decode_run(fields["run"])
    archive_fields = _strict_envelope(fields["archive"], "PromotionSnapshotArchiveV1")
    raw_bytes_wrapper = archive_fields.get("canonical_snapshot_json_utf8")
    if not isinstance(raw_bytes_wrapper, Mapping) or set(raw_bytes_wrapper) != {"$bytes"}:
        raise P2r0IntegrityError("authoritative P2 snapshot archive bytes are unavailable")
    raw_snapshot = _decode_unpadded_base64url(raw_bytes_wrapper["$bytes"])
    p2_archive = PromotionSnapshotArchiveV1(
        archive_fields["schema_version"], archive_fields["review_run_id"], archive_fields["episode_id"],
        archive_fields["input_snapshot_hash"], raw_snapshot, archive_fields["archive_payload_hash"],
    )
    p2_run = P2ReviewRunWithSnapshotV1(
        fields["schema_version"], run, p2_archive, fields["encoding_profile_hash"], fields["logical_commit_hash"],
    )
    try:
        persisted = p2_authority.require_archive(run)
    except Exception as exc:
        raise P2r0IntegrityError("authoritative P2 Run archive cannot be read") from exc
    if CanonicalHashV1.canonical_json_utf8(persisted) != CanonicalHashV1.canonical_json_utf8(raw_commit):
        raise P2r0IntegrityError("authoritative P2 Run archive differs from persisted index")
    # P2a's validator checks the wrapper/profile/commit identity.  The strict
    # P1 decoder below additionally proves the archived bytes are the exact
    # frozen ReviewInputSnapshot, without consulting current runtime state.
    p2_run.validate(encoder)
    validated_snapshot = _validated_p1_snapshot_bytes(raw_snapshot, run.episode_id)
    if run.review_run_id != archive.review_run_id or run.episode_id != archive.episode_id:
        raise P2r0IntegrityError("P2 Run/Episode lineage does not match P2r0 archive")
    if run.input_snapshot_hash != archive.input_snapshot_hash:
        raise P2r0IntegrityError("P2 Run/input snapshot lineage does not match P2r0 archive")
    if p2_run.logical_commit_hash != archive.p2_run_snapshot_logical_commit_hash:
        raise P2r0IntegrityError("P2 logical commit lineage does not match P2r0 archive")
    return p2_run, validated_snapshot, encoder


def _snapshot_host_event_and_record(snapshot: Mapping[str, object], ref_id: str) -> tuple[Mapping[str, object], Mapping[str, object]] | None:
    episode_fields = _p1_wire_fields(snapshot.get("episode"), "iris_memory.cognitive.episode.Episode")
    event_refs = episode_fields.get("event_refs")
    if type(event_refs) not in (tuple, list):
        raise P2r0IntegrityError("P1 snapshot Episode event_refs are not canonical")
    matching_events: list[Mapping[str, object]] = []
    for event in event_refs:
        fields = _p1_wire_fields(event, "iris_memory.cognitive.episode.EpisodeEventRef")
        if fields.get("ref_id") == ref_id:
            matching_events.append(fields)
    if len(matching_events) != 1:
        if len(matching_events) > 1:
            raise P2r0FindingAttributionConflict("P2R0_FINDING_EVENT_REF_CONFLICT")
        return None
    event = matching_events[0]
    if event.get("kind") != "HOST_OUTPUT":
        return None
    trace_id = event.get("trace_id")
    source_event_id = event.get("source_event_id")
    execution_record_id = event.get("execution_record_id")
    if type(trace_id) is not str or not trace_id or type(source_event_id) is not str or not source_event_id:
        return None
    if type(execution_record_id) is not str or execution_record_id != f"{trace_id}:1":
        return None
    facts = snapshot.get("fact_envelopes")
    if type(facts) not in (tuple, list):
        raise P2r0IntegrityError("P1 snapshot fact_envelopes are not canonical")
    records: list[Mapping[str, object]] = []
    for envelope in facts:
        if not isinstance(envelope, Mapping) or set(envelope) != {"source_type", "ref_id", "schema_version", "payload"}:
            raise P2r0IntegrityError("P1 fact envelope shape mismatch")
        if envelope.get("source_type") != "HOST_RESULT" or envelope.get("ref_id") != ref_id:
            continue
        record = _p1_wire_fields(envelope.get("payload"), "iris_memory.cognitive.contracts.BehaviorExecutionRecord")
        trace = _p1_wire_fields(record.get("trace"), "iris_memory.cognitive.contracts.BehaviorTrace")
        if (
            record.get("stage") == "HOST_OUTPUT"
            and trace.get("trace_id") == trace_id
            and trace.get("event_id") == source_event_id
            and record.get("revision") == 1
        ):
            records.append(record)
    if len(records) != 1:
        if len(records) > 1:
            raise P2r0FindingAttributionConflict("P2R0_FINDING_HOST_RECORD_CONFLICT")
        return None
    return event, records[0]


def resolve_finding_host_fact(
    finding: object,
    archive: P2rReplyLinkFactArchiveV1,
    *,
    p2_authority: object | None = None,
) -> HostOutputMessageIdentityFactV1 | None:
    """Join ``Finding.attributed_to.ref_id`` to Host event-ref identity.

    The Finding ref is an Episode ``HOST_OUTPUT`` event ref, never a Host fact
    id.  A missing match is simply ineligible; multiple distinct facts are an
    integrity conflict.
    """
    if type(archive) is not P2rReplyLinkFactArchiveV1:
        raise P2r0IntegrityError("finding join requires an authoritative archive")
    if p2_authority is None:
        return None
    p2_run, snapshot, encoder = _authoritative_p2_context(archive, p2_authority)
    from .review import ReviewFinding

    if type(finding) is not ReviewFinding:
        return None
    authoritative_findings = [item for item in p2_run.run.findings if item.finding_id == finding.finding_id]
    if len(authoritative_findings) != 1:
        if len(authoritative_findings) > 1:
            raise P2r0FindingAttributionConflict("P2R0_FINDING_MEMBERSHIP_CONFLICT")
        return None
    if CanonicalHashV1.canonical_json_utf8(encoder.encode(authoritative_findings[0])) != CanonicalHashV1.canonical_json_utf8(encoder.encode(finding)):
        return None
    if finding.review_run_id != archive.review_run_id or finding.episode_id != archive.episode_id:
        return None
    attributed = getattr(finding, "attributed_to", None)
    if attributed is None or getattr(attributed, "target_type", None) is not AttributionTargetType.HOST_RESULT:
        return None
    ref_id = getattr(attributed, "ref_id", None)
    if type(ref_id) is not str or not ref_id:
        return None
    event_and_record = _snapshot_host_event_and_record(snapshot, ref_id)
    if event_and_record is None:
        return None
    event, record = event_and_record
    matches = [
        item for item in archive.host_output_facts
        if item.host_output_event_ref_id == ref_id
        and item.source_event_id == event.get("source_event_id")
        and item.trace_id == event.get("trace_id")
        and item.host_output_execution_record_id == event.get("execution_record_id") == f"{item.trace_id}:1"
        and record.get("stage") == "HOST_OUTPUT"
        and _p1_wire_fields(record.get("trace"), "iris_memory.cognitive.contracts.BehaviorTrace").get("trace_id") == item.trace_id
        and _p1_wire_fields(record.get("trace"), "iris_memory.cognitive.contracts.BehaviorTrace").get("event_id") == item.source_event_id
        and record.get("revision") == 1
    ]
    if len({item.fact_id for item in matches}) > 1:
        raise P2r0FindingAttributionConflict("P2R0_FINDING_ATTRIBUTION_CONFLICT")
    return matches[0] if matches else None


def resolve_finding_host_fact_id(
    finding: object,
    archive: P2rReplyLinkFactArchiveV1,
    *,
    p2_authority: object | None = None,
) -> str | None:
    fact = resolve_finding_host_fact(finding, archive, p2_authority=p2_authority)
    return fact.fact_id if fact is not None else None


def validate_archive_p2_lineage(archive: P2rReplyLinkFactArchiveV1, p2_run_snapshot: object) -> None:
    """Validate optional transitive P2a lineage without reconstructing runtime state.

    The P2r0 archive stores only the authoritative P2a logical commit hash.  A
    caller that already has the immutable P2a ``P2ReviewRunWithSnapshotV1`` may
    provide it here; no current Episode/Outcome lookup or legacy backfill is
    performed.
    """
    from .promotion_infrastructure import P2ReviewRunWithSnapshotV1

    if type(archive) is not P2rReplyLinkFactArchiveV1 or type(p2_run_snapshot) is not P2ReviewRunWithSnapshotV1:
        raise P2r0IntegrityError("archive lineage requires an authoritative P2ReviewRunWithSnapshotV1")
    run = p2_run_snapshot.run
    if (
        p2_run_snapshot.logical_commit_hash != archive.p2_run_snapshot_logical_commit_hash
        or run.review_run_id != archive.review_run_id
        or run.episode_id != archive.episode_id
        or run.input_snapshot_hash != archive.input_snapshot_hash
    ):
        raise P2r0IntegrityError("P2r0 archive does not bind the exact P2a Run/snapshot lineage")


@dataclass(frozen=True, slots=True)
class P2r0CanonicalTypeProfileV1:
    type_name: str
    schema: str
    fields: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "type_name", _text(self.type_name, "type_name"))
        object.__setattr__(self, "schema", _text(self.schema, "schema"))
        fields = tuple(self.fields)
        if not fields or any(type(item) is not str or not item for item in fields) or len(set(fields)) != len(fields):
            raise P2r0IntegrityError("profile fields must be unique non-empty strings")
        object.__setattr__(self, "fields", fields)


@dataclass(frozen=True, slots=True)
class FrozenP2r0CanonicalEncodingProfileV1:
    profile_id: str
    profile_version: str
    type_profiles: tuple[P2r0CanonicalTypeProfileV1, ...]
    schema_version: str = P2R0_PROFILE_SCHEMA

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_id", _text(self.profile_id, "profile_id"))
        object.__setattr__(self, "profile_version", _text(self.profile_version, "profile_version"))
        object.__setattr__(self, "schema_version", _text(self.schema_version, "schema_version"))
        profiles = tuple(self.type_profiles)
        if any(type(item) is not P2r0CanonicalTypeProfileV1 for item in profiles):
            raise P2r0IntegrityError("profile entries must be P2r0CanonicalTypeProfileV1")
        names = tuple(item.type_name for item in profiles)
        if not names or len(set(names)) != len(names):
            raise P2r0IntegrityError("profile type names must be unique")
        object.__setattr__(self, "type_profiles", profiles)

    def semantic_payload(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "schema_version": self.schema_version,
                "profile_id": self.profile_id,
                "profile_version": self.profile_version,
                "type_profiles": tuple(
                    {"type_name": item.type_name, "schema": item.schema, "fields": item.fields}
                    for item in self.type_profiles
                ),
            }
        )

    @property
    def profile_hash(self) -> str:
        return p2r0_encoding_profile_hash(self)


_P2R0_FIELDS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "PlatformMessageIdentityV1": ("platform_id", "account_id", "conversation_id", "message_id"),
        "HostOutputMessageIdentityFactV1": (
            "schema_version", "fact_id", "platform_message_identity", "operation_index", "operation_kind", "status",
            "host_send_result_schema_version", "platform_send_receipt_schema_version", "source_event_id", "trace_id",
            "host_output_execution_record_id", "host_output_event_ref_id", "dispatch_execution_record_id",
        ),
        "InboundReplyReferenceFactV1": (
            "schema_version", "fact_id", "source_event_id", "source_platform_message_identity",
            "reply_target_platform_message_identity",
        ),
        "FactCaptureAuthorityBindingV1": ("schema_version", "fact_id", "transaction_id"),
        "P2rReplyLinkFactArchiveV1": (
            "schema_version", "archive_id", "review_run_id", "episode_id", "input_snapshot_hash",
            "p2_run_snapshot_logical_commit_hash", "p2r0_encoding_profile_hash", "host_output_facts",
            "inbound_reply_facts", "fact_capture_authority", "archive_payload_hash",
        ),
        "ExactHostReplyLinkV1": (
            "schema_version", "link_id", "archive_id", "archive_payload_hash", "review_run_id", "episode_id",
            "inbound_reply_fact_id", "host_output_fact_id", "matched_platform_message_identity", "status",
        ),
    }
)
_P2R0_SCHEMAS: Mapping[str, str] = MappingProxyType(
    {
        "PlatformMessageIdentityV1": PLATFORM_MESSAGE_IDENTITY_SCHEMA,
        "HostOutputMessageIdentityFactV1": HOST_OUTPUT_FACT_SCHEMA,
        "InboundReplyReferenceFactV1": INBOUND_REPLY_FACT_SCHEMA,
        "FactCaptureAuthorityBindingV1": FACT_CAPTURE_BINDING_SCHEMA,
        "P2rReplyLinkFactArchiveV1": ARCHIVE_SCHEMA,
        "ExactHostReplyLinkV1": EXACT_REPLY_LINK_SCHEMA,
    }
)


def default_p2r0_encoding_profile() -> FrozenP2r0CanonicalEncodingProfileV1:
    return FrozenP2r0CanonicalEncodingProfileV1(
        P2R0_PROFILE_ID,
        "1",
        tuple(P2r0CanonicalTypeProfileV1(name, _P2R0_SCHEMAS[name], fields) for name, fields in _P2R0_FIELDS.items()),
    )


def p2r0_encoding_profile_hash(profile: FrozenP2r0CanonicalEncodingProfileV1 | None = None) -> str:
    profile = profile or default_p2r0_encoding_profile()
    if type(profile) is not FrozenP2r0CanonicalEncodingProfileV1:
        raise P2r0IntegrityError("expected frozen P2r0 encoding profile")
    if profile.schema_version != P2R0_PROFILE_SCHEMA or profile.profile_id != P2R0_PROFILE_ID or profile.profile_version != "1":
        raise P2r0IntegrityError("P2r0 profile identity/schema mismatch")
    profile_entries = {item.type_name: item for item in profile.type_profiles}
    if set(profile_entries) != set(_P2R0_FIELDS):
        raise P2r0IntegrityError("P2r0 profile must contain the exact closed type table")
    for name, expected_fields in _P2R0_FIELDS.items():
        item = profile_entries[name]
        if item.schema != _P2R0_SCHEMAS[name] or item.fields != expected_fields:
            raise P2r0IntegrityError(f"P2r0 profile table mismatch for {name}")
    return CanonicalHashV1.hash(P2R0_PROFILE_HASH_DOMAIN, profile.semantic_payload())


canonical_artifact_encoding_profile_hash_p2r0 = p2r0_encoding_profile_hash


class P2r0CanonicalArtifactEncoderV1:
    """Closed explicit encoder for the six P2r0 artifacts."""

    def __init__(self, profile: FrozenP2r0CanonicalEncodingProfileV1 | None = None) -> None:
        supplied = profile or default_p2r0_encoding_profile()
        if type(supplied) is not FrozenP2r0CanonicalEncodingProfileV1:
            raise P2r0IntegrityError("expected frozen P2r0 encoding profile")
        self.profile = FrozenP2r0CanonicalEncodingProfileV1(
            supplied.profile_id,
            supplied.profile_version,
            tuple(P2r0CanonicalTypeProfileV1(item.type_name, item.schema, tuple(item.fields)) for item in supplied.type_profiles),
            supplied.schema_version,
        )
        self.profile_hash = p2r0_encoding_profile_hash(self.profile)
        if self.profile.schema_version != P2R0_PROFILE_SCHEMA or self.profile.profile_id != P2R0_PROFILE_ID or self.profile.profile_version != "1":
            raise P2r0IntegrityError("P2r0 profile identity/schema mismatch")
        self._profiles = {item.type_name: item for item in self.profile.type_profiles}
        if set(self._profiles) != set(_P2R0_FIELDS):
            raise P2r0IntegrityError("P2r0 profile must contain the exact closed type table")
        for name, fields in _P2R0_FIELDS.items():
            profile_item = self._profiles[name]
            if profile_item.schema != _P2R0_SCHEMAS[name] or profile_item.fields != fields:
                raise P2r0IntegrityError(f"P2r0 profile table mismatch for {name}")

    def _envelope(self, name: str, fields: Mapping[str, object]) -> Mapping[str, object]:
        expected = _P2R0_FIELDS[name]
        if tuple(fields) != expected:
            raise P2r0IntegrityError(f"P2r0 field order/shape mismatch for {name}")
        return {
            "$type": name,
            "$schema": self._profiles[name].schema,
            "fields": dict(fields),
        }

    def encode(self, value: object) -> Mapping[str, object]:
        if type(value) is PlatformMessageIdentityV1:
            return _deep_freeze(self._envelope("PlatformMessageIdentityV1", value.canonical_body()))  # type: ignore[return-value]
        if type(value) is HostOutputMessageIdentityFactV1:
            return _deep_freeze(self._envelope("HostOutputMessageIdentityFactV1", {
                "schema_version": value.schema_version, "fact_id": value.fact_id,
                "platform_message_identity": self.encode(value.platform_message_identity), "operation_index": value.operation_index,
                "operation_kind": value.operation_kind, "status": value.status,
                "host_send_result_schema_version": value.host_send_result_schema_version,
                "platform_send_receipt_schema_version": value.platform_send_receipt_schema_version,
                "source_event_id": value.source_event_id, "trace_id": value.trace_id,
                "host_output_execution_record_id": value.host_output_execution_record_id,
                "host_output_event_ref_id": value.host_output_event_ref_id,
                "dispatch_execution_record_id": value.dispatch_execution_record_id,
            }))  # type: ignore[return-value]
        if type(value) is InboundReplyReferenceFactV1:
            return _deep_freeze(self._envelope("InboundReplyReferenceFactV1", {
                "schema_version": value.schema_version,
                "fact_id": value.fact_id,
                "source_event_id": value.source_event_id,
                "source_platform_message_identity": self.encode(value.source_platform_message_identity),
                "reply_target_platform_message_identity": self.encode(value.reply_target_platform_message_identity),
            }))  # type: ignore[return-value]
        if type(value) is FactCaptureAuthorityBindingV1:
            return _deep_freeze(self._envelope("FactCaptureAuthorityBindingV1", {
                "schema_version": value.schema_version,
                "fact_id": value.fact_id, "transaction_id": value.transaction_id,
            }))  # type: ignore[return-value]
        if type(value) is P2rReplyLinkFactArchiveV1:
            return _deep_freeze(self._envelope("P2rReplyLinkFactArchiveV1", {
                "schema_version": value.schema_version, "archive_id": value.archive_id,
                "review_run_id": value.review_run_id, "episode_id": value.episode_id,
                "input_snapshot_hash": value.input_snapshot_hash,
                "p2_run_snapshot_logical_commit_hash": value.p2_run_snapshot_logical_commit_hash,
                "p2r0_encoding_profile_hash": value.p2r0_encoding_profile_hash,
                "host_output_facts": tuple(self.encode(item) for item in value.host_output_facts),
                "inbound_reply_facts": tuple(self.encode(item) for item in value.inbound_reply_facts),
                "fact_capture_authority": tuple(self.encode(item) for item in value.fact_capture_authority),
                "archive_payload_hash": value.archive_payload_hash,
            }))  # type: ignore[return-value]
        if type(value) is ExactHostReplyLinkV1:
            return _deep_freeze(self._envelope("ExactHostReplyLinkV1", {
                "schema_version": value.schema_version, "link_id": value.link_id,
                "archive_id": value.archive_id, "archive_payload_hash": value.archive_payload_hash,
                "review_run_id": value.review_run_id, "episode_id": value.episode_id,
                "inbound_reply_fact_id": value.inbound_reply_fact_id, "host_output_fact_id": value.host_output_fact_id,
                "matched_platform_message_identity": self.encode(value.matched_platform_message_identity) if value.matched_platform_message_identity else None,
                "status": value.status,
            }))  # type: ignore[return-value]
        raise P2r0IntegrityError(f"unsupported P2r0 artifact: {type(value).__name__}")


# Backward/ergonomic aliases.  The aliases do not introduce another wire
# format; they all resolve to the same closed P2r0 profile and encoder.
P2r0CanonicalEncodingProfileV1 = FrozenP2r0CanonicalEncodingProfileV1
FrozenCanonicalArtifactEncodingProfileP2r0V1 = FrozenP2r0CanonicalEncodingProfileV1
P2CanonicalArtifactEncoderP2r0V1 = P2r0CanonicalArtifactEncoderV1
FrozenCanonicalArtifactEncodingProfileV1 = FrozenP2r0CanonicalEncodingProfileV1
P2r0EncodingProfileV1 = FrozenP2r0CanonicalEncodingProfileV1


def _expect_envelope(payload: object, name: str) -> Mapping[str, object]:
    if not isinstance(payload, Mapping) or set(payload) != {"$type", "$schema", "fields"}:
        raise P2r0IntegrityError(f"invalid {name} envelope")
    if payload.get("$type") != name or payload.get("$schema") != _P2R0_SCHEMAS[name]:
        raise P2r0IntegrityError(f"invalid {name} schema")
    fields = payload.get("fields")
    # JSON object ordering is canonicalized by sorted keys.  The profile's
    # ordered tuple governs the closed field table; wire objects are validated
    # by exact membership, never by incidental JSON key order.
    if not isinstance(fields, Mapping) or set(fields) != set(_P2R0_FIELDS[name]):
        raise P2r0IntegrityError(f"invalid {name} fields")
    return fields


def _decode_identity(payload: object) -> PlatformMessageIdentityV1:
    fields = _expect_envelope(payload, "PlatformMessageIdentityV1")
    return PlatformMessageIdentityV1(
        fields["platform_id"], fields["account_id"], fields["conversation_id"], fields["message_id"]
    )


def _decode_host(payload: object) -> HostOutputMessageIdentityFactV1:
    fields = _expect_envelope(payload, "HostOutputMessageIdentityFactV1")
    return HostOutputMessageIdentityFactV1(
        fields["schema_version"], fields["fact_id"], _decode_identity(fields["platform_message_identity"]),
        fields["operation_index"], fields["operation_kind"], fields["status"],
        fields["host_send_result_schema_version"], fields["platform_send_receipt_schema_version"],
        fields["source_event_id"], fields["trace_id"], fields["host_output_execution_record_id"],
        fields["host_output_event_ref_id"], fields["dispatch_execution_record_id"],
    )


def _decode_inbound(payload: object) -> InboundReplyReferenceFactV1:
    fields = _expect_envelope(payload, "InboundReplyReferenceFactV1")
    return InboundReplyReferenceFactV1(
        fields["schema_version"], fields["fact_id"], fields["source_event_id"],
        _decode_identity(fields["source_platform_message_identity"]),
        _decode_identity(fields["reply_target_platform_message_identity"]),
    )


def _decode_binding(payload: object) -> FactCaptureAuthorityBindingV1:
    fields = _expect_envelope(payload, "FactCaptureAuthorityBindingV1")
    return FactCaptureAuthorityBindingV1(fields["schema_version"], fields["fact_id"], fields["transaction_id"])


def _decode_archive(payload: object) -> P2rReplyLinkFactArchiveV1:
    fields = _expect_envelope(payload, "P2rReplyLinkFactArchiveV1")
    if type(fields["host_output_facts"]) not in (tuple, list) or type(fields["inbound_reply_facts"]) not in (tuple, list) or type(fields["fact_capture_authority"]) not in (tuple, list):
        raise P2r0IntegrityError("archive collections must be JSON arrays")
    return P2rReplyLinkFactArchiveV1(
        fields["schema_version"], fields["archive_id"], fields["review_run_id"], fields["episode_id"],
        fields["input_snapshot_hash"], fields["p2_run_snapshot_logical_commit_hash"], fields["p2r0_encoding_profile_hash"],
        tuple(_decode_host(item) for item in fields["host_output_facts"]),
        tuple(_decode_inbound(item) for item in fields["inbound_reply_facts"]),
        tuple(_decode_binding(item) for item in fields["fact_capture_authority"]), fields["archive_payload_hash"],
    )


class P2r0Store:
    """Authoritative P2r0 JSONL store.

    Only a committed PREPARE+COMMIT pair indexes an artifact.  Replay builds a
    complete scratch index and publishes it only after every record validates.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.root = P2R0_PERSISTENCE_ROOT
        self.profile = default_p2r0_encoding_profile()
        self.encoder = P2r0CanonicalArtifactEncoderV1(self.profile)
        self._host_facts: dict[str, HostOutputMessageIdentityFactV1] = {}
        self._inbound_facts: dict[str, InboundReplyReferenceFactV1] = {}
        self._host_identity_index: dict[PlatformMessageIdentityV1, str] = {}
        self._inbound_source_index: dict[str, str] = {}
        self._archives: dict[str, P2rReplyLinkFactArchiveV1] = {}
        self._capture_by_fact: dict[str, tuple[str, str]] = {}
        self._prepared: dict[str, Mapping[str, object]] = {}
        self._committed: dict[str, Mapping[str, object]] = {}
        self._replay()

    def _scratch(self) -> P2r0Store:
        scratch = object.__new__(type(self))
        scratch.path = self.path
        scratch.root = self.root
        scratch.profile = self.profile
        scratch.encoder = self.encoder
        scratch._host_facts = dict(self._host_facts)
        scratch._inbound_facts = dict(self._inbound_facts)
        scratch._host_identity_index = dict(self._host_identity_index)
        scratch._inbound_source_index = dict(self._inbound_source_index)
        scratch._archives = dict(self._archives)
        scratch._capture_by_fact = dict(self._capture_by_fact)
        scratch._prepared = dict(self._prepared)
        scratch._committed = dict(self._committed)
        return scratch

    def _publish(self, candidate: P2r0Store) -> None:
        self._host_facts = candidate._host_facts
        self._inbound_facts = candidate._inbound_facts
        self._host_identity_index = candidate._host_identity_index
        self._inbound_source_index = candidate._inbound_source_index
        self._archives = candidate._archives
        self._capture_by_fact = candidate._capture_by_fact
        self._prepared = candidate._prepared
        self._committed = candidate._committed

    def _prepare(self, operation: str, payload: Mapping[str, object]) -> Mapping[str, object]:
        payload_hash = CanonicalHashV1.hash(P2R0_DOMAIN_TRANSACTION_PAYLOAD, payload)
        identity = {
            "transaction_schema": P2R0_TRANSACTION_SCHEMA,
            "persistence_root": self.root,
            "operation": operation,
            "payload_hash": payload_hash,
        }
        tx_id = TX_PREFIX + CanonicalHashV1.digest(P2R0_DOMAIN_TRANSACTION_IDENTITY, identity)
        body = {
            "schema_version": P2R0_STORE_SCHEMA,
            "persistence_root": self.root,
            "record_type": P2R0_TX_PREPARE,
            "transaction_schema": P2R0_TRANSACTION_SCHEMA,
            "transaction_id": tx_id,
            "operation": operation,
            "payload": payload,
            "payload_hash": payload_hash,
        }
        return _deep_freeze({**body, "prepare_hash": CanonicalHashV1.hash(P2R0_DOMAIN_TRANSACTION_PREPARE, body)})  # type: ignore[return-value]

    def _commit(self, prepare: Mapping[str, object]) -> Mapping[str, object]:
        self._validate_prepare(prepare)
        body = {
            "schema_version": P2R0_STORE_SCHEMA,
            "persistence_root": self.root,
            "record_type": P2R0_TX_COMMIT,
            "transaction_schema": P2R0_TRANSACTION_SCHEMA,
            "transaction_id": prepare["transaction_id"],
            "prepare_hash": prepare["prepare_hash"],
        }
        return _deep_freeze({**body, "commit_hash": CanonicalHashV1.hash(P2R0_DOMAIN_TRANSACTION_COMMIT, body)})  # type: ignore[return-value]

    def _append(self, record: Mapping[str, object], stage: str) -> None:
        encoded = _json_bytes(record) + b"\n"
        with self.path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            start = handle.tell()
            try:
                written = self._write_append_bytes(handle, encoded)
                if written != len(encoded):
                    raise OSError("short P2r0 write")
                self._flush_append_handle(handle)
                self._sync_append_handle(handle)
            except Exception:
                try:
                    handle.seek(start)
                    handle.truncate(start)
                    handle.flush()
                    os.fsync(handle.fileno())
                except Exception:  # noqa: BLE001, S110 - cleanup must not mask append failure
                    pass
                raise

    @staticmethod
    def _write_append_bytes(handle: object, encoded: bytes) -> int:
        return handle.write(encoded)  # type: ignore[union-attr]

    @staticmethod
    def _flush_append_handle(handle: object) -> None:
        handle.flush()  # type: ignore[union-attr]

    @staticmethod
    def _sync_append_handle(handle: object) -> None:
        os.fsync(handle.fileno())  # type: ignore[union-attr]

    def _record_transaction(self, operation: str, payload: Mapping[str, object]) -> None:
        prepare = self._prepare(operation, payload)
        commit = self._commit(prepare)
        candidate = self._scratch()
        candidate._apply(prepare)
        candidate._apply(commit)
        self._append(prepare, P2R0_TX_PREPARE)
        try:
            self._append(commit, P2R0_TX_COMMIT)
        except Exception as exc:
            # A commit write can be indeterminate.  Do not publish memory or
            # claim success; the caller must replay to determine authority.
            if isinstance(exc, P2r0CommitOutcomeIndeterminateError):
                raise
            raise P2r0CommitOutcomeIndeterminateError("P2R0_COMMIT_OUTCOME_INDETERMINATE") from exc
        self._publish(candidate)

    def record_host_output_fact(self, fact: HostOutputMessageIdentityFactV1) -> HostOutputMessageIdentityFactV1:
        if type(fact) is not HostOutputMessageIdentityFactV1:
            raise P2r0IntegrityError("expected HostOutputMessageIdentityFactV1")
        self._record_transaction(P2R0_HOST_OUTPUT_FACT_CAPTURE, self.encoder.encode(fact))
        return fact

    capture_host_output_fact = record_host_output_fact
    record_host_fact = record_host_output_fact

    def record_inbound_reply_fact(self, fact: InboundReplyReferenceFactV1) -> InboundReplyReferenceFactV1:
        if type(fact) is not InboundReplyReferenceFactV1:
            raise P2r0IntegrityError("expected InboundReplyReferenceFactV1")
        self._record_transaction(P2R0_INBOUND_REPLY_FACT_CAPTURE, self.encoder.encode(fact))
        return fact

    capture_inbound_reply_fact = record_inbound_reply_fact
    record_inbound_fact = record_inbound_reply_fact

    def record_archive(
        self,
        archive: P2rReplyLinkFactArchiveV1,
        *,
        authoritative_p2_run: object | None = None,
    ) -> P2rReplyLinkFactArchiveV1:
        if type(archive) is not P2rReplyLinkFactArchiveV1:
            raise P2r0IntegrityError("expected P2rReplyLinkFactArchiveV1")
        if archive.p2r0_encoding_profile_hash != self.encoder.profile_hash:
            raise P2r0IntegrityError("archive references a non-authoritative P2r0 profile")
        if authoritative_p2_run is not None:
            validate_archive_p2_lineage(archive, authoritative_p2_run)
        self._record_transaction(P2R0_REPLY_LINK_FACT_ARCHIVE, self.encoder.encode(archive))
        return archive

    record_reply_link_fact_archive = record_archive

    @property
    def host_output_facts(self) -> tuple[HostOutputMessageIdentityFactV1, ...]:
        return tuple(self._host_facts[key] for key in sorted(self._host_facts))

    @property
    def inbound_reply_facts(self) -> tuple[InboundReplyReferenceFactV1, ...]:
        return tuple(self._inbound_facts[key] for key in sorted(self._inbound_facts))

    @property
    def archives(self) -> tuple[P2rReplyLinkFactArchiveV1, ...]:
        return tuple(self._archives[key] for key in sorted(self._archives))

    @property
    def prepared_transactions(self) -> tuple[Mapping[str, object], ...]:
        return tuple(self._prepared[key] for key in sorted(self._prepared))

    @property
    def committed_transactions(self) -> tuple[Mapping[str, object], ...]:
        return tuple(self._committed[key] for key in sorted(self._committed))

    def require_archive(self, archive_id: str) -> P2rReplyLinkFactArchiveV1:
        archive = self._archives.get(archive_id)
        if archive is None:
            raise P2r0IntegrityError("authoritative archive is unavailable")
        return archive

    def get_host_output_fact(self, fact_id: str) -> HostOutputMessageIdentityFactV1 | None:
        return self._host_facts.get(fact_id)

    def get_inbound_reply_fact(self, fact_id: str) -> InboundReplyReferenceFactV1 | None:
        return self._inbound_facts.get(fact_id)

    def get_archive(self, archive_id: str) -> P2rReplyLinkFactArchiveV1 | None:
        return self._archives.get(archive_id)

    def derive_storage_exact_host_reply_link(
        self,
        archive_id: str,
        inbound_reply_fact_id: str,
        host_fact_constraint: HostOutputMessageIdentityFactV1 | str | None = None,
    ) -> ExactHostReplyLinkV1:
        """Derive a non-authoritative link from P2r0 storage only.

        Callers needing an authoritative result must use
        :class:`P2r0AuthorityResolver` with a bound persisted P2a source.
        """
        return ExactHostReplyLinkV1.derive(self.require_archive(archive_id), inbound_reply_fact_id, host_fact_constraint)

    def resolve_exact_host_reply_link(self, *args: object, **kwargs: object) -> ExactHostReplyLinkV1:
        raise P2r0IntegrityError("P2r0 storage alone cannot resolve an authoritative exact reply link")

    resolve_exact_reply_link = resolve_exact_host_reply_link

    def _validate_prepare(self, record: Mapping[str, object]) -> Mapping[str, object]:
        expected = {
            "schema_version", "persistence_root", "record_type", "transaction_schema", "transaction_id",
            "operation", "payload", "payload_hash", "prepare_hash",
        }
        if set(record) != expected or record["schema_version"] != P2R0_STORE_SCHEMA or record["persistence_root"] != self.root:
            raise P2r0IntegrityError("invalid P2r0 PREPARE shape/root")
        if record["record_type"] != P2R0_TX_PREPARE or record["transaction_schema"] != P2R0_TRANSACTION_SCHEMA:
            raise P2r0IntegrityError("invalid P2r0 PREPARE schema")
        if type(record["operation"]) is not str or not isinstance(record["payload"], Mapping):
            raise P2r0IntegrityError("invalid P2r0 PREPARE operation")
        if not _check_hash_id(record["transaction_id"], TX_PREFIX):
            raise P2r0IntegrityError("invalid P2r0 transaction id")
        payload_hash = CanonicalHashV1.hash(P2R0_DOMAIN_TRANSACTION_PAYLOAD, record["payload"])
        if record["payload_hash"] != payload_hash:
            raise P2r0IntegrityError("P2r0 PREPARE payload hash mismatch")
        identity = {
            "transaction_schema": record["transaction_schema"], "persistence_root": record["persistence_root"],
            "operation": record["operation"], "payload_hash": payload_hash,
        }
        if record["transaction_id"] != TX_PREFIX + CanonicalHashV1.digest(P2R0_DOMAIN_TRANSACTION_IDENTITY, identity):
            raise P2r0IntegrityError("P2r0 PREPARE transaction identity mismatch")
        body = {key: record[key] for key in expected if key != "prepare_hash"}
        if record["prepare_hash"] != CanonicalHashV1.hash(P2R0_DOMAIN_TRANSACTION_PREPARE, body):
            raise P2r0IntegrityError("P2r0 PREPARE hash mismatch")
        if record["operation"] not in {
            P2R0_HOST_OUTPUT_FACT_CAPTURE, P2R0_INBOUND_REPLY_FACT_CAPTURE, P2R0_REPLY_LINK_FACT_ARCHIVE,
        }:
            raise P2r0IntegrityError("unknown P2r0 logical operation")
        return record

    def _validate_commit(self, record: Mapping[str, object]) -> Mapping[str, object]:
        expected = {
            "schema_version", "persistence_root", "record_type", "transaction_schema", "transaction_id",
            "prepare_hash", "commit_hash",
        }
        if set(record) != expected or record["schema_version"] != P2R0_STORE_SCHEMA or record["persistence_root"] != self.root:
            raise P2r0IntegrityError("invalid P2r0 COMMIT shape/root")
        if record["record_type"] != P2R0_TX_COMMIT or record["transaction_schema"] != P2R0_TRANSACTION_SCHEMA:
            raise P2r0IntegrityError("invalid P2r0 COMMIT schema")
        _check_hash_id(record["transaction_id"], TX_PREFIX)
        if type(record["prepare_hash"]) is not str:
            raise P2r0IntegrityError("invalid P2r0 COMMIT prepare hash")
        body = {key: record[key] for key in expected if key != "commit_hash"}
        if record["commit_hash"] != CanonicalHashV1.hash(P2R0_DOMAIN_TRANSACTION_COMMIT, body):
            raise P2r0IntegrityError("P2r0 COMMIT hash mismatch")
        return record

    def _apply(self, record: object) -> None:
        if not isinstance(record, Mapping) or type(record.get("record_type")) is not str:
            raise P2r0IntegrityError("invalid P2r0 transaction record")
        if record["record_type"] == P2R0_TX_PREPARE:
            prepare = self._validate_prepare(record)
            prior = self._prepared.get(prepare["transaction_id"])
            if prior is not None and _json_bytes(prior) != _json_bytes(prepare):
                raise P2r0IntegrityError("conflicting P2r0 PREPARE")
            self._prepared.setdefault(prepare["transaction_id"], _deep_freeze(prepare))
            return
        if record["record_type"] == P2R0_TX_COMMIT:
            commit = self._validate_commit(record)
            tx_id = commit["transaction_id"]
            previous = self._committed.get(tx_id)
            if previous is not None:
                if _json_bytes(previous) != _json_bytes(commit):
                    raise P2r0IntegrityError("conflicting P2r0 COMMIT")
                return
            prepare = self._prepared.get(tx_id)
            if prepare is None or commit["prepare_hash"] != prepare["prepare_hash"]:
                raise P2r0IntegrityError("P2r0 COMMIT has no matching PREPARE")
            self._apply_operation(prepare["operation"], prepare["payload"], tx_id)
            self._committed[tx_id] = _deep_freeze(commit)  # type: ignore[assignment]
            return
        raise P2r0IntegrityError("unknown P2r0 transaction record type")

    @staticmethod
    def _same_artifact(left: object, right: object) -> bool:
        return _json_bytes(left) == _json_bytes(right)

    def _apply_operation(self, operation: object, payload: object, tx_id: str) -> None:
        if not isinstance(payload, Mapping):
            raise P2r0IntegrityError("P2r0 operation payload must be an artifact envelope")
        if operation == P2R0_HOST_OUTPUT_FACT_CAPTURE:
            fact = _decode_host(payload)
            previous = self._host_facts.get(fact.fact_id)
            if previous is not None and not self._same_artifact(self.encoder.encode(previous), payload):
                raise P2r0IntegrityError("Host fact identity conflicts with different payload")
            identity_owner = self._host_identity_index.get(fact.platform_message_identity)
            if identity_owner is not None and identity_owner != fact.fact_id:
                raise P2r0IntegrityError("duplicate Host platform identity has conflicting facts")
            self._host_facts.setdefault(fact.fact_id, fact)
            self._host_identity_index[fact.platform_message_identity] = fact.fact_id
            prior_tx = self._capture_by_fact.get(fact.fact_id)
            if prior_tx is not None and prior_tx != (tx_id, operation):
                raise P2r0IntegrityError("Host fact has multiple capture transactions")
            self._capture_by_fact[fact.fact_id] = (tx_id, operation)
            return
        if operation == P2R0_INBOUND_REPLY_FACT_CAPTURE:
            fact = _decode_inbound(payload)
            previous = self._inbound_facts.get(fact.fact_id)
            if previous is not None and not self._same_artifact(self.encoder.encode(previous), payload):
                raise P2r0IntegrityError("Inbound fact identity conflicts with different payload")
            source_owner = self._inbound_source_index.get(fact.source_event_id)
            if source_owner is not None and source_owner != fact.fact_id:
                raise P2r0IntegrityError("duplicate inbound source event has conflicting reply targets")
            self._inbound_facts.setdefault(fact.fact_id, fact)
            self._inbound_source_index[fact.source_event_id] = fact.fact_id
            prior_tx = self._capture_by_fact.get(fact.fact_id)
            if prior_tx is not None and prior_tx != (tx_id, operation):
                raise P2r0IntegrityError("Inbound fact has multiple capture transactions")
            self._capture_by_fact[fact.fact_id] = (tx_id, operation)
            return
        if operation == P2R0_REPLY_LINK_FACT_ARCHIVE:
            archive = _decode_archive(payload)
            if archive.p2r0_encoding_profile_hash != self.encoder.profile_hash:
                raise P2r0IntegrityError("archive P2r0 profile mismatch")
            for fact_id in tuple(item.fact_id for item in archive.host_output_facts) + tuple(item.fact_id for item in archive.inbound_reply_facts):
                if fact_id not in self._capture_by_fact:
                    raise P2r0IntegrityError("archive contains an orphan or PREPARE-only fact")
            for binding in archive.fact_capture_authority:
                capture = self._capture_by_fact.get(binding.fact_id)
                if capture is None or capture[0] != binding.transaction_id:
                    raise P2r0IntegrityError("archive capture binding does not name the exact committed transaction")
            previous = self._archives.get(archive.archive_id)
            if previous is not None and not self._same_artifact(self.encoder.encode(previous), payload):
                raise P2r0IntegrityError("archive identity conflicts with different payload")
            self._archives.setdefault(archive.archive_id, archive)
            return
        raise P2r0IntegrityError("unknown P2r0 logical operation")

    def _replay(self) -> None:
        if not self.path.exists():
            return
        candidate = self._scratch()
        with self.path.open("rb") as handle:
            for line_number, raw in enumerate(handle, 1):
                if not raw or not raw.strip() or not raw.endswith(b"\n"):
                    raise P2r0IntegrityError(f"malformed/truncated P2r0 record at line {line_number}")
                try:
                    record = _strict_loads(raw[:-1].decode("utf-8", "strict"))
                except (UnicodeDecodeError, P2r0IntegrityError) as exc:
                    raise P2r0IntegrityError(f"malformed P2r0 record at line {line_number}") from exc
                if _json_bytes(record) + b"\n" != raw:
                    raise P2r0IntegrityError(f"non-canonical P2r0 record at line {line_number}")
                candidate._apply(record)
        self._publish(candidate)


# Explicit names used by callers that want to state the authority boundary.
P2r0ReplyLinkFactStore = P2r0Store
ReplyLinkAuthorityStore = P2r0Store
P2r0FactStore = P2r0Store
P2rFactStore = P2r0Store


class P2r0AuthorityResolver:
    """Resolve links only after P2r0 and persisted P2a authority agree.

    A replay-valid P2r0 archive proves only local archive integrity.  The
    production resolver therefore binds an exact, non-synthetic P2a store at
    composition time and never accepts a caller-supplied P2 wrapper as an
    authority substitute.
    """

    def __init__(self, store: P2r0Store, p2_authority: object | None = None) -> None:
        if type(store) is not P2r0Store:
            raise P2r0IntegrityError("resolver requires the authoritative P2r0Store")
        if p2_authority is not None:
            from .promotion_infrastructure import P2PromotionStore

            if type(p2_authority) is not P2PromotionStore:
                raise P2r0IntegrityError("resolver requires an authoritative production P2PromotionStore")
        self._store = store
        self._p2_authority = p2_authority

    def resolve_exact_host_reply_link(
        self,
        archive_id: str,
        inbound_reply_fact_id: str,
        host_fact_constraint: HostOutputMessageIdentityFactV1 | str | None = None,
    ) -> ExactHostReplyLinkV1:
        if self._p2_authority is None:
            raise P2r0IntegrityError("authoritative P2 source is required for exact-link resolution")
        archive = self._store.require_archive(archive_id)
        _authoritative_p2_context(archive, self._p2_authority)
        return ExactHostReplyLinkV1.derive(archive, inbound_reply_fact_id, host_fact_constraint)

    resolve_exact_reply_link = resolve_exact_host_reply_link

    def resolve_finding_host_fact(
        self,
        finding: object,
        archive_id: str,
    ) -> HostOutputMessageIdentityFactV1 | None:
        if self._p2_authority is None:
            raise P2r0IntegrityError("authoritative P2 source is required for Finding joins")
        archive = self._store.require_archive(archive_id)
        return resolve_finding_host_fact(finding, archive, p2_authority=self._p2_authority)


def derive_host_output_fact_id(fact: HostOutputMessageIdentityFactV1) -> str:
    if type(fact) is not HostOutputMessageIdentityFactV1:
        raise P2r0IntegrityError("expected HostOutputMessageIdentityFactV1")
    return _id(HOST_FACT_PREFIX, HOST_OUTPUT_FACT_DOMAIN, fact._identity_body())


def derive_inbound_reply_fact_id(fact: InboundReplyReferenceFactV1) -> str:
    if type(fact) is not InboundReplyReferenceFactV1:
        raise P2r0IntegrityError("expected InboundReplyReferenceFactV1")
    return _id(INBOUND_FACT_PREFIX, INBOUND_REPLY_FACT_DOMAIN, fact.identity_body())


__all__ = [
    "ARCHIVE_IDENTITY_DOMAIN",
    "ARCHIVE_PAYLOAD_DOMAIN",
    "ARCHIVE_PREFIX",
    "FACT_CAPTURE_BINDING_DOMAIN",
    "FACT_CAPTURE_BINDING_SCHEMA",
    "HOST_FACT_PREFIX",
    "HOST_OUTPUT_FACT_DOMAIN",
    "HOST_OUTPUT_FACT_SCHEMA",
    "INBOUND_FACT_PREFIX",
    "INBOUND_REPLY_FACT_DOMAIN",
    "INBOUND_REPLY_FACT_SCHEMA",
    "P2R0_CONTRACT_FROZEN",
    "P2R0_DOMAIN_TRANSACTION_COMMIT",
    "P2R0_DOMAIN_TRANSACTION_IDENTITY",
    "P2R0_DOMAIN_TRANSACTION_PAYLOAD",
    "P2R0_DOMAIN_TRANSACTION_PREPARE",
    "P2R0_HOST_OUTPUT_FACT_CAPTURE",
    "P2R0_INBOUND_REPLY_FACT_CAPTURE",
    "P2R0_PERSISTENCE_ROOT",
    "P2R0_PROFILE_HASH_DOMAIN",
    "P2R0_PROFILE_ID",
    "P2R0_PROFILE_SCHEMA",
    "P2R0_REPLY_LINK_FACT_ARCHIVE",
    "P2R0_STORE_SCHEMA",
    "P2R0_TRANSACTION_ROOT",
    "P2R0_TRANSACTION_SCHEMA",
    "P2R0_TX_COMMIT",
    "P2R0_TX_PREPARE",
    "REPLY_LINK_PREFIX",
    "TX_PREFIX",
    "ExactHostReplyLinkStatus",
    "ExactHostReplyLinkV1",
    "ExactReplyLinkV1",
    "FactCaptureAuthorityBindingV1",
    "FrozenCanonicalArtifactEncodingProfileV1",
    "FrozenP2r0CanonicalEncodingProfileV1",
    "HostFactStatus",
    "HostOperationKind",
    "HostOutputMessageIdentityFactV1",
    "InboundReplyReferenceFactV1",
    "P2r0AuthorityResolver",
    "P2r0CanonicalArtifactEncoderV1",
    "P2r0CanonicalEncodingProfileV1",
    "P2r0CanonicalTypeProfileV1",
    "P2r0CommitOutcomeIndeterminateError",
    "P2r0EncodingProfileV1",
    "P2r0FactStore",
    "P2r0FindingAttributionConflict",
    "P2r0HostFactStatus",
    "P2r0HostOperationKind",
    "P2r0IntegrityError",
    "P2r0ReplyLinkFactStore",
    "P2r0Store",
    "P2rFactStore",
    "P2rReplyLinkFactArchiveV1",
    "PlatformMessageIdentityV1",
    "ReplyLinkAuthorityStore",
    "canonical_artifact_encoding_profile_hash_p2r0",
    "default_p2r0_encoding_profile",
    "derive_exact_host_reply_link",
    "derive_host_output_fact_id",
    "derive_inbound_reply_fact_id",
    "p2r0_encoding_profile_hash",
    "resolve_exact_host_reply_link",
    "resolve_finding_host_fact",
    "resolve_finding_host_fact_id",
    "validate_archive_p2_lineage",
]

# A concise alias for code that uses the shorter contract name.
ExactReplyLinkV1 = ExactHostReplyLinkV1
