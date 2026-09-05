"""Capture-time inbound semantic authority (P2r.1a).

This module records only a closed, interaction-level decision about an
already-captured inbound fact.  It deliberately does not produce
ReviewEvidence or alter runtime behaviour.  Production construction has no
evaluator, so the normal runtime records no semantic authority at all.

The wire format is explicit and append-only.  Raw inbound content exists only
in the short-lived evaluator input and is never written to the authority log.
"""

from __future__ import annotations

import base64
import hashlib
import inspect
import json
import math
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

from .contracts import ResolvedEvent
from .promotion_infrastructure import CanonicalHashV1
from .reply_link_authority import InboundReplyReferenceFactV1, PlatformMessageIdentityV1


class InboundSemanticAuthorityIntegrityError(ValueError):
    """Invalid semantic authority, profile, or persisted history."""


# Both spellings are kept as small compatibility aliases for callers that
# name the artifact (rather than the authority layer) in their error import.
InboundSemanticActAuthorityIntegrityError = InboundSemanticAuthorityIntegrityError


class InboundSemanticDecision(str, Enum):
    MATCH = "MATCH"
    NO_MATCH = "NO_MATCH"
    ABSTAIN = "ABSTAIN"


class InboundSemanticKind(str, Enum):
    EXPLICIT_CORRECTION = "EXPLICIT_CORRECTION"


INBOUND_SEMANTIC_AUTHORITY_SCHEMA = "p2r1a.inbound-semantic-act-authority.v1"
INBOUND_SEMANTIC_PROFILE_SCHEMA = "p2r1a.inbound-semantic-evaluator-profile.v1"
INBOUND_SEMANTIC_STORE_SCHEMA = "p2r1a.inbound-semantic-authority-store.v1"
INBOUND_SEMANTIC_CONTENT_ENCODING = "UTF-8"
AUTHORITY_PREFIX = "authority:p2r1a:"
PROFILE_PREFIX = "profile:p2r1a:"
PROFILE_HASH_DOMAIN = "p2r1a:inbound-semantic-evaluator-profile:v1"
INPUT_HASH_DOMAIN = "p2r1a:inbound-semantic-evaluator-input:v1"
AUTHORITY_ID_DOMAIN = "p2r1a:inbound-semantic-act-authority-identity:v1"
AUTHORITY_PAYLOAD_DOMAIN = "p2r1a:inbound-semantic-act-authority-payload:v1"
STORE_PROFILE_RECORD = "PROFILE"
STORE_AUTHORITY_RECORD = "AUTHORITY"
_HASH_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ID_RE = re.compile(r"[0-9a-f]{64}\Z")
_DECISION_VALUES = tuple(item.value for item in InboundSemanticDecision)
# Explicit production boundary: this phase has no configured evaluator.
PRODUCTION_SEMANTIC_EVALUATOR = None
PRODUCTION_REVIEW_EVIDENCE_ENABLED = False
P2A_V1_PRODUCTION_PROMOTABLE_RULES = frozenset()
PRODUCTION_VALIDATED_EVIDENCE_COUNT = 0


def _text(value: object, field_name: str) -> str:
    if type(value) is not str or not value:
        raise InboundSemanticAuthorityIntegrityError(f"{field_name} must be a non-empty string")
    return value


def _hash(value: object, field_name: str) -> str:
    value = _text(value, field_name)
    if _HASH_RE.fullmatch(value) is None:
        raise InboundSemanticAuthorityIntegrityError(f"{field_name} is not a SHA-256 hash")
    return value


def _deep_freeze(value: object) -> object:
    """Detach a JSON-shaped value into immutable containers."""
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise InboundSemanticAuthorityIntegrityError("non-finite value is not canonical")
        return value
    if type(value) in (tuple, list):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise InboundSemanticAuthorityIntegrityError("mapping keys must be strings")
            frozen[key] = _deep_freeze(item)
        return MappingProxyType(frozen)
    raise InboundSemanticAuthorityIntegrityError(
        f"unsupported mutable value: {type(value).__name__}"
    )


def _plain(value: object) -> object:
    """Convert our immutable values to the only permitted JSON values."""
    if value is None or type(value) in (bool, int, str, float):
        if type(value) is float and not math.isfinite(value):
            raise InboundSemanticAuthorityIntegrityError("non-finite value is not canonical")
        return value
    if isinstance(value, Enum):
        return value.value
    if type(value) in (tuple, list):
        return [_plain(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _plain(value[key]) for key in sorted(value)}
    raise InboundSemanticAuthorityIntegrityError(
        f"unsupported canonical value: {type(value).__name__}"
    )


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        _plain(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _strict_loads(raw: bytes) -> object:
    def reject_constant(value: str) -> object:
        raise InboundSemanticAuthorityIntegrityError(f"non-finite JSON constant: {value}")

    def reject_duplicate(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise InboundSemanticAuthorityIntegrityError("duplicate JSON key")
            result[key] = value
        return result

    try:
        return json.loads(
            raw.decode("utf-8", "strict"),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicate,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InboundSemanticAuthorityIntegrityError("malformed authority JSON") from exc


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def canonical_content_payload_hash(content: str) -> str:
    """Hash the exact UTF-8 bytes of one resolved-event content string."""
    if type(content) is not str:
        raise InboundSemanticAuthorityIntegrityError("content must be a string")
    return "sha256:" + hashlib.sha256(content.encode("utf-8", "strict")).hexdigest()


def _expect_keys(value: object, expected: set[str], label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise InboundSemanticAuthorityIntegrityError(f"invalid {label} field set")
    return value


def _identity_payload(identity: PlatformMessageIdentityV1) -> Mapping[str, str]:
    if type(identity) is not PlatformMessageIdentityV1:
        raise InboundSemanticAuthorityIntegrityError("invalid platform message identity")
    return identity.canonical_body()


@dataclass(frozen=True, slots=True)
class InboundSemanticEvaluatorProfileV1:
    """Content-addressed evaluator configuration, never a mutable registry."""

    profile_id: str
    profile_version: str
    evaluator_kind: str
    provider: str = ""
    model: str = ""
    deployment: str = ""
    model_version: str = ""
    prompt_template_hash: str = ""
    input_schema_version: str = INBOUND_SEMANTIC_AUTHORITY_SCHEMA
    output_schema_version: str = INBOUND_SEMANTIC_AUTHORITY_SCHEMA
    allowed_decisions: tuple[str, ...] = _DECISION_VALUES
    canonical_parsing_rules: Mapping[str, object] = field(default_factory=dict)
    decoding_parameters: Mapping[str, object] = field(default_factory=dict)
    schema_version: str = INBOUND_SEMANTIC_PROFILE_SCHEMA
    profile_payload_hash: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != INBOUND_SEMANTIC_PROFILE_SCHEMA:
            raise InboundSemanticAuthorityIntegrityError("unknown evaluator profile schema")
        for name in ("profile_id", "profile_version", "evaluator_kind"):
            _text(getattr(self, name), name)
        object.__setattr__(self, "allowed_decisions", tuple(self.allowed_decisions))
        decisions = self.allowed_decisions
        if any(type(decision) is not str for decision in decisions):
            raise InboundSemanticAuthorityIntegrityError("profile decision must be a string")
        if len(decisions) != len(set(decisions)) or set(decisions) != set(_DECISION_VALUES):
            raise InboundSemanticAuthorityIntegrityError("profile decision set is not the frozen V1 set")
        for name in (
            "provider", "model", "deployment", "model_version", "prompt_template_hash",
            "input_schema_version", "output_schema_version",
        ):
            value = getattr(self, name)
            if type(value) is not str:
                raise InboundSemanticAuthorityIntegrityError(f"{name} must be a string")
        if not isinstance(self.canonical_parsing_rules, Mapping):
            raise InboundSemanticAuthorityIntegrityError("canonical parsing rules must be a mapping")
        if not isinstance(self.decoding_parameters, Mapping):
            raise InboundSemanticAuthorityIntegrityError("decoding parameters must be a mapping")
        object.__setattr__(self, "canonical_parsing_rules", _deep_freeze(self.canonical_parsing_rules))
        object.__setattr__(self, "decoding_parameters", _deep_freeze(self.decoding_parameters))
        expected = CanonicalHashV1.hash(PROFILE_HASH_DOMAIN, self._payload_without_hash())
        if self.profile_payload_hash:
            if self.profile_payload_hash != expected:
                raise InboundSemanticAuthorityIntegrityError("evaluator profile hash mismatch")
        else:
            object.__setattr__(self, "profile_payload_hash", expected)

    def _payload_without_hash(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "evaluator_kind": self.evaluator_kind,
            "provider": self.provider,
            "model": self.model,
            "deployment": self.deployment,
            "model_version": self.model_version,
            "prompt_template_hash": self.prompt_template_hash,
            "input_schema_version": self.input_schema_version,
            "output_schema_version": self.output_schema_version,
            "allowed_decisions": self.allowed_decisions,
            "canonical_parsing_rules": self.canonical_parsing_rules,
            "decoding_parameters": self.decoding_parameters,
        })

    def canonical_payload(self) -> Mapping[str, object]:
        return MappingProxyType({**dict(self._payload_without_hash()), "profile_payload_hash": self.profile_payload_hash})

    @classmethod
    def create(cls, **kwargs: object) -> InboundSemanticEvaluatorProfileV1:
        return cls(**kwargs)  # type: ignore[arg-type]

    from_fields = create


@dataclass(frozen=True, slots=True)
class InboundSemanticEvaluationInputV1:
    """Detached evaluator input; content is transient and never persisted."""

    semantic_kind: InboundSemanticKind
    source_event_id: str
    source_platform_message_identity: PlatformMessageIdentityV1
    inbound_reply_fact_id: str
    content: str
    content_encoding: str
    content_payload_hash: str
    evaluator_profile_id: str
    evaluator_profile_hash: str
    content_bytes: bytes = field(init=False, repr=False)
    evaluator_input_payload_hash: str = field(init=False)

    def __post_init__(self) -> None:
        try:
            kind = self.semantic_kind if isinstance(self.semantic_kind, InboundSemanticKind) else InboundSemanticKind(self.semantic_kind)
        except (TypeError, ValueError) as exc:
            raise InboundSemanticAuthorityIntegrityError("unsupported semantic kind") from exc
        if kind is not InboundSemanticKind.EXPLICIT_CORRECTION:
            raise InboundSemanticAuthorityIntegrityError("unsupported semantic kind")
        object.__setattr__(self, "semantic_kind", kind)
        _text(self.source_event_id, "source_event_id")
        _text(self.inbound_reply_fact_id, "inbound_reply_fact_id")
        if type(self.source_platform_message_identity) is not PlatformMessageIdentityV1:
            raise InboundSemanticAuthorityIntegrityError("invalid source platform identity")
        if self.content_encoding != INBOUND_SEMANTIC_CONTENT_ENCODING:
            raise InboundSemanticAuthorityIntegrityError("unsupported content encoding")
        if type(self.content) is not str:
            raise InboundSemanticAuthorityIntegrityError("content must be the exact ResolvedEvent string")
        content_bytes = self.content.encode("utf-8", "strict")
        content_hash = canonical_content_payload_hash(self.content)
        if self.content_payload_hash != content_hash:
            raise InboundSemanticAuthorityIntegrityError("content payload hash mismatch")
        _hash(self.evaluator_profile_hash, "evaluator_profile_hash")
        _text(self.evaluator_profile_id, "evaluator_profile_id")
        object.__setattr__(self, "content_bytes", content_bytes)
        expected_input = CanonicalHashV1.hash(INPUT_HASH_DOMAIN, self.canonical_payload_without_hash())
        object.__setattr__(self, "evaluator_input_payload_hash", expected_input)

    def canonical_payload_without_hash(self) -> Mapping[str, object]:
        return MappingProxyType({
            "semantic_kind": self.semantic_kind.value,
            "source_event_id": self.source_event_id,
            "source_platform_message_identity": _identity_payload(self.source_platform_message_identity),
            "inbound_reply_fact_id": self.inbound_reply_fact_id,
            "content_encoding": self.content_encoding,
            "content_payload_hash": self.content_payload_hash,
            "content_bytes_base64url": _b64(self.content_bytes),
            "evaluator_profile_id": self.evaluator_profile_id,
            "evaluator_profile_hash": self.evaluator_profile_hash,
        })

    def canonical_payload(self) -> Mapping[str, object]:
        return MappingProxyType({
            **dict(self.canonical_payload_without_hash()),
            "evaluator_input_payload_hash": self.evaluator_input_payload_hash,
        })


class InboundSemanticEvaluatorV1(Protocol):
    def evaluate(self, input: InboundSemanticEvaluationInputV1) -> object: ...


@dataclass(frozen=True, slots=True)
class InboundSemanticEvaluationResultV1:
    """Closed evaluator output; no free-form model text is accepted."""

    decision: InboundSemanticDecision

    def __post_init__(self) -> None:
        try:
            decision = self.decision if isinstance(self.decision, InboundSemanticDecision) else InboundSemanticDecision(self.decision)
        except (TypeError, ValueError) as exc:
            raise InboundSemanticAuthorityIntegrityError("unknown evaluator decision") from exc
        object.__setattr__(self, "decision", decision)


@dataclass(frozen=True, slots=True)
class InboundSemanticActAuthorityV1:
    schema_version: str
    authority_id: str
    source_event_id: str
    source_platform_message_identity: PlatformMessageIdentityV1
    inbound_reply_fact_id: str
    content_encoding: str
    content_payload_hash: str
    semantic_kind: InboundSemanticKind
    decision: InboundSemanticDecision
    evaluator_profile_id: str
    evaluator_profile_hash: str
    evaluator_input_payload_hash: str
    producer: str
    provenance: tuple[str, ...]
    authority_payload_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != INBOUND_SEMANTIC_AUTHORITY_SCHEMA:
            raise InboundSemanticAuthorityIntegrityError("unknown semantic authority schema")
        for name in ("source_event_id", "inbound_reply_fact_id", "evaluator_profile_id", "producer"):
            _text(getattr(self, name), name)
        if type(self.source_platform_message_identity) is not PlatformMessageIdentityV1:
            raise InboundSemanticAuthorityIntegrityError("invalid source platform identity")
        if self.content_encoding != INBOUND_SEMANTIC_CONTENT_ENCODING:
            raise InboundSemanticAuthorityIntegrityError("unsupported content encoding")
        _hash(self.content_payload_hash, "content_payload_hash")
        _hash(self.evaluator_profile_hash, "evaluator_profile_hash")
        _hash(self.evaluator_input_payload_hash, "evaluator_input_payload_hash")
        try:
            kind = self.semantic_kind if isinstance(self.semantic_kind, InboundSemanticKind) else InboundSemanticKind(self.semantic_kind)
            decision = self.decision if isinstance(self.decision, InboundSemanticDecision) else InboundSemanticDecision(self.decision)
        except (TypeError, ValueError) as exc:
            raise InboundSemanticAuthorityIntegrityError("unknown semantic enum") from exc
        if kind is not InboundSemanticKind.EXPLICIT_CORRECTION:
            raise InboundSemanticAuthorityIntegrityError("unsupported semantic kind")
        object.__setattr__(self, "semantic_kind", kind)
        object.__setattr__(self, "decision", decision)
        object.__setattr__(self, "provenance", tuple(self.provenance))
        if not self.provenance or any(type(item) is not str or not item for item in self.provenance):
            raise InboundSemanticAuthorityIntegrityError("invalid provenance")
        if self.authority_id != self.derive_authority_id(self.identity_body()):
            raise InboundSemanticAuthorityIntegrityError("authority id is not derived from factual identity")
        expected_payload = CanonicalHashV1.hash(AUTHORITY_PAYLOAD_DOMAIN, self.payload_without_hash())
        if self.authority_payload_hash != expected_payload:
            raise InboundSemanticAuthorityIntegrityError("authority payload hash mismatch")

    @staticmethod
    def derive_authority_id(identity: Mapping[str, object]) -> str:
        return AUTHORITY_PREFIX + CanonicalHashV1.digest(AUTHORITY_ID_DOMAIN, identity)

    def identity_body(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version,
            "source_event_id": self.source_event_id,
            "source_platform_message_identity": _identity_payload(self.source_platform_message_identity),
            "inbound_reply_fact_id": self.inbound_reply_fact_id,
            "content_encoding": self.content_encoding,
            "content_payload_hash": self.content_payload_hash,
            "semantic_kind": self.semantic_kind.value,
            "evaluator_profile_hash": self.evaluator_profile_hash,
            "evaluator_input_payload_hash": self.evaluator_input_payload_hash,
        })

    def payload_without_hash(self) -> Mapping[str, object]:
        return MappingProxyType({
            **dict(self.identity_body()),
            "decision": self.decision.value,
            "evaluator_profile_id": self.evaluator_profile_id,
            "producer": self.producer,
            "provenance": self.provenance,
        })

    def canonical_payload(self) -> Mapping[str, object]:
        return MappingProxyType({
            **dict(self.payload_without_hash()),
            "authority_id": self.authority_id,
            "authority_payload_hash": self.authority_payload_hash,
        })

    @classmethod
    def create(
        cls,
        *,
        source_event_id: str,
        source_platform_message_identity: PlatformMessageIdentityV1,
        inbound_reply_fact_id: str,
        content_payload_hash: str,
        semantic_kind: InboundSemanticKind,
        decision: InboundSemanticDecision,
        evaluator_profile_id: str,
        evaluator_profile_hash: str,
        evaluator_input_payload_hash: str,
        producer: str,
        provenance: Sequence[str],
    ) -> InboundSemanticActAuthorityV1:
        identity = {
            "schema_version": INBOUND_SEMANTIC_AUTHORITY_SCHEMA,
            "source_event_id": source_event_id,
            "source_platform_message_identity": _identity_payload(source_platform_message_identity),
            "inbound_reply_fact_id": inbound_reply_fact_id,
            "content_encoding": INBOUND_SEMANTIC_CONTENT_ENCODING,
            "content_payload_hash": content_payload_hash,
            "semantic_kind": semantic_kind.value if isinstance(semantic_kind, Enum) else semantic_kind,
            "evaluator_profile_hash": evaluator_profile_hash,
            "evaluator_input_payload_hash": evaluator_input_payload_hash,
        }
        values = {
            "schema_version": INBOUND_SEMANTIC_AUTHORITY_SCHEMA,
            "source_event_id": source_event_id,
            "source_platform_message_identity": source_platform_message_identity,
            "inbound_reply_fact_id": inbound_reply_fact_id,
            "content_encoding": INBOUND_SEMANTIC_CONTENT_ENCODING,
            "content_payload_hash": content_payload_hash,
            "semantic_kind": semantic_kind,
            "decision": decision,
            "evaluator_profile_id": evaluator_profile_id,
            "evaluator_profile_hash": evaluator_profile_hash,
            "evaluator_input_payload_hash": evaluator_input_payload_hash,
            "producer": producer,
            "provenance": tuple(provenance),
        }
        payload = {
            **identity,
            "decision": decision.value if isinstance(decision, Enum) else decision,
            "evaluator_profile_id": evaluator_profile_id,
            "producer": producer,
            "provenance": tuple(provenance),
        }
        payload_hash = CanonicalHashV1.hash(AUTHORITY_PAYLOAD_DOMAIN, payload)
        return cls(
            authority_id=cls.derive_authority_id(identity),
            authority_payload_hash=payload_hash,
            **values,
        )

    from_evaluation = create


def _decode_profile(payload: object) -> InboundSemanticEvaluatorProfileV1:
    data = _expect_keys(payload, {
        "schema_version", "profile_id", "profile_version", "evaluator_kind", "provider", "model",
        "deployment", "model_version", "prompt_template_hash", "input_schema_version",
        "output_schema_version", "allowed_decisions", "canonical_parsing_rules",
        "decoding_parameters", "profile_payload_hash",
    }, "profile")
    if type(data["allowed_decisions"]) is not list:
        raise InboundSemanticAuthorityIntegrityError("profile decisions must be an array")
    return InboundSemanticEvaluatorProfileV1(**dict(data))  # type: ignore[arg-type]


def _decode_authority(payload: object) -> InboundSemanticActAuthorityV1:
    data = _expect_keys(payload, {
        "schema_version", "authority_id", "source_event_id", "source_platform_message_identity",
        "inbound_reply_fact_id", "content_encoding", "content_payload_hash", "semantic_kind",
        "decision", "evaluator_profile_id", "evaluator_profile_hash", "evaluator_input_payload_hash",
        "producer", "provenance", "authority_payload_hash",
    }, "authority")
    identity_data = _expect_keys(data["source_platform_message_identity"], {
        "platform_id", "account_id", "conversation_id", "message_id",
    }, "platform identity")
    if type(data["provenance"]) is not list:
        raise InboundSemanticAuthorityIntegrityError("authority provenance must be an array")
    return InboundSemanticActAuthorityV1(
        **{**dict(data), "source_platform_message_identity": PlatformMessageIdentityV1(**dict(identity_data))}
    )  # type: ignore[arg-type]


class InboundSemanticActAuthorityStoreV1:
    """Small durable authority store with replay-before-publish semantics."""

    owner = "P2r.1a Inbound Semantic Authority"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._profiles: dict[str, InboundSemanticEvaluatorProfileV1] = {}
        self._profile_ids: dict[tuple[str, str], str] = {}
        self._authorities: dict[str, InboundSemanticActAuthorityV1] = {}
        self._replay()

    @property
    def profiles(self) -> tuple[InboundSemanticEvaluatorProfileV1, ...]:
        return tuple(self._profiles[key] for key in sorted(self._profiles))

    @property
    def authorities(self) -> tuple[InboundSemanticActAuthorityV1, ...]:
        return tuple(self._authorities[key] for key in sorted(self._authorities))

    def get_profile(self, profile_hash: str) -> InboundSemanticEvaluatorProfileV1 | None:
        return self._profiles.get(profile_hash)

    def get_authority(self, authority_id: str) -> InboundSemanticActAuthorityV1 | None:
        return self._authorities.get(authority_id)

    get = get_authority

    def record_profile(self, profile: InboundSemanticEvaluatorProfileV1) -> InboundSemanticEvaluatorProfileV1:
        if type(profile) is not InboundSemanticEvaluatorProfileV1:
            raise InboundSemanticAuthorityIntegrityError("expected evaluator profile")
        existing = self._profiles.get(profile.profile_payload_hash)
        if existing is not None:
            if _json_bytes(existing.canonical_payload()) != _json_bytes(profile.canonical_payload()):
                raise InboundSemanticAuthorityIntegrityError("conflicting profile payload")
            return existing
        profile_key = (profile.profile_id, profile.profile_version)
        prior_hash = self._profile_ids.get(profile_key)
        if prior_hash is not None and prior_hash != profile.profile_payload_hash:
            raise InboundSemanticAuthorityIntegrityError("profile identity has conflicting content")
        record = self._record_envelope(STORE_PROFILE_RECORD, profile.canonical_payload())
        self._persist(record)
        self._profiles[profile.profile_payload_hash] = profile
        self._profile_ids[profile_key] = profile.profile_payload_hash
        return profile

    def record_authority(
        self,
        authority: InboundSemanticActAuthorityV1,
        *,
        profile: InboundSemanticEvaluatorProfileV1 | None = None,
    ) -> InboundSemanticActAuthorityV1:
        if type(authority) is not InboundSemanticActAuthorityV1:
            raise InboundSemanticAuthorityIntegrityError("expected semantic authority")
        known = self._profiles.get(authority.evaluator_profile_hash)
        if known is None or (profile is not None and known.profile_payload_hash != profile.profile_payload_hash):
            raise InboundSemanticAuthorityIntegrityError("authority references an unknown profile hash")
        if known.profile_id != authority.evaluator_profile_id:
            raise InboundSemanticAuthorityIntegrityError("authority profile identity mismatch")
        existing = self._authorities.get(authority.authority_id)
        if existing is not None:
            if _json_bytes(existing.canonical_payload()) != _json_bytes(authority.canonical_payload()):
                raise InboundSemanticAuthorityIntegrityError("conflicting authority identity")
            return existing
        record = self._record_envelope(STORE_AUTHORITY_RECORD, authority.canonical_payload())
        self._persist(record)
        self._authorities[authority.authority_id] = authority
        return authority

    record = record_authority
    append = record_authority
    record_inbound_semantic_authority = record_authority

    @staticmethod
    def _record_envelope(record_type: str, payload: Mapping[str, object]) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": INBOUND_SEMANTIC_STORE_SCHEMA,
            "record_type": record_type,
            "payload": payload,
        })

    def _persist(self, record: Mapping[str, object]) -> None:
        try:
            self._append(record)
        except InboundSemanticAuthorityIntegrityError:
            raise
        except Exception as exc:
            raise InboundSemanticAuthorityIntegrityError("semantic authority append failed") from exc

    def _append(self, record: Mapping[str, object]) -> None:
        encoded = _json_bytes(record) + b"\n"
        previous_size = self.path.stat().st_size if self.path.exists() else 0
        try:
            with self.path.open("ab") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception as exc:
            # A failed durability step must not leave an accepted operation in
            # memory.  Roll back a partial tail when the filesystem permits it;
            # otherwise the next replay fails closed on the non-durable tail.
            try:
                with self.path.open("r+b") as handle:
                    handle.truncate(previous_size)
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError as rollback_error:
                raise InboundSemanticAuthorityIntegrityError(
                    "semantic authority append rollback failed"
                ) from rollback_error
            raise InboundSemanticAuthorityIntegrityError("semantic authority append failed") from exc

    def _replay(self) -> None:
        if not self.path.exists():
            return
        profiles: dict[str, InboundSemanticEvaluatorProfileV1] = {}
        profile_ids: dict[tuple[str, str], str] = {}
        authorities: dict[str, InboundSemanticActAuthorityV1] = {}
        with self.path.open("rb") as handle:
            for line_number, raw in enumerate(handle, 1):
                if not raw or not raw.endswith(b"\n") or not raw[:-1].strip():
                    raise InboundSemanticAuthorityIntegrityError(f"malformed authority record at line {line_number}")
                record = _strict_loads(raw[:-1])
                if _json_bytes(record) + b"\n" != raw:
                    raise InboundSemanticAuthorityIntegrityError(f"non-canonical authority record at line {line_number}")
                data = _expect_keys(record, {"schema_version", "record_type", "payload"}, "store record")
                if data["schema_version"] != INBOUND_SEMANTIC_STORE_SCHEMA:
                    raise InboundSemanticAuthorityIntegrityError("unknown authority store schema")
                if data["record_type"] == STORE_PROFILE_RECORD:
                    profile = _decode_profile(data["payload"])
                    prior = profiles.get(profile.profile_payload_hash)
                    if prior is not None and _json_bytes(prior.canonical_payload()) != _json_bytes(profile.canonical_payload()):
                        raise InboundSemanticAuthorityIntegrityError("conflicting profile replay")
                    key = (profile.profile_id, profile.profile_version)
                    prior_hash = profile_ids.get(key)
                    if prior_hash is not None and prior_hash != profile.profile_payload_hash:
                        raise InboundSemanticAuthorityIntegrityError("conflicting profile identity replay")
                    profiles[profile.profile_payload_hash] = profile
                    profile_ids[key] = profile.profile_payload_hash
                elif data["record_type"] == STORE_AUTHORITY_RECORD:
                    authority = _decode_authority(data["payload"])
                    profile = profiles.get(authority.evaluator_profile_hash)
                    if profile is None or profile.profile_id != authority.evaluator_profile_id:
                        raise InboundSemanticAuthorityIntegrityError("authority replay has no exact profile")
                    prior = authorities.get(authority.authority_id)
                    if prior is not None and _json_bytes(prior.canonical_payload()) != _json_bytes(authority.canonical_payload()):
                        raise InboundSemanticAuthorityIntegrityError("conflicting authority replay")
                    authorities[authority.authority_id] = authority
                else:
                    raise InboundSemanticAuthorityIntegrityError("unknown authority record type")
        self._profiles = profiles
        self._profile_ids = profile_ids
        self._authorities = authorities


class InboundSemanticActAuthorityServiceV1:
    """Evaluate after a committed inbound fact, using one detached event."""

    owner = "P2r.1a Capture-Time Semantic Authority"

    def __init__(
        self,
        store: InboundSemanticActAuthorityStoreV1,
        *,
        profile: InboundSemanticEvaluatorProfileV1 | None = None,
        evaluator: InboundSemanticEvaluatorV1 | None = None,
    ) -> None:
        if type(store) is not InboundSemanticActAuthorityStoreV1:
            raise TypeError("semantic service requires InboundSemanticActAuthorityStoreV1")
        if evaluator is not None and not callable(getattr(evaluator, "evaluate", None)) and not callable(evaluator):
            raise TypeError("evaluator must expose evaluate(input)")
        self.store = store
        self.profile = profile
        self.evaluator = evaluator
        if profile is not None:
            self.store.record_profile(profile)

    def evaluate_after_inbound_commit(
        self,
        resolved_event: ResolvedEvent,
        inbound_fact: InboundReplyReferenceFactV1,
        source_platform_message_identity: PlatformMessageIdentityV1,
    ) -> InboundSemanticActAuthorityV1 | None:
        """Return an authority only when an explicitly supplied evaluator exists."""
        if self.evaluator is None or self.profile is None:
            return None
        if type(resolved_event) is not ResolvedEvent:
            raise InboundSemanticAuthorityIntegrityError("semantic input requires the attached ResolvedEvent")
        if type(inbound_fact) is not InboundReplyReferenceFactV1:
            raise InboundSemanticAuthorityIntegrityError("semantic input requires the committed inbound fact")
        if type(source_platform_message_identity) is not PlatformMessageIdentityV1:
            raise InboundSemanticAuthorityIntegrityError("semantic input requires source platform identity")
        if resolved_event.event_id != inbound_fact.source_event_id:
            raise InboundSemanticAuthorityIntegrityError("event and inbound fact source IDs differ")
        if source_platform_message_identity != inbound_fact.source_platform_message_identity:
            raise InboundSemanticAuthorityIntegrityError("event and inbound fact platform identities differ")
        evaluator_input = self.prepare_evaluator_input(
            resolved_event=resolved_event,
            inbound_fact=inbound_fact,
            source_platform_message_identity=source_platform_message_identity,
        )
        try:
            result = self.evaluator.evaluate(evaluator_input) if callable(getattr(self.evaluator, "evaluate", None)) else self.evaluator(evaluator_input)  # type: ignore[misc]
        except Exception as exc:
            raise InboundSemanticAuthorityIntegrityError("semantic evaluator failed") from exc
        decision = self._decode_decision(result)
        authority = InboundSemanticActAuthorityV1.create(
            source_event_id=evaluator_input.source_event_id,
            source_platform_message_identity=evaluator_input.source_platform_message_identity,
            inbound_reply_fact_id=evaluator_input.inbound_reply_fact_id,
            content_payload_hash=evaluator_input.content_payload_hash,
            semantic_kind=evaluator_input.semantic_kind,
            decision=decision,
            evaluator_profile_id=self.profile.profile_id,
            evaluator_profile_hash=self.profile.profile_payload_hash,
            evaluator_input_payload_hash=evaluator_input.evaluator_input_payload_hash,
            producer=self.profile.evaluator_kind,
            provenance=("inbound_fact_committed", "capture_time_evaluation"),
        )
        return self.store.record_authority(authority, profile=self.profile)

    def prepare_evaluator_input(
        self,
        *,
        resolved_event: ResolvedEvent,
        inbound_fact: InboundReplyReferenceFactV1,
        source_platform_message_identity: PlatformMessageIdentityV1,
    ) -> InboundSemanticEvaluationInputV1:
        """Build one detached input after validating the committed fact chain.

        This is shared by the synchronous compatibility path and the bounded
        production worker.  It deliberately returns only the exact immutable
        evaluator input; no caller-provided decision is accepted here.
        """
        if self.evaluator is None or self.profile is None:
            raise InboundSemanticAuthorityIntegrityError("semantic evaluator is unavailable")
        if type(resolved_event) is not ResolvedEvent:
            raise InboundSemanticAuthorityIntegrityError("semantic input requires the attached ResolvedEvent")
        if type(inbound_fact) is not InboundReplyReferenceFactV1:
            raise InboundSemanticAuthorityIntegrityError("semantic input requires the committed inbound fact")
        if type(source_platform_message_identity) is not PlatformMessageIdentityV1:
            raise InboundSemanticAuthorityIntegrityError("semantic input requires source platform identity")
        if resolved_event.event_id != inbound_fact.source_event_id:
            raise InboundSemanticAuthorityIntegrityError("event and inbound fact source IDs differ")
        if source_platform_message_identity != inbound_fact.source_platform_message_identity:
            raise InboundSemanticAuthorityIntegrityError("event and inbound fact platform identities differ")
        content = resolved_event.content
        if type(content) is not str:
            raise InboundSemanticAuthorityIntegrityError("ResolvedEvent content is not an exact string")
        return InboundSemanticEvaluationInputV1(
            semantic_kind=InboundSemanticKind.EXPLICIT_CORRECTION,
            source_event_id=resolved_event.event_id,
            source_platform_message_identity=source_platform_message_identity,
            inbound_reply_fact_id=inbound_fact.fact_id,
            content=content,
            content_encoding=INBOUND_SEMANTIC_CONTENT_ENCODING,
            content_payload_hash=canonical_content_payload_hash(content),
            evaluator_profile_id=self.profile.profile_id,
            evaluator_profile_hash=self.profile.profile_payload_hash,
        )

    async def evaluate_detached_input(
        self,
        evaluator_input: InboundSemanticEvaluationInputV1,
    ) -> InboundSemanticActAuthorityV1:
        """Evaluate one exact detached input through the bound evaluator.

        Callers provide only the immutable input.  The decision is obtained
        here from the evaluator bound at service construction and is never
        accepted as a caller-supplied persistence argument.
        """
        if type(evaluator_input) is not InboundSemanticEvaluationInputV1:
            raise InboundSemanticAuthorityIntegrityError("expected semantic evaluation input")
        if self.evaluator is None or self.profile is None:
            raise InboundSemanticAuthorityIntegrityError("semantic evaluator is unavailable")
        if evaluator_input.evaluator_profile_id != self.profile.profile_id:
            raise InboundSemanticAuthorityIntegrityError("semantic input profile identity mismatch")
        if evaluator_input.evaluator_profile_hash != self.profile.profile_payload_hash:
            raise InboundSemanticAuthorityIntegrityError("semantic input profile hash mismatch")
        evaluate = getattr(self.evaluator, "evaluate", None)
        evaluate = evaluate if callable(evaluate) else self.evaluator
        if not callable(evaluate):
            raise InboundSemanticAuthorityIntegrityError("semantic evaluator is unavailable")
        try:
            result = evaluate(evaluator_input)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            raise InboundSemanticAuthorityIntegrityError("semantic evaluator failed") from exc
        decision = self._decode_decision(result)
        authority = InboundSemanticActAuthorityV1.create(
            source_event_id=evaluator_input.source_event_id,
            source_platform_message_identity=evaluator_input.source_platform_message_identity,
            inbound_reply_fact_id=evaluator_input.inbound_reply_fact_id,
            content_payload_hash=evaluator_input.content_payload_hash,
            semantic_kind=evaluator_input.semantic_kind,
            decision=decision,
            evaluator_profile_id=self.profile.profile_id,
            evaluator_profile_hash=self.profile.profile_payload_hash,
            evaluator_input_payload_hash=evaluator_input.evaluator_input_payload_hash,
            producer=self.profile.evaluator_kind,
            provenance=("inbound_fact_committed", "capture_time_evaluation"),
        )
        return self.store.record_authority(authority, profile=self.profile)

    def record_detached_evaluation(
        self,
        evaluator_input: InboundSemanticEvaluationInputV1,
        result: object,
    ) -> None:
        """Deprecated compatibility surface; direct decision injection is disabled."""
        del evaluator_input, result
        raise InboundSemanticAuthorityIntegrityError(
            "direct detached decision persistence is disabled"
        )

    @staticmethod
    def _decode_decision(result: object) -> InboundSemanticDecision:
        if isinstance(result, InboundSemanticDecision):
            return result
        if isinstance(result, InboundSemanticEvaluationResultV1):
            return result.decision
        if not isinstance(result, Mapping) or set(result) != {"decision"} or type(result["decision"]) is not str:
            raise InboundSemanticAuthorityIntegrityError("evaluator output must be exactly {decision}")
        try:
            return InboundSemanticDecision(result["decision"])
        except ValueError as exc:
            raise InboundSemanticAuthorityIntegrityError("unknown evaluator decision") from exc

    process_inbound = evaluate_after_inbound_commit
    capture_inbound_semantic = evaluate_after_inbound_commit


def create_runtime_semantic_authority_service(
    data_dir: str | Path,
) -> InboundSemanticActAuthorityServiceV1:
    """Create the production service with no evaluator (therefore no artifact)."""
    root = Path(data_dir) / "cognitive" / "p2r1a-inbound-semantic-authority"
    store = InboundSemanticActAuthorityStoreV1(root / "authority.jsonl")
    return InboundSemanticActAuthorityServiceV1(store)


def create_test_semantic_authority_service(
    path: str | Path,
    *,
    profile: InboundSemanticEvaluatorProfileV1,
    evaluator: InboundSemanticEvaluatorV1,
) -> InboundSemanticActAuthorityServiceV1:
    """Explicit test/future composition; never used by the production factory."""
    return InboundSemanticActAuthorityServiceV1(
        InboundSemanticActAuthorityStoreV1(path), profile=profile, evaluator=evaluator
    )


__all__ = [
    "AUTHORITY_PREFIX",
    "INBOUND_SEMANTIC_AUTHORITY_SCHEMA",
    "INBOUND_SEMANTIC_CONTENT_ENCODING",
    "INBOUND_SEMANTIC_PROFILE_SCHEMA",
    "INBOUND_SEMANTIC_STORE_SCHEMA",
    "P2A_V1_PRODUCTION_PROMOTABLE_RULES",
    "PRODUCTION_REVIEW_EVIDENCE_ENABLED",
    "PRODUCTION_SEMANTIC_EVALUATOR",
    "PRODUCTION_VALIDATED_EVIDENCE_COUNT",
    "EvaluatorProfileV1",
    "InboundSemanticActAuthorityIntegrityError",
    "InboundSemanticActAuthorityServiceV1",
    "InboundSemanticActAuthorityStoreV1",
    "InboundSemanticActAuthorityV1",
    "InboundSemanticAuthorityIntegrityError",
    "InboundSemanticAuthorityServiceV1",
    "InboundSemanticAuthorityStoreV1",
    "InboundSemanticAuthorityV1",
    "InboundSemanticDecision",
    "InboundSemanticEvaluationInputV1",
    "InboundSemanticEvaluationResultV1",
    "InboundSemanticEvaluatorProfileV1",
    "InboundSemanticEvaluatorV1",
    "InboundSemanticKind",
    "SemanticDecision",
    "SemanticKind",
    "canonical_content_payload_hash",
    "create_runtime_semantic_authority_service",
    "create_test_semantic_authority_service",
]


# Concise aliases used by integration callers; the V1 names above remain the
# canonical contract identifiers.
InboundSemanticAuthorityV1 = InboundSemanticActAuthorityV1
InboundSemanticAuthorityStoreV1 = InboundSemanticActAuthorityStoreV1
InboundSemanticAuthorityServiceV1 = InboundSemanticActAuthorityServiceV1
SemanticDecision = InboundSemanticDecision
SemanticKind = InboundSemanticKind
EvaluatorProfileV1 = InboundSemanticEvaluatorProfileV1
