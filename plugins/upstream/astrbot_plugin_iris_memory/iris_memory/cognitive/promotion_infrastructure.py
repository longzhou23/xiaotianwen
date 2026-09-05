"""P2a.2 immutable promotion infrastructure.

This module archives exact P1 review inputs, gives P2 artifacts deterministic
canonical identities, and provides append-only structural replay.  It does not
evaluate semantics.  Production persistence remains closed unless an exact,
separately frozen rule is enabled by runtime composition.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
from collections.abc import Mapping as ABCMapping
from dataclasses import dataclass, is_dataclass, replace
from dataclasses import fields as dataclass_fields
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from types import MappingProxyType, UnionType
from typing import Any, Mapping, Union, get_args, get_origin, get_type_hints

from .review import (
    AttributionRef,
    BehaviorScope,
    CausalAttribution,
    Confidence,
    EvidenceSourceType,
    LocalEvidenceProposition,
    ReviewEvidence,
    ReviewEvidenceRef,
    ReviewFinding,
    ReviewRun,
)
from .review_service import ReviewInputSnapshot, _canonical_json


class PromotionInfrastructureIntegrityError(ValueError):
    """A P2 immutable artifact, replay record, or persistence root is invalid."""


class LegacyArchiveUnavailableError(LookupError):
    """A pre-P2 P1 ReviewRun has no exact archival snapshot and cannot be used."""


class CommitOutcomeIndeterminateError(PromotionInfrastructureIntegrityError):
    """The final commit record may have reached storage despite an I/O error.

    The caller must not treat this as a definitive non-commit.  A later replay
    may materialize the transaction only if it finds the exact valid commit
    marker for a prior durable PREPARE record.
    """


P2_CANONICAL_ARTIFACT_ENCODING_PROFILE = "P2_CANONICAL_ARTIFACT_ENCODING_PROFILE"
P2_REVIEW_RUN_WITH_SNAPSHOT = "P2_REVIEW_RUN_WITH_SNAPSHOT"
P2_PROMOTED_EVIDENCE_COMMIT = "P2_PROMOTED_EVIDENCE_COMMIT"
P2_TX_PREPARE = "P2_TX_PREPARE"
P2_TX_COMMIT = "P2_TX_COMMIT"
_P2_TRANSACTION_SCHEMA = "p2a.persistence-transaction.v1"
COMMIT_OUTCOME_INDETERMINATE = "COMMIT_OUTCOME_INDETERMINATE"
PRODUCTION_RULE_DISABLED = "PRODUCTION_RULE_DISABLED"
NOT_P2_VALID = "NOT_P2_VALID"
EXPLICIT_CORRECTION_OF_EXACT_HOST_OUTPUT_V1 = (
    "EXPLICIT_CORRECTION_OF_EXACT_HOST_OUTPUT_V1"
)
_P2R1_PRODUCTION_RULES = frozenset({EXPLICIT_CORRECTION_OF_EXACT_HOST_OUTPUT_V1})


class CanonicalHashV1:
    """The single P2 domain-separated canonical hash primitive."""

    PREFIX = b"xiaotianwen:canonical-hash:v1\0"

    @staticmethod
    def canonical_json_utf8(payload: object) -> bytes:
        return json.dumps(
            _canonical_json_value(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    @classmethod
    def hash(cls, domain: str, payload: object) -> str:
        if not domain or not domain.isascii():
            raise PromotionInfrastructureIntegrityError("canonical hash domain must be non-empty ASCII")
        raw = cls.PREFIX + domain.encode("ascii") + b"\0" + cls.canonical_json_utf8(payload)
        return "sha256:" + hashlib.sha256(raw).hexdigest()

    @classmethod
    def digest(cls, domain: str, payload: object) -> str:
        return cls.hash(domain, payload).removeprefix("sha256:")


def _canonical_json_value(value: object) -> object:
    """Accept JSON semantic values only; never coerce arbitrary runtime objects."""
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise PromotionInfrastructureIntegrityError("NaN and Infinity are not canonical JSON")
        return value
    if type(value) in (tuple, list):
        return [_canonical_json_value(item) for item in value]
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key in sorted(value):
            if type(key) is not str:
                raise PromotionInfrastructureIntegrityError("canonical mapping keys must be strings")
            result[key] = _canonical_json_value(value[key])
        return result
    raise PromotionInfrastructureIntegrityError(f"unsupported canonical JSON value: {type(value).__name__}")


def _deep_immutable(value: object) -> object:
    """Detach one validated JSON-shaped artifact from every mutable owner.

    P2 indexes retain only this representation.  MappingProxyType at the root
    is not enough: every nested mapping and sequence must be detached before a
    historical artifact is indexed or returned to a caller.
    """
    if value is None or type(value) in (bool, int, str, bytes):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise PromotionInfrastructureIntegrityError("NaN and Infinity are not immutable canonical values")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise PromotionInfrastructureIntegrityError("immutable mapping keys must be strings")
            frozen[key] = _deep_immutable(item)
        return MappingProxyType(frozen)
    if type(value) in (tuple, list):
        return tuple(_deep_immutable(item) for item in value)
    raise PromotionInfrastructureIntegrityError(f"unsupported immutable artifact value: {type(value).__name__}")


def _deep_detached_json(value: object) -> object:
    """Return a detached, JSON-only view without exposing indexed objects."""
    return json.loads(CanonicalHashV1.canonical_json_utf8(value).decode("utf-8"))


def _strict_json_loads(text: str, *, context: str) -> object:
    def reject_constant(value: str) -> object:
        raise PromotionInfrastructureIntegrityError(f"non-finite JSON constant in {context}: {value}")

    def strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise PromotionInfrastructureIntegrityError(f"duplicate JSON key in {context}")
            result[key] = value
        return result

    try:
        return json.loads(text, parse_constant=reject_constant, object_pairs_hook=strict_object)
    except json.JSONDecodeError as exc:
        raise PromotionInfrastructureIntegrityError(f"malformed JSON in {context}") from exc


def _utc_microseconds(value: datetime) -> str:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise PromotionInfrastructureIntegrityError("canonical datetime must be timezone-aware")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _enum_value(value: object, expected: type[Enum]) -> str:
    if type(value) is not expected:
        raise PromotionInfrastructureIntegrityError(
            f"expected exact {expected.__name__} enum, got {type(value).__name__}"
        )
    return value.value


def _bytes_value(value: bytes) -> Mapping[str, str]:
    if type(value) is not bytes:
        raise PromotionInfrastructureIntegrityError("expected bytes")
    return MappingProxyType({"$bytes": base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")})


def _decode_unpadded_base64url(value: object) -> bytes:
    if type(value) is not str or re.fullmatch(r"[A-Za-z0-9_-]*", value) is None:
        raise PromotionInfrastructureIntegrityError("invalid unpadded base64url bytes")
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, TypeError) as exc:
        raise PromotionInfrastructureIntegrityError("invalid unpadded base64url bytes") from exc
    if base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != value:
        raise PromotionInfrastructureIntegrityError("non-canonical unpadded base64url bytes")
    return decoded


@dataclass(frozen=True, slots=True)
class CanonicalTypeProfileV1:
    type_name: str
    schema: str
    fields: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", tuple(self.fields))
        if not self.type_name or not self.schema or not self.fields:
            raise ValueError("canonical type profile requires type_name, schema, and fields")
        if (
            len(set(self.fields)) != len(self.fields)
            or any(type(field) is not str or not field for field in self.fields)
        ):
            raise ValueError("canonical type profile fields must be unique non-empty strings")


@dataclass(frozen=True, slots=True)
class FrozenCanonicalArtifactEncodingProfileV1:
    profile_id: str
    profile_version: str
    type_profiles: tuple[CanonicalTypeProfileV1, ...]
    schema_version: str = "p2a.canonical-artifact-encoding-profile.v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "type_profiles", tuple(self.type_profiles))
        if not self.profile_id or not self.profile_version:
            raise ValueError("encoding profile identity is required")
        if any(type(item) is not CanonicalTypeProfileV1 for item in self.type_profiles):
            raise ValueError("encoding profile entries must be CanonicalTypeProfileV1")
        names = tuple(item.type_name for item in self.type_profiles)
        if not names or len(set(names)) != len(names):
            raise ValueError("encoding profile type names must be non-empty and unique")

    def semantic_payload(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "type_profiles": tuple(
                MappingProxyType({"type_name": profile.type_name, "schema": profile.schema, "fields": profile.fields})
                for profile in self.type_profiles
            ),
        })


def canonical_artifact_encoding_profile_hash(profile: FrozenCanonicalArtifactEncodingProfileV1) -> str:
    if type(profile) is not FrozenCanonicalArtifactEncodingProfileV1:
        raise PromotionInfrastructureIntegrityError("expected FrozenCanonicalArtifactEncodingProfileV1")
    return CanonicalHashV1.hash("p2a:canonical-artifact-encoding-profile:v1", profile.semantic_payload())


_P1_SCHEMA = "p1c.review-artifact.v1"
_P2_SCHEMA = "p2a.promotion-artifact.v1"
_TYPE_FIELDS: dict[str, tuple[str, ...]] = {
    "ReviewEvidenceRef": ("ref_id", "source_type", "evidence_kind"),
    "AttributionRef": ("target_type", "ref_id"),
    "BehaviorScope": ("channel", "directedness", "intent_domain", "topic_hint", "tool_used"),
    "LocalEvidenceProposition": ("dimension", "context_refs", "behavior_refs", "observation_refs", "statement"),
    "ReviewFinding": (
        "finding_id", "review_run_id", "episode_id", "dimension", "finding_type", "claim", "evidence_refs",
        "attributed_to", "confidence", "causal_attribution", "interpretation_producer", "created_at", "producer",
        "model", "model_version", "prompt_version", "provenance",
    ),
    "ReviewRun": (
        "review_run_id", "episode_id", "status", "input_snapshot_hash", "created_at", "model", "model_version",
        "prompt_version", "schema_version", "raw_output_digest", "findings", "producer", "provenance",
    ),
    "ReviewEvidence": (
        "evidence_id", "source_review_run_id", "source_finding_id", "source_episode_id", "dimension", "proposition",
        "scope", "evidence_refs", "confidence", "causal_attribution", "attributed_to", "interpretation_producer",
        "created_at", "producer", "model", "model_version", "prompt_version", "schema_version", "provenance",
    ),
    "CanonicalTypeProfileV1": ("type_name", "schema", "fields"),
    "FrozenCanonicalArtifactEncodingProfileV1": ("profile_id", "profile_version", "type_profiles", "schema_version"),
    "PromotionSnapshotArchiveV1": (
        "schema_version", "review_run_id", "episode_id", "input_snapshot_hash", "canonical_snapshot_json_utf8",
        "archive_payload_hash",
    ),
    "P2ReviewRunWithSnapshotV1": (
        "schema_version", "run", "archive", "encoding_profile_hash", "logical_commit_hash",
    ),
    "EvidenceSemanticBodyV1": ("schema_version", "evidence_without_id", "proposition_hash", "semantic_body_hash"),
    "EvidenceIdentityBodyV1": ("schema_version", "semantic_body_hash", "encoding_profile_hash"),
    "ReceiptIdentityBodyV1": ("schema_version", "evidence_id", "evidence_payload_hash", "encoding_profile_hash"),
    "PromotionReceiptV1": ("schema_version", "receipt_id", "evidence_id", "evidence_payload_hash", "encoding_profile_hash"),
    "P2PromotedEvidenceCommitV1": ("schema_version", "evidence", "receipt", "encoding_profile_hash", "logical_commit_hash"),
}

_TYPE_SCHEMAS: Mapping[str, str] = MappingProxyType({
    name: _P1_SCHEMA if name in {
        "ReviewEvidenceRef", "AttributionRef", "BehaviorScope", "LocalEvidenceProposition",
        "ReviewFinding", "ReviewRun", "ReviewEvidence",
    } else _P2_SCHEMA
    for name in _TYPE_FIELDS
})


def default_encoding_profile_v1() -> FrozenCanonicalArtifactEncodingProfileV1:
    profiles = tuple(
        CanonicalTypeProfileV1(
            type_name=name,
            schema=_TYPE_SCHEMAS[name],
            fields=fields,
        )
        for name, fields in _TYPE_FIELDS.items()
    )
    return FrozenCanonicalArtifactEncodingProfileV1("p2a.canonical-artifacts", "1", profiles)


class P2CanonicalArtifactEncoderV1:
    """Explicit, closed encoders for the accepted P1 and P2 artifact roots."""

    def __init__(self, profile: FrozenCanonicalArtifactEncodingProfileV1 | None = None) -> None:
        # Reconstruct the public profile through its normalizing frozen
        # dataclasses so a caller-owned list cannot remain authoritative.
        supplied = profile or default_encoding_profile_v1()
        if type(supplied) is not FrozenCanonicalArtifactEncodingProfileV1:
            raise PromotionInfrastructureIntegrityError("expected frozen encoding profile")
        self.profile = FrozenCanonicalArtifactEncodingProfileV1(
            supplied.profile_id,
            supplied.profile_version,
            tuple(CanonicalTypeProfileV1(item.type_name, item.schema, item.fields) for item in supplied.type_profiles),
            supplied.schema_version,
        )
        self.profile_hash = canonical_artifact_encoding_profile_hash(self.profile)
        self._profiles = {item.type_name: item for item in self.profile.type_profiles}
        if set(self._profiles) != set(_TYPE_FIELDS):
            raise PromotionInfrastructureIntegrityError("encoding profile does not define the exact v1 type set")
        for name, expected in _TYPE_FIELDS.items():
            entry = self._profiles[name]
            if entry.schema != _TYPE_SCHEMAS[name] or entry.fields != expected:
                raise PromotionInfrastructureIntegrityError(f"encoding profile schema or fields differ for {name}")

    def encode(self, value: object) -> Mapping[str, object]:
        encoded = self._encode(value)
        frozen = _deep_immutable(encoded)
        if not isinstance(frozen, Mapping):  # pragma: no cover - defensive typing guard
            raise PromotionInfrastructureIntegrityError("encoded artifact must be a mapping")
        return frozen

    def _envelope(self, name: str, schema: str, fields: Mapping[str, object], instance: object) -> dict[str, object]:
        expected = _TYPE_FIELDS[name]
        actual = tuple(getattr(type(instance), "__dataclass_fields__", {}).keys())
        if actual != expected or tuple(fields.keys()) != expected or schema != _TYPE_SCHEMAS[name]:
            raise PromotionInfrastructureIntegrityError(f"unknown, missing, or reordered fields for {name}")
        if self._profiles[name].schema != schema or self._profiles[name].fields != expected:
            raise PromotionInfrastructureIntegrityError(f"encoder/profile mismatch for {name}")
        return {"$type": name, "$schema": self._profiles[name].schema, "fields": dict(fields)}

    @staticmethod
    def _scalar(value: object) -> object:
        if value is None or type(value) in (bool, int, str):
            return value
        if type(value) is float:
            if not math.isfinite(value):
                raise PromotionInfrastructureIntegrityError("non-finite scalar")
            return value
        raise PromotionInfrastructureIntegrityError(f"unsupported scalar: {type(value).__name__}")

    def _tuple(self, values: tuple[object, ...], encoder: Any = None) -> tuple[object, ...]:
        if type(values) is not tuple:
            raise PromotionInfrastructureIntegrityError("canonical artifact sequences must be exact tuples")
        encode = encoder or self._scalar
        return tuple(encode(value) for value in values)

    def _encode(self, value: object) -> dict[str, object]:
        from .review import (
            AttributionTargetType,
            EvidenceKind,
            EvidenceSourceType,
            FindingType,
            InterpretationProducer,
            ReviewDimension,
            ReviewStatus,
        )

        if type(value) is ReviewEvidenceRef:
            return self._envelope("ReviewEvidenceRef", _P1_SCHEMA, {
                "ref_id": self._scalar(value.ref_id),
                "source_type": _enum_value(value.source_type, EvidenceSourceType),
                "evidence_kind": _enum_value(value.evidence_kind, EvidenceKind),
            }, value)
        if type(value) is AttributionRef:
            return self._envelope("AttributionRef", _P1_SCHEMA, {
                "target_type": _enum_value(value.target_type, AttributionTargetType),
                "ref_id": self._scalar(value.ref_id),
            }, value)
        if type(value) is BehaviorScope:
            return self._envelope("BehaviorScope", _P1_SCHEMA, {
                "channel": self._scalar(value.channel), "directedness": self._scalar(value.directedness),
                "intent_domain": self._scalar(value.intent_domain), "topic_hint": self._scalar(value.topic_hint),
                "tool_used": self._scalar(value.tool_used),
            }, value)
        if type(value) is LocalEvidenceProposition:
            return self._envelope("LocalEvidenceProposition", _P1_SCHEMA, {
                "dimension": _enum_value(value.dimension, ReviewDimension),
                "context_refs": self._tuple(value.context_refs), "behavior_refs": self._tuple(value.behavior_refs),
                "observation_refs": self._tuple(value.observation_refs), "statement": self._scalar(value.statement),
            }, value)
        if type(value) is ReviewFinding:
            return self._envelope("ReviewFinding", _P1_SCHEMA, {
                "finding_id": self._scalar(value.finding_id), "review_run_id": self._scalar(value.review_run_id),
                "episode_id": self._scalar(value.episode_id), "dimension": _enum_value(value.dimension, ReviewDimension),
                "finding_type": _enum_value(value.finding_type, FindingType), "claim": self._scalar(value.claim),
                "evidence_refs": self._tuple(value.evidence_refs, self._encode), "attributed_to": self._encode(value.attributed_to),
                "confidence": _enum_value(value.confidence, Confidence),
                "causal_attribution": _enum_value(value.causal_attribution, CausalAttribution),
                "interpretation_producer": _enum_value(value.interpretation_producer, InterpretationProducer),
                "created_at": _utc_microseconds(value.created_at), "producer": self._scalar(value.producer),
                "model": self._scalar(value.model), "model_version": self._scalar(value.model_version),
                "prompt_version": self._scalar(value.prompt_version), "provenance": self._tuple(value.provenance),
            }, value)
        if type(value) is ReviewRun:
            return self._envelope("ReviewRun", _P1_SCHEMA, {
                "review_run_id": self._scalar(value.review_run_id), "episode_id": self._scalar(value.episode_id),
                "status": _enum_value(value.status, ReviewStatus), "input_snapshot_hash": self._scalar(value.input_snapshot_hash),
                "created_at": _utc_microseconds(value.created_at), "model": self._scalar(value.model),
                "model_version": self._scalar(value.model_version), "prompt_version": self._scalar(value.prompt_version),
                "schema_version": self._scalar(value.schema_version), "raw_output_digest": self._scalar(value.raw_output_digest),
                "findings": self._tuple(value.findings, self._encode), "producer": self._scalar(value.producer),
                "provenance": self._tuple(value.provenance),
            }, value)
        if type(value) is ReviewEvidence:
            return self._envelope("ReviewEvidence", _P1_SCHEMA, {
                "evidence_id": self._scalar(value.evidence_id), "source_review_run_id": self._scalar(value.source_review_run_id),
                "source_finding_id": self._scalar(value.source_finding_id), "source_episode_id": self._scalar(value.source_episode_id),
                "dimension": _enum_value(value.dimension, ReviewDimension), "proposition": self._encode(value.proposition),
                "scope": self._encode(value.scope), "evidence_refs": self._tuple(value.evidence_refs, self._encode),
                "confidence": _enum_value(value.confidence, Confidence),
                "causal_attribution": _enum_value(value.causal_attribution, CausalAttribution),
                "attributed_to": self._encode(value.attributed_to),
                "interpretation_producer": _enum_value(value.interpretation_producer, InterpretationProducer),
                "created_at": _utc_microseconds(value.created_at), "producer": self._scalar(value.producer),
                "model": self._scalar(value.model), "model_version": self._scalar(value.model_version),
                "prompt_version": self._scalar(value.prompt_version), "schema_version": self._scalar(value.schema_version),
                "provenance": self._tuple(value.provenance),
            }, value)
        if type(value) is CanonicalTypeProfileV1:
            return self._envelope("CanonicalTypeProfileV1", _P2_SCHEMA, {
                "type_name": self._scalar(value.type_name), "schema": self._scalar(value.schema),
                "fields": self._tuple(value.fields),
            }, value)
        if type(value) is FrozenCanonicalArtifactEncodingProfileV1:
            return self._envelope("FrozenCanonicalArtifactEncodingProfileV1", _P2_SCHEMA, {
                "profile_id": self._scalar(value.profile_id), "profile_version": self._scalar(value.profile_version),
                "type_profiles": self._tuple(value.type_profiles, self._encode), "schema_version": self._scalar(value.schema_version),
            }, value)
        if type(value) is PromotionSnapshotArchiveV1:
            return self._envelope("PromotionSnapshotArchiveV1", _P2_SCHEMA, {
                "schema_version": self._scalar(value.schema_version), "review_run_id": self._scalar(value.review_run_id),
                "episode_id": self._scalar(value.episode_id), "input_snapshot_hash": self._scalar(value.input_snapshot_hash),
                "canonical_snapshot_json_utf8": _bytes_value(value.canonical_snapshot_json_utf8),
                "archive_payload_hash": self._scalar(value.archive_payload_hash),
            }, value)
        if type(value) is P2ReviewRunWithSnapshotV1:
            return self._envelope("P2ReviewRunWithSnapshotV1", _P2_SCHEMA, {
                "schema_version": self._scalar(value.schema_version), "run": self._encode(value.run),
                "archive": self._encode(value.archive), "encoding_profile_hash": self._scalar(value.encoding_profile_hash),
                "logical_commit_hash": self._scalar(value.logical_commit_hash),
            }, value)
        if type(value) is EvidenceSemanticBodyV1:
            return self._envelope("EvidenceSemanticBodyV1", _P2_SCHEMA, {
                "schema_version": self._scalar(value.schema_version), "evidence_without_id": _canonical_json_value(value.evidence_without_id),
                "proposition_hash": self._scalar(value.proposition_hash), "semantic_body_hash": self._scalar(value.semantic_body_hash),
            }, value)
        if type(value) is EvidenceIdentityBodyV1:
            return self._envelope("EvidenceIdentityBodyV1", _P2_SCHEMA, {
                "schema_version": self._scalar(value.schema_version), "semantic_body_hash": self._scalar(value.semantic_body_hash),
                "encoding_profile_hash": self._scalar(value.encoding_profile_hash),
            }, value)
        if type(value) is ReceiptIdentityBodyV1:
            return self._envelope("ReceiptIdentityBodyV1", _P2_SCHEMA, {
                "schema_version": self._scalar(value.schema_version), "evidence_id": self._scalar(value.evidence_id),
                "evidence_payload_hash": self._scalar(value.evidence_payload_hash),
                "encoding_profile_hash": self._scalar(value.encoding_profile_hash),
            }, value)
        if type(value) is PromotionReceiptV1:
            return self._envelope("PromotionReceiptV1", _P2_SCHEMA, {
                "schema_version": self._scalar(value.schema_version), "receipt_id": self._scalar(value.receipt_id),
                "evidence_id": self._scalar(value.evidence_id), "evidence_payload_hash": self._scalar(value.evidence_payload_hash),
                "encoding_profile_hash": self._scalar(value.encoding_profile_hash),
            }, value)
        if type(value) is P2PromotedEvidenceCommitV1:
            return self._envelope("P2PromotedEvidenceCommitV1", _P2_SCHEMA, {
                "schema_version": self._scalar(value.schema_version), "evidence": self._encode(value.evidence),
                "receipt": self._encode(value.receipt), "encoding_profile_hash": self._scalar(value.encoding_profile_hash),
                "logical_commit_hash": self._scalar(value.logical_commit_hash),
            }, value)
        raise PromotionInfrastructureIntegrityError(f"unsupported canonical artifact type: {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class PromotionSnapshotArchiveV1:
    schema_version: str
    review_run_id: str
    episode_id: str
    input_snapshot_hash: str
    canonical_snapshot_json_utf8: bytes
    archive_payload_hash: str

    @classmethod
    def from_snapshot(cls, run: ReviewRun, snapshot: ReviewInputSnapshot) -> "PromotionSnapshotArchiveV1":
        if type(run) is not ReviewRun or type(snapshot) is not ReviewInputSnapshot:
            raise PromotionInfrastructureIntegrityError("archive requires exact P1 ReviewRun and ReviewInputSnapshot")
        raw = _canonical_json(snapshot.canonical_payload()).encode("utf-8")
        p1_hash = "sha256:" + hashlib.sha256(raw).hexdigest()
        if run.review_run_id == "" or run.episode_id != snapshot.episode.episode_id or run.input_snapshot_hash != p1_hash:
            raise PromotionInfrastructureIntegrityError("ReviewRun does not match exact P1 input snapshot")
        body = {
            "schema_version": "p2a.promotion-snapshot-archive.v1", "review_run_id": run.review_run_id,
            "episode_id": run.episode_id, "input_snapshot_hash": p1_hash,
            "canonical_snapshot_json_utf8": _bytes_value(raw),
        }
        return cls(
            body["schema_version"], run.review_run_id, run.episode_id, p1_hash, raw,
            CanonicalHashV1.hash("p2a:promotion-snapshot-archive:v1", body),
        )

    def body(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version, "review_run_id": self.review_run_id, "episode_id": self.episode_id,
            "input_snapshot_hash": self.input_snapshot_hash,
            "canonical_snapshot_json_utf8": _bytes_value(self.canonical_snapshot_json_utf8),
        })

    def validate(self) -> None:
        if self.schema_version != "p2a.promotion-snapshot-archive.v1":
            raise PromotionInfrastructureIntegrityError("unknown snapshot archive schema")
        p1_hash = "sha256:" + hashlib.sha256(self.canonical_snapshot_json_utf8).hexdigest()
        if p1_hash != self.input_snapshot_hash:
            raise PromotionInfrastructureIntegrityError("archive snapshot bytes do not match P1 snapshot hash")
        if self.archive_payload_hash != CanonicalHashV1.hash("p2a:promotion-snapshot-archive:v1", self.body()):
            raise PromotionInfrastructureIntegrityError("snapshot archive payload hash mismatch")


@dataclass(frozen=True, slots=True)
class P2ReviewRunWithSnapshotV1:
    schema_version: str
    run: ReviewRun
    archive: PromotionSnapshotArchiveV1
    encoding_profile_hash: str
    logical_commit_hash: str

    @classmethod
    def create(cls, run: ReviewRun, snapshot: ReviewInputSnapshot, encoder: P2CanonicalArtifactEncoderV1) -> "P2ReviewRunWithSnapshotV1":
        archive = PromotionSnapshotArchiveV1.from_snapshot(run, snapshot)
        body = {
            "schema_version": "p2a.review-run-with-snapshot.v1", "run": encoder.encode(run),
            "archive": encoder.encode(archive), "encoding_profile_hash": encoder.profile_hash,
        }
        return cls(
            "p2a.review-run-with-snapshot.v1", run, archive, encoder.profile_hash,
            CanonicalHashV1.hash("p2a:review-run-with-snapshot:v1", body),
        )

    def validate(self, encoder: P2CanonicalArtifactEncoderV1) -> None:
        self.archive.validate()
        if self.schema_version != "p2a.review-run-with-snapshot.v1":
            raise PromotionInfrastructureIntegrityError("unknown run/snapshot schema")
        if self.run.review_run_id != self.archive.review_run_id or self.run.episode_id != self.archive.episode_id:
            raise PromotionInfrastructureIntegrityError("run/archive identity mismatch")
        if self.run.input_snapshot_hash != self.archive.input_snapshot_hash:
            raise PromotionInfrastructureIntegrityError("run/archive snapshot hash mismatch")
        if self.encoding_profile_hash != encoder.profile_hash:
            raise PromotionInfrastructureIntegrityError("run/snapshot encoding profile hash mismatch")
        body = {
            "schema_version": self.schema_version, "run": encoder.encode(self.run), "archive": encoder.encode(self.archive),
            "encoding_profile_hash": self.encoding_profile_hash,
        }
        if self.logical_commit_hash != CanonicalHashV1.hash("p2a:review-run-with-snapshot:v1", body):
            raise PromotionInfrastructureIntegrityError("run/snapshot logical commit hash mismatch")


@dataclass(frozen=True, slots=True)
class EvidenceSemanticBodyV1:
    schema_version: str
    evidence_without_id: Mapping[str, object]
    proposition_hash: str
    semantic_body_hash: str

    @classmethod
    def from_evidence(cls, evidence: ReviewEvidence, encoder: P2CanonicalArtifactEncoderV1) -> "EvidenceSemanticBodyV1":
        encoded = dict(encoder.encode(evidence))
        fields = dict(encoded["fields"])
        fields.pop("evidence_id")
        without_id = MappingProxyType({"$type": encoded["$type"], "$schema": encoded["$schema"], "fields": MappingProxyType(fields)})
        proposition_hash = CanonicalHashV1.hash("p2a:local-evidence-proposition:v1", encoder.encode(evidence.proposition))
        body = {"schema_version": "p2a.evidence-semantic-body.v1", "evidence_without_id": without_id, "proposition_hash": proposition_hash}
        return cls(body["schema_version"], without_id, proposition_hash, CanonicalHashV1.hash("p2a:evidence-semantic-body:v1", body))


@dataclass(frozen=True, slots=True)
class EvidenceIdentityBodyV1:
    schema_version: str
    semantic_body_hash: str
    encoding_profile_hash: str

    def evidence_id(self) -> str:
        return "evidence:p2a:" + CanonicalHashV1.digest("p2a:evidence-identity:v1", {
            "schema_version": self.schema_version, "semantic_body_hash": self.semantic_body_hash,
            "encoding_profile_hash": self.encoding_profile_hash,
        })


@dataclass(frozen=True, slots=True)
class ReceiptIdentityBodyV1:
    schema_version: str
    evidence_id: str
    evidence_payload_hash: str
    encoding_profile_hash: str

    def receipt_id(self) -> str:
        return "receipt:p2a:" + CanonicalHashV1.digest("p2a:receipt-identity:v1", {
            "schema_version": self.schema_version, "evidence_id": self.evidence_id,
            "evidence_payload_hash": self.evidence_payload_hash, "encoding_profile_hash": self.encoding_profile_hash,
        })


@dataclass(frozen=True, slots=True)
class PromotionReceiptV1:
    schema_version: str
    receipt_id: str
    evidence_id: str
    evidence_payload_hash: str
    encoding_profile_hash: str


@dataclass(frozen=True, slots=True)
class P2PromotedEvidenceCommitV1:
    schema_version: str
    evidence: ReviewEvidence
    receipt: PromotionReceiptV1
    encoding_profile_hash: str
    logical_commit_hash: str

    def validate(self, encoder: P2CanonicalArtifactEncoderV1) -> None:
        if self.schema_version != "p2a.promoted-evidence-commit.v1":
            raise PromotionInfrastructureIntegrityError("unknown promoted evidence commit schema")
        payload_hash = CanonicalHashV1.hash("p2a:review-evidence-payload:v1", encoder.encode(self.evidence))
        if self.receipt.evidence_id != self.evidence.evidence_id or self.receipt.evidence_payload_hash != payload_hash:
            raise PromotionInfrastructureIntegrityError("receipt does not bind the exact Evidence payload")
        identity = ReceiptIdentityBodyV1("p2a.receipt-identity.v1", self.evidence.evidence_id, payload_hash, self.encoding_profile_hash)
        if self.receipt.receipt_id != identity.receipt_id():
            raise PromotionInfrastructureIntegrityError("receipt identity mismatch")
        body = {
            "schema_version": self.schema_version, "evidence": encoder.encode(self.evidence),
            "receipt": encoder.encode(self.receipt), "encoding_profile_hash": self.encoding_profile_hash,
        }
        if self.logical_commit_hash != CanonicalHashV1.hash("p2a:promoted-evidence-commit:v1", body):
            raise PromotionInfrastructureIntegrityError("promoted evidence logical commit hash mismatch")


@dataclass(frozen=True, slots=True)
class PromotionCommand:
    finding_id: str
    requested_rule_id: str | None = None


@dataclass(frozen=True, slots=True)
class PromotionGateDecision:
    accepted: bool
    reason: str


class ProductionPromotionGateV1:
    """Closed, exact-rule gate for an explicitly composed production writer.

    The gate deliberately accepts neither wildcards nor arbitrary rule names.
    A normal construction has no enabled rules; runtime composition must opt
    into the one frozen P2r.1 identifier.
    """

    def __init__(self, *, enable_explicit_correction_rule: bool = False) -> None:
        if type(enable_explicit_correction_rule) is not bool:
            raise PromotionInfrastructureIntegrityError("production rule enablement must be a bool")
        self.enabled_rules = (
            _P2R1_PRODUCTION_RULES if enable_explicit_correction_rule else frozenset()
        )

    def evaluate(self, command: PromotionCommand) -> PromotionGateDecision:
        if type(command) is not PromotionCommand:
            raise PromotionInfrastructureIntegrityError("PromotionCommand shape is invalid")
        if command.requested_rule_id in self.enabled_rules:
            return PromotionGateDecision(True, "ACCEPTED_EXACT_RULE")
        return PromotionGateDecision(False, PRODUCTION_RULE_DISABLED)


@dataclass(frozen=True, slots=True)
class SyntheticPromotionCandidateV1:
    """Unpersisted test-only input; deliberately carries no receipt."""

    evidence: ReviewEvidence
    finding: ReviewFinding
    encoding_profile_hash: str


def build_synthetic_candidate(
    evidence: ReviewEvidence,
    finding: ReviewFinding,
    encoder: P2CanonicalArtifactEncoderV1,
) -> SyntheticPromotionCandidateV1:
    """Build an unpersisted synthetic input without receipt authority."""
    if type(evidence) is not ReviewEvidence or type(finding) is not ReviewFinding:
        raise PromotionInfrastructureIntegrityError("synthetic commit requires exact P1 artifacts")
    if (
        evidence.source_finding_id != finding.finding_id
        or evidence.source_review_run_id != finding.review_run_id
        or evidence.source_episode_id != finding.episode_id
        or evidence.created_at != finding.created_at
    ):
        raise PromotionInfrastructureIntegrityError("synthetic Evidence does not retain authoritative Finding lineage/timestamp")
    semantic = EvidenceSemanticBodyV1.from_evidence(evidence, encoder)
    identity = EvidenceIdentityBodyV1("p2a.evidence-identity.v1", semantic.semantic_body_hash, encoder.profile_hash)
    canonical_evidence = replace(evidence, evidence_id=identity.evidence_id())
    return SyntheticPromotionCandidateV1(canonical_evidence, finding, encoder.profile_hash)


def _materialize_synthetic_commit(
    candidate: SyntheticPromotionCandidateV1,
    encoder: P2CanonicalArtifactEncoderV1,
) -> P2PromotedEvidenceCommitV1:
    """Create the private append candidate; callers receive it only after write."""
    if type(candidate) is not SyntheticPromotionCandidateV1:
        raise PromotionInfrastructureIntegrityError("synthetic writer requires an exact test candidate")
    if candidate.encoding_profile_hash != encoder.profile_hash:
        raise PromotionInfrastructureIntegrityError("synthetic candidate profile mismatch")
    canonical_evidence = candidate.evidence
    payload_hash = CanonicalHashV1.hash("p2a:review-evidence-payload:v1", encoder.encode(canonical_evidence))
    receipt_identity = ReceiptIdentityBodyV1("p2a.receipt-identity.v1", canonical_evidence.evidence_id, payload_hash, encoder.profile_hash)
    receipt = PromotionReceiptV1(
        "p2a.promotion-receipt.v1", receipt_identity.receipt_id(), canonical_evidence.evidence_id, payload_hash, encoder.profile_hash
    )
    body = {
        "schema_version": "p2a.promoted-evidence-commit.v1", "evidence": encoder.encode(canonical_evidence),
        "receipt": encoder.encode(receipt), "encoding_profile_hash": encoder.profile_hash,
    }
    return P2PromotedEvidenceCommitV1(
        "p2a.promoted-evidence-commit.v1", canonical_evidence, receipt, encoder.profile_hash,
        CanonicalHashV1.hash("p2a:promoted-evidence-commit:v1", body),
    )


def _strict_envelope(payload: object, type_name: str) -> Mapping[str, object]:
    """Decode one closed P1/P2 envelope without permissive JSON coercion."""
    if not isinstance(payload, Mapping) or set(payload) != {"$type", "$schema", "fields"}:
        raise PromotionInfrastructureIntegrityError(f"invalid encoded {type_name}")
    if payload.get("$type") != type_name or payload.get("$schema") != _TYPE_SCHEMAS[type_name]:
        raise PromotionInfrastructureIntegrityError(f"invalid encoded {type_name}")
    fields = payload.get("fields")
    if not isinstance(fields, Mapping) or set(fields) != set(_TYPE_FIELDS[type_name]):
        raise PromotionInfrastructureIntegrityError(f"encoded {type_name} fields mismatch")
    return fields


def _scalar(value: object, *, nullable: bool = False) -> str | int | float | bool | None:
    if nullable and value is None:
        return None
    if type(value) in (str, int, bool):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise PromotionInfrastructureIntegrityError("invalid canonical scalar")


def _string(value: object, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if type(value) is not str:
        raise PromotionInfrastructureIntegrityError("expected canonical string")
    return value


def _tuple_strings(value: object) -> tuple[str, ...]:
    if type(value) not in (tuple, list) or any(type(item) is not str for item in value):
        raise PromotionInfrastructureIntegrityError("expected canonical string sequence")
    return tuple(value)


def _datetime_from_canonical(value: object) -> datetime:
    if type(value) is not str:
        raise PromotionInfrastructureIntegrityError("expected canonical datetime string")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise PromotionInfrastructureIntegrityError("invalid canonical datetime") from exc
    if _utc_microseconds(parsed) != value:
        raise PromotionInfrastructureIntegrityError("non-canonical datetime")
    return parsed


def _is_p2_identifier(value: object, prefix: str) -> bool:
    digest = value[len(prefix):] if type(value) is str and value.startswith(prefix) else ""
    return len(digest) == 64 and all(character in "0123456789abcdef" for character in digest)


def _decode_review_evidence_ref(payload: object) -> ReviewEvidenceRef:
    from .review import EvidenceKind, EvidenceSourceType

    fields = _strict_envelope(payload, "ReviewEvidenceRef")
    try:
        return ReviewEvidenceRef(_string(fields["ref_id"]), EvidenceSourceType(_string(fields["source_type"])), EvidenceKind(_string(fields["evidence_kind"])))
    except (TypeError, ValueError) as exc:
        raise PromotionInfrastructureIntegrityError("invalid ReviewEvidenceRef") from exc


def _decode_attribution_ref(payload: object) -> AttributionRef:
    from .review import AttributionTargetType

    fields = _strict_envelope(payload, "AttributionRef")
    try:
        return AttributionRef(AttributionTargetType(_string(fields["target_type"])), _string(fields["ref_id"]))
    except (TypeError, ValueError) as exc:
        raise PromotionInfrastructureIntegrityError("invalid AttributionRef") from exc


def _decode_scope(payload: object) -> BehaviorScope:
    fields = _strict_envelope(payload, "BehaviorScope")
    return BehaviorScope(
        _string(fields["channel"], nullable=True),
        _string(fields["directedness"], nullable=True),
        _string(fields["intent_domain"], nullable=True),
        _string(fields["topic_hint"], nullable=True),
        _scalar(fields["tool_used"], nullable=True),
    )


def _decode_proposition(payload: object) -> LocalEvidenceProposition:
    from .review import ReviewDimension

    fields = _strict_envelope(payload, "LocalEvidenceProposition")
    try:
        return LocalEvidenceProposition(
            ReviewDimension(_string(fields["dimension"])),
            _tuple_strings(fields["context_refs"]),
            _tuple_strings(fields["behavior_refs"]),
            _tuple_strings(fields["observation_refs"]),
            _string(fields["statement"]),
        )
    except (TypeError, ValueError) as exc:
        raise PromotionInfrastructureIntegrityError("invalid LocalEvidenceProposition") from exc


def _decode_finding(payload: object) -> ReviewFinding:
    from .review import (
        CausalAttribution,
        Confidence,
        FindingType,
        InterpretationProducer,
        ReviewDimension,
    )

    fields = _strict_envelope(payload, "ReviewFinding")
    try:
        return ReviewFinding(
            finding_id=_string(fields["finding_id"]), review_run_id=_string(fields["review_run_id"]),
            episode_id=_string(fields["episode_id"]), dimension=ReviewDimension(_string(fields["dimension"])),
            finding_type=FindingType(_string(fields["finding_type"])), claim=_string(fields["claim"]),
            evidence_refs=tuple(_decode_review_evidence_ref(item) for item in fields["evidence_refs"]),
            attributed_to=_decode_attribution_ref(fields["attributed_to"]), confidence=Confidence(_string(fields["confidence"])),
            causal_attribution=CausalAttribution(_string(fields["causal_attribution"])),
            interpretation_producer=InterpretationProducer(_string(fields["interpretation_producer"])),
            created_at=_datetime_from_canonical(fields["created_at"]), producer=_string(fields["producer"], nullable=True),
            model=_string(fields["model"], nullable=True), model_version=_string(fields["model_version"], nullable=True),
            prompt_version=_string(fields["prompt_version"], nullable=True), provenance=_tuple_strings(fields["provenance"]),
        )
    except (TypeError, ValueError) as exc:
        raise PromotionInfrastructureIntegrityError("invalid ReviewFinding") from exc


def _decode_run(payload: object) -> ReviewRun:
    from .review import ReviewStatus

    fields = _strict_envelope(payload, "ReviewRun")
    try:
        return ReviewRun(
            review_run_id=_string(fields["review_run_id"]), episode_id=_string(fields["episode_id"]),
            status=ReviewStatus(_string(fields["status"])), input_snapshot_hash=_string(fields["input_snapshot_hash"]),
            created_at=_datetime_from_canonical(fields["created_at"]), model=_string(fields["model"], nullable=True),
            model_version=_string(fields["model_version"], nullable=True), prompt_version=_string(fields["prompt_version"], nullable=True),
            schema_version=_string(fields["schema_version"]), raw_output_digest=_string(fields["raw_output_digest"], nullable=True),
            findings=tuple(_decode_finding(item) for item in fields["findings"]),
            producer=_string(fields["producer"], nullable=True), provenance=_tuple_strings(fields["provenance"]),
        )
    except (TypeError, ValueError) as exc:
        raise PromotionInfrastructureIntegrityError("invalid ReviewRun") from exc


def _decode_evidence(payload: object) -> ReviewEvidence:
    from .review import (
        CausalAttribution,
        Confidence,
        InterpretationProducer,
        ReviewDimension,
    )

    fields = _strict_envelope(payload, "ReviewEvidence")
    try:
        return ReviewEvidence(
            evidence_id=_string(fields["evidence_id"]), source_review_run_id=_string(fields["source_review_run_id"]),
            source_finding_id=_string(fields["source_finding_id"]), source_episode_id=_string(fields["source_episode_id"]),
            dimension=ReviewDimension(_string(fields["dimension"])), proposition=_decode_proposition(fields["proposition"]),
            scope=_decode_scope(fields["scope"]), evidence_refs=tuple(_decode_review_evidence_ref(item) for item in fields["evidence_refs"]),
            confidence=Confidence(_string(fields["confidence"])), causal_attribution=CausalAttribution(_string(fields["causal_attribution"])),
            attributed_to=_decode_attribution_ref(fields["attributed_to"]),
            interpretation_producer=InterpretationProducer(_string(fields["interpretation_producer"])),
            created_at=_datetime_from_canonical(fields["created_at"]), producer=_string(fields["producer"], nullable=True),
            model=_string(fields["model"], nullable=True), model_version=_string(fields["model_version"], nullable=True),
            prompt_version=_string(fields["prompt_version"], nullable=True), schema_version=_string(fields["schema_version"]),
            provenance=_tuple_strings(fields["provenance"]),
        )
    except (TypeError, ValueError) as exc:
        raise PromotionInfrastructureIntegrityError("invalid ReviewEvidence") from exc


_P1_SNAPSHOT_ROOT_FIELDS = frozenset({"schema_version", "episode", "outcomes", "fact_envelopes"})
_P1_SNAPSHOT_TYPE_FIELDS: Mapping[str, frozenset[str]] = MappingProxyType({
    "iris_memory.cognitive.episode.Episode": frozenset({
        "episode_id", "scope_id", "state", "root_event_id", "opened_at", "last_activity_at", "participants",
        "topic_hint", "event_refs", "unresolved_refs", "soft_closed_at", "finalized_at", "revision", "provenance",
    }),
    "iris_memory.cognitive.episode.EpisodeEventRef": frozenset({
        "ref_id", "kind", "source_event_id", "trace_id", "execution_record_id", "observed_at", "actor_entity",
    }),
    "iris_memory.cognitive.outcome.OutcomeObservation": frozenset({
        "observation_id", "target_episode_id", "kind", "observed_at", "source_event_id", "source_ref_id",
        "actor_entity", "target_entity", "explicitness", "confidence", "evidence", "producer", "provenance",
    }),
    "iris_memory.cognitive.contracts.EntityReference": frozenset({"entity_id", "source", "confidence", "evidence"}),
    "iris_memory.cognitive.contracts.BehaviorExecutionRecord": frozenset({"trace", "host_result", "comparison", "stage", "revision", "updated_at"}),
    "iris_memory.cognitive.contracts.BehaviorLoopResult": frozenset({"trace", "realizer_request"}),
    "iris_memory.cognitive.contracts.BehaviorTrace": frozenset({"event_id", "trigger", "participation", "intent", "grounding", "exit_reason", "runtime_mode", "created_at", "identity", "situation_lite", "proposed_output_state", "trace_id"}),
    "iris_memory.cognitive.contracts.CanonicalEntity": frozenset({"id", "aliases", "platform_ids"}),
    "iris_memory.cognitive.contracts.CanonicalExperience": frozenset({"id", "event", "subject", "perspective", "provenance"}),
    "iris_memory.cognitive.contracts.EventExecutionContext": frozenset({"event_id", "runtime_mode", "started_at"}),
    "iris_memory.cognitive.contracts.GroundingResult": frozenset({"semantic_requirement", "status", "basis", "allowed_claims", "blocked_claims", "required_tool", "confidence", "requested_enforcement"}),
    "iris_memory.cognitive.contracts.HostResult": frozenset({"legacy_fallthrough", "output_generated", "output_nonempty", "dispatch_observed", "output_state", "producer", "applied_enforcement", "delivery_status"}),
    "iris_memory.cognitive.contracts.IdentityClaim": frozenset({"mention", "candidate_entity", "evidence", "confidence", "source", "status", "created_at"}),
    "iris_memory.cognitive.contracts.IdentityConfig": frozenset({"self_entity", "self_aliases"}),
    "iris_memory.cognitive.contracts.Intent": frozenset({"action", "target_entity", "reason", "basis", "confidence", "exit_reason", "domain"}),
    "iris_memory.cognitive.contracts.IrisPreprocessResult": frozenset({"experience", "metadata"}),
    "iris_memory.cognitive.contracts.LegacyProactiveSignals": frozenset({"activation_signal", "willingness", "threshold", "cooldown", "consecutive_reply_penalty", "skip_signal", "topic_drift_signal", "post_evaluation_signal"}),
    "iris_memory.cognitive.contracts.ParticipationResult": frozenset({"decision", "reason", "exit_reason"}),
    "iris_memory.cognitive.contracts.RealizerRequest": frozenset({"intent", "grounding", "situation", "allowed_claims", "blocked_claims"}),
    "iris_memory.cognitive.contracts.ResolvedEvent": frozenset({"event_id", "source", "occurred_at", "session_id", "mode", "content", "actor", "mentioned_entities", "reply_to", "raw_metadata"}),
    "iris_memory.cognitive.contracts.RuntimeMemoryView": frozenset({"memory_id", "raw_content", "content", "subject", "perspective", "provenance"}),
    "iris_memory.cognitive.contracts.ShadowComparison": frozenset({"cognitive_would_participate", "cognitive_would_reply", "cognitive_exit_reason", "legacy_replied", "legacy_output_present", "divergence"}),
    "iris_memory.cognitive.contracts.Situation": frozenset({"episode_id", "shared_focus_type", "shared_focus_summary", "mode", "active_entities", "current_topic", "self_already_spoke", "self_last_action", "self_last_action_at", "unresolved_items", "updated_at"}),
    "iris_memory.cognitive.contracts.SituationFull": frozenset({"experience", "lite", "runtime_memory_view", "committed_affect", "committed_relationship", "behavioral_prior", "persona_read_only"}),
    "iris_memory.cognitive.contracts.SituationLite": frozenset({"scope_id", "channel", "active_entities", "reply_chain", "recent_self_action", "self_recently_spoke", "current_topic_hint", "message_velocity", "last_activity_at", "ongoing_episode_hint", "last_self_action_at"}),
    "iris_memory.cognitive.contracts.TriggerDecision": frozenset({"should_start_loop", "reason", "score", "exit_reason"}),
    "iris_memory.cognitive.contracts.TriggerSnapshot": frozenset({"previous_committed_state", "experience", "situation", "legacy_signals"}),
})
_P1_FACT_ROOT_TYPES: Mapping[str, str] = MappingProxyType({
    "EPISODE_EVENT": "iris_memory.cognitive.episode.EpisodeEventRef",
    "OUTCOME_OBSERVATION": "iris_memory.cognitive.outcome.OutcomeObservation",
    "BEHAVIOR_TRACE": "iris_memory.cognitive.contracts.BehaviorTrace",
    "HOST_RESULT": "iris_memory.cognitive.contracts.BehaviorExecutionRecord",
})


def _p1_type_registry() -> Mapping[str, type[object]]:
    """Exact frozen P1 dataclass roots accepted by Review snapshot replay.

    This table is intentionally explicit.  Reflection is used only to apply the
    declared field annotations of these named frozen contracts; it never makes
    an unknown runtime type part of the historical wire format.
    """
    from . import contracts as p1_contracts
    from .episode import Episode, EpisodeEventRef
    from .outcome import OutcomeObservation

    values = (
        Episode,
        EpisodeEventRef,
        OutcomeObservation,
        p1_contracts.EntityReference,
        p1_contracts.BehaviorExecutionRecord,
        p1_contracts.BehaviorLoopResult,
        p1_contracts.BehaviorTrace,
        p1_contracts.CanonicalEntity,
        p1_contracts.CanonicalExperience,
        p1_contracts.EventExecutionContext,
        p1_contracts.GroundingResult,
        p1_contracts.HostResult,
        p1_contracts.IdentityClaim,
        p1_contracts.IdentityConfig,
        p1_contracts.Intent,
        p1_contracts.IrisPreprocessResult,
        p1_contracts.LegacyProactiveSignals,
        p1_contracts.ParticipationResult,
        p1_contracts.RealizerRequest,
        p1_contracts.ResolvedEvent,
        p1_contracts.RuntimeMemoryView,
        p1_contracts.ShadowComparison,
        p1_contracts.Situation,
        p1_contracts.SituationFull,
        p1_contracts.SituationLite,
        p1_contracts.TriggerDecision,
        p1_contracts.TriggerSnapshot,
    )
    registry = {f"{value.__module__}.{value.__qualname__}": value for value in values}
    if set(registry) != set(_P1_SNAPSHOT_TYPE_FIELDS):
        raise PromotionInfrastructureIntegrityError("P1 replay type table drifted from the frozen snapshot profile")
    return MappingProxyType(registry)


def _decode_p1_untyped(value: object, registry: Mapping[str, type[object]]) -> object:
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise PromotionInfrastructureIntegrityError("non-finite P1 snapshot value")
        return value
    if type(value) is list:
        return tuple(_decode_p1_untyped(item, registry) for item in value)
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        raise PromotionInfrastructureIntegrityError("invalid P1 canonical value")
    if "__type__" in value or "fields" in value:
        type_name = value.get("__type__")
        contract = registry.get(type_name) if type(type_name) is str else None
        if contract is None:
            raise PromotionInfrastructureIntegrityError("unsupported P1 canonical dataclass type")
        return _decode_p1_dataclass(value, contract, registry)
    return MappingProxyType({key: _decode_p1_untyped(item, registry) for key, item in value.items()})


def _decode_p1_annotated(
    value: object,
    annotation: object,
    registry: Mapping[str, type[object]],
) -> object:
    if annotation is Any or annotation is object:
        return _decode_p1_untyped(value, registry)
    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin in (Union, UnionType):
        if value is None and type(None) in arguments:
            return None
        options = tuple(item for item in arguments if item is not type(None))
        if len(options) != 1:
            raise PromotionInfrastructureIntegrityError("unsupported P1 union annotation")
        return _decode_p1_annotated(value, options[0], registry)
    if annotation is datetime:
        if type(value) is not str:
            raise PromotionInfrastructureIntegrityError("P1 datetime must use its canonical string form")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise PromotionInfrastructureIntegrityError("invalid P1 datetime") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise PromotionInfrastructureIntegrityError("P1 datetime must be timezone-aware")
        return parsed
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        if type(value) is not str:
            raise PromotionInfrastructureIntegrityError("P1 enum must use its value string")
        try:
            return annotation(value)
        except ValueError as exc:
            raise PromotionInfrastructureIntegrityError("unknown P1 enum value") from exc
    if isinstance(annotation, type) and is_dataclass(annotation):
        return _decode_p1_dataclass(value, annotation, registry)
    if origin is tuple:
        if type(value) is not list:
            raise PromotionInfrastructureIntegrityError("P1 tuple must use a canonical JSON array")
        if len(arguments) == 2 and arguments[1] is Ellipsis:
            return tuple(_decode_p1_annotated(item, arguments[0], registry) for item in value)
        if len(arguments) != len(value):
            raise PromotionInfrastructureIntegrityError("P1 fixed tuple length mismatch")
        return tuple(_decode_p1_annotated(item, item_type, registry) for item, item_type in zip(value, arguments))
    if origin in (dict, Mapping, ABCMapping):
        if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
            raise PromotionInfrastructureIntegrityError("P1 mapping must have string keys")
        key_type, item_type = arguments or (str, Any)
        if key_type is not str:
            raise PromotionInfrastructureIntegrityError("unsupported P1 mapping key contract")
        return MappingProxyType({key: _decode_p1_annotated(item, item_type, registry) for key, item in value.items()})
    if origin in (list, set, frozenset):
        if type(value) is not list:
            raise PromotionInfrastructureIntegrityError("P1 sequence must use a canonical JSON array")
        item_type = arguments[0] if arguments else Any
        decoded = tuple(_decode_p1_annotated(item, item_type, registry) for item in value)
        if origin is list:
            return list(decoded)
        return frozenset(decoded) if origin is frozenset else set(decoded)
    if annotation is str:
        if type(value) is not str:
            raise PromotionInfrastructureIntegrityError("invalid P1 string field")
        return value
    if annotation is bool:
        if type(value) is not bool:
            raise PromotionInfrastructureIntegrityError("invalid P1 boolean field")
        return value
    if annotation is int:
        if type(value) is not int:
            raise PromotionInfrastructureIntegrityError("invalid P1 integer field")
        return value
    if annotation is float:
        if type(value) not in (int, float) or type(value) is bool or not math.isfinite(value):
            raise PromotionInfrastructureIntegrityError("invalid P1 numeric field")
        return value
    raise PromotionInfrastructureIntegrityError(f"unsupported P1 field annotation: {annotation!r}")


def _decode_p1_dataclass(
    value: object,
    expected_type: type[object],
    registry: Mapping[str, type[object]],
) -> object:
    type_name = f"{expected_type.__module__}.{expected_type.__qualname__}"
    if not isinstance(value, Mapping) or set(value) != {"__type__", "fields"}:
        raise PromotionInfrastructureIntegrityError("invalid P1 snapshot dataclass envelope")
    raw_fields = value.get("fields")
    expected_names = _P1_SNAPSHOT_TYPE_FIELDS.get(type_name)
    actual_names = frozenset(field.name for field in dataclass_fields(expected_type))
    if (
        value.get("__type__") != type_name
        or expected_names is None
        or actual_names != expected_names
        or not isinstance(raw_fields, Mapping)
        or set(raw_fields) != expected_names
    ):
        raise PromotionInfrastructureIntegrityError("P1 snapshot dataclass shape mismatch")
    try:
        annotations = get_type_hints(expected_type)
        decoded = {
            field.name: _decode_p1_annotated(raw_fields[field.name], annotations[field.name], registry)
            for field in dataclass_fields(expected_type)
        }
        instance = expected_type(**{
            field.name: decoded[field.name]
            for field in dataclass_fields(expected_type)
            if field.init
        })
        for field in dataclass_fields(expected_type):
            if not field.init:
                object.__setattr__(instance, field.name, decoded[field.name])
    except PromotionInfrastructureIntegrityError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise PromotionInfrastructureIntegrityError(f"invalid P1 {expected_type.__name__}") from exc
    return instance


def _validated_p1_snapshot_bytes(raw: bytes, expected_episode_id: str) -> Mapping[str, object]:
    """Strictly validate the archived, already-detached P1 snapshot authority.

    This never opens an EpisodeStore or OutcomeStore.  It accepts only the
    canonical P1 snapshot wire representation and proves byte-for-byte
    canonicality with the P1 serializer before P2 indexes it.
    """
    try:
        text = raw.decode("utf-8", "strict")
        parsed = _strict_json_loads(text, context="P1 snapshot")
    except UnicodeDecodeError as exc:
        raise PromotionInfrastructureIntegrityError("invalid P1 snapshot JSON") from exc
    if not isinstance(parsed, Mapping) or set(parsed) != _P1_SNAPSHOT_ROOT_FIELDS:
        raise PromotionInfrastructureIntegrityError("P1 snapshot root shape mismatch")
    if parsed.get("schema_version") != "p1d.review-input-snapshot.v1":
        raise PromotionInfrastructureIntegrityError("unknown P1 snapshot schema")
    registry = _p1_type_registry()
    try:
        episode = _decode_p1_dataclass(
            parsed["episode"], registry["iris_memory.cognitive.episode.Episode"], registry,
        )
        if getattr(episode, "episode_id", None) != expected_episode_id:
            raise PromotionInfrastructureIntegrityError("P1 snapshot Episode identity mismatch")
        if type(parsed["outcomes"]) is not list or type(parsed["fact_envelopes"]) is not list:
            raise PromotionInfrastructureIntegrityError("P1 snapshot sequences must be canonical JSON arrays")
        outcomes = tuple(
            _decode_p1_dataclass(
                item, registry["iris_memory.cognitive.outcome.OutcomeObservation"], registry,
            )
            for item in parsed["outcomes"]
        )
        facts: dict[tuple[EvidenceSourceType, str], object] = {}
        for envelope in parsed["fact_envelopes"]:
            if not isinstance(envelope, Mapping) or set(envelope) != {"source_type", "ref_id", "schema_version", "payload"}:
                raise PromotionInfrastructureIntegrityError("P1 fact envelope shape mismatch")
            source_name, ref_id = envelope.get("source_type"), envelope.get("ref_id")
            if type(source_name) is not str or type(ref_id) is not str or not ref_id:
                raise PromotionInfrastructureIntegrityError("P1 fact envelope identity mismatch")
            if envelope.get("schema_version") != "p1d.fact-envelope.v1" or source_name not in _P1_FACT_ROOT_TYPES:
                raise PromotionInfrastructureIntegrityError("P1 fact envelope schema/source mismatch")
            source_type = EvidenceSourceType(source_name)
            contract = registry[_P1_FACT_ROOT_TYPES[source_name]]
            key = (source_type, ref_id)
            if key in facts:
                raise PromotionInfrastructureIntegrityError("duplicate P1 fact envelope identity")
            facts[key] = _decode_p1_dataclass(envelope["payload"], contract, registry)
        reconstructed = ReviewInputSnapshot(episode, outcomes, facts)
    except PromotionInfrastructureIntegrityError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise PromotionInfrastructureIntegrityError("P1 snapshot violates the frozen ReviewInputSnapshot contract") from exc
    canonical = _canonical_json(reconstructed.canonical_payload()).encode("utf-8")
    if canonical != raw:
        raise PromotionInfrastructureIntegrityError("P1 snapshot bytes are not exact canonical JSON")
    validated = _deep_immutable(json.loads(canonical.decode("utf-8")))
    if not isinstance(validated, Mapping):  # pragma: no cover - root was checked above
        raise PromotionInfrastructureIntegrityError("invalid reconstructed P1 snapshot")
    return validated


class P2PromotionStore:
    """Append-only P2 persistence with immutable committed-transaction indexes.

    A logical operation becomes authority only when an exact PREPARE record has
    been followed by its exact COMMIT marker.  Cleanup rollback is deliberately
    not part of this correctness argument: a residual PREPARE is always
    incomplete and never materializes profile, Run, Evidence, or Receipt state.
    """

    STORE_SCHEMA = "p2a.promotion-store.v1"

    def __init__(self, path: str | Path) -> None:
        self._initialize(path, root="production", synthetic_enabled=False)

    def _initialize(self, path: str | Path, *, root: str, synthetic_enabled: bool) -> None:
        self.path = Path(path)
        self._synthetic_enabled = synthetic_enabled
        self.root = root
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._profiles: dict[str, Mapping[str, object]] = {}
        self._profile_objects: dict[str, FrozenCanonicalArtifactEncodingProfileV1] = {}
        self._profile_humans: dict[tuple[str, str], str] = {}
        self._run_commits: dict[str, Mapping[str, object]] = {}
        self._evidence_commits: dict[str, Mapping[str, object]] = {}
        self._prepared_transactions: dict[str, Mapping[str, object]] = {}
        self._committed_transactions: dict[str, Mapping[str, object]] = {}
        self._replay()

    def _prepare(self, operation: str, payload: Mapping[str, object]) -> Mapping[str, object]:
        if type(operation) is not str or not isinstance(payload, Mapping):
            raise PromotionInfrastructureIntegrityError("P2 transaction requires operation and mapping payload")
        payload_hash = CanonicalHashV1.hash("p2a:persistence-transaction-payload:v1", payload)
        identity = {
            "transaction_schema": _P2_TRANSACTION_SCHEMA,
            "persistence_root": self.root,
            "operation": operation,
            "payload_hash": payload_hash,
        }
        transaction_id = "tx:p2a:" + CanonicalHashV1.digest("p2a:persistence-transaction-identity:v1", identity)
        body = {
            "schema_version": self.STORE_SCHEMA,
            "persistence_root": self.root,
            "record_type": P2_TX_PREPARE,
            "transaction_schema": _P2_TRANSACTION_SCHEMA,
            "transaction_id": transaction_id,
            "operation": operation,
            "payload": payload,
            "payload_hash": payload_hash,
        }
        frozen = _deep_immutable({
            **body,
            "prepare_hash": CanonicalHashV1.hash("p2a:persistence-transaction-prepare:v1", body),
        })
        if not isinstance(frozen, Mapping):  # pragma: no cover - defensive guard
            raise PromotionInfrastructureIntegrityError("transaction PREPARE must be a mapping")
        return frozen

    def _commit(self, prepare: Mapping[str, object]) -> Mapping[str, object]:
        prepared = self._validate_prepare(prepare)
        body = {
            "schema_version": self.STORE_SCHEMA,
            "persistence_root": self.root,
            "record_type": P2_TX_COMMIT,
            "transaction_schema": _P2_TRANSACTION_SCHEMA,
            "transaction_id": prepared["transaction_id"],
            "prepare_hash": prepared["prepare_hash"],
        }
        frozen = _deep_immutable({
            **body,
            "commit_hash": CanonicalHashV1.hash("p2a:persistence-transaction-commit:v1", body),
        })
        if not isinstance(frozen, Mapping):  # pragma: no cover - defensive guard
            raise PromotionInfrastructureIntegrityError("transaction COMMIT must be a mapping")
        return frozen

    def _append(self, record: Mapping[str, object], *, stage: str) -> None:
        """Append one transaction record through its declared durability point.

        Rollback is storage hygiene only.  A failed PREPARE has no matching
        COMMIT and therefore remains non-authoritative even if cleanup itself
        fails.  A COMMIT-stage I/O error is explicitly *indeterminate*: its
        bytes may have reached storage, so callers must not treat it as a
        definitive non-commit.
        """
        if stage not in (P2_TX_PREPARE, P2_TX_COMMIT):
            raise PromotionInfrastructureIntegrityError("unknown transaction append stage")
        encoded = CanonicalHashV1.canonical_json_utf8(record) + b"\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            start = handle.tell()
            try:
                written = self._write_append_bytes(handle, encoded)
                if written != len(encoded):
                    raise OSError("short P2 append write")
                self._flush_append_handle(handle)
                self._sync_append_handle(handle)
            except Exception as exc:
                try:
                    handle.seek(start)
                    handle.truncate(start)
                    handle.flush()
                    os.fsync(handle.fileno())
                except Exception:
                    # Cleanup is intentionally not correctness.  A remaining
                    # PREPARE cannot materialize authority; a remaining COMMIT
                    # has an explicitly indeterminate caller outcome.
                    pass
                if stage == P2_TX_COMMIT:
                    raise CommitOutcomeIndeterminateError(COMMIT_OUTCOME_INDETERMINATE) from exc
                raise

    @staticmethod
    def _write_append_bytes(handle: Any, encoded: bytes) -> int:
        return handle.write(encoded)

    @staticmethod
    def _flush_append_handle(handle: Any) -> None:
        handle.flush()

    @staticmethod
    def _sync_append_handle(handle: Any) -> None:
        os.fsync(handle.fileno())

    def _scratch(self) -> "P2PromotionStore":
        """Create detached indexes for replay/preflight without touching authority."""
        scratch = object.__new__(type(self))
        scratch.path = self.path
        scratch._synthetic_enabled = self._synthetic_enabled
        scratch.root = self.root
        scratch._profiles = dict(self._profiles)
        scratch._profile_objects = dict(self._profile_objects)
        scratch._profile_humans = dict(self._profile_humans)
        scratch._run_commits = dict(self._run_commits)
        scratch._evidence_commits = dict(self._evidence_commits)
        scratch._prepared_transactions = dict(self._prepared_transactions)
        scratch._committed_transactions = dict(self._committed_transactions)
        return scratch

    def _publish_from(self, candidate: "P2PromotionStore") -> None:
        self._profiles = candidate._profiles
        self._profile_objects = candidate._profile_objects
        self._profile_humans = candidate._profile_humans
        self._run_commits = candidate._run_commits
        self._evidence_commits = candidate._evidence_commits
        self._prepared_transactions = candidate._prepared_transactions
        self._committed_transactions = candidate._committed_transactions

    def _preflight(self, prepare: Mapping[str, object], commit: Mapping[str, object]) -> "P2PromotionStore":
        candidate = self._scratch()
        candidate._apply(prepare)
        candidate._apply(commit)
        return candidate

    def _record_transaction(self, operation: str, payload: Mapping[str, object]) -> None:
        prepare = self._prepare(operation, payload)
        commit = self._commit(prepare)
        candidate = self._preflight(prepare, commit)
        self._append(prepare, stage=P2_TX_PREPARE)
        self._append(commit, stage=P2_TX_COMMIT)
        self._publish_from(candidate)

    def _replay(self) -> None:
        if not self.path.exists():
            return
        candidate = self._scratch()
        with self.path.open("rb") as handle:
            for number, raw_line in enumerate(handle, 1):
                if not raw_line.strip():
                    raise PromotionInfrastructureIntegrityError(f"blank P2 record at line {number}")
                try:
                    if not raw_line.endswith(b"\n"):
                        raise PromotionInfrastructureIntegrityError("record has no complete newline terminator")
                    line = raw_line[:-1].decode("utf-8", "strict")
                    record = _strict_json_loads(line, context=f"P2 record line {number}")
                except (UnicodeDecodeError, PromotionInfrastructureIntegrityError) as exc:
                    raise PromotionInfrastructureIntegrityError(f"malformed or truncated P2 record at line {number}") from exc
                if CanonicalHashV1.canonical_json_utf8(record) + b"\n" != raw_line:
                    raise PromotionInfrastructureIntegrityError(f"non-canonical P2 record bytes at line {number}")
                candidate._apply(record)
        self._publish_from(candidate)

    def _apply(self, record: object) -> None:
        if not isinstance(record, Mapping) or type(record.get("record_type")) is not str:
            raise PromotionInfrastructureIntegrityError("unknown P2 transaction record shape")
        if record["record_type"] == P2_TX_PREPARE:
            self._apply_prepare(record)
            return
        if record["record_type"] == P2_TX_COMMIT:
            self._apply_commit(record)
            return
        raise PromotionInfrastructureIntegrityError("unknown P2 transaction record")

    @staticmethod
    def _same_record(left: Mapping[str, object], right: Mapping[str, object]) -> bool:
        return CanonicalHashV1.canonical_json_utf8(left) == CanonicalHashV1.canonical_json_utf8(right)

    def _validate_prepare(self, record: Mapping[str, object]) -> Mapping[str, object]:
        expected = {
            "schema_version", "persistence_root", "record_type", "transaction_schema", "transaction_id",
            "operation", "payload", "payload_hash", "prepare_hash",
        }
        if set(record) != expected:
            raise PromotionInfrastructureIntegrityError("unknown P2 PREPARE record shape")
        if record["schema_version"] != self.STORE_SCHEMA or record["persistence_root"] != self.root:
            raise PromotionInfrastructureIntegrityError("P2 PREPARE schema/root mismatch")
        if record["record_type"] != P2_TX_PREPARE or record["transaction_schema"] != _P2_TRANSACTION_SCHEMA:
            raise PromotionInfrastructureIntegrityError("unsupported P2 PREPARE schema")
        if type(record["operation"]) is not str or not isinstance(record["payload"], Mapping):
            raise PromotionInfrastructureIntegrityError("invalid P2 PREPARE logical operation")
        if not _is_p2_identifier(record["transaction_id"], "tx:p2a:"):
            raise PromotionInfrastructureIntegrityError("invalid P2 transaction identity")
        payload_hash = CanonicalHashV1.hash("p2a:persistence-transaction-payload:v1", record["payload"])
        if record["payload_hash"] != payload_hash:
            raise PromotionInfrastructureIntegrityError("P2 PREPARE payload hash mismatch")
        identity = {
            "transaction_schema": record["transaction_schema"],
            "persistence_root": record["persistence_root"],
            "operation": record["operation"],
            "payload_hash": payload_hash,
        }
        expected_id = "tx:p2a:" + CanonicalHashV1.digest("p2a:persistence-transaction-identity:v1", identity)
        if record["transaction_id"] != expected_id:
            raise PromotionInfrastructureIntegrityError("P2 PREPARE transaction identity mismatch")
        body = {key: record[key] for key in expected if key != "prepare_hash"}
        if record["prepare_hash"] != CanonicalHashV1.hash("p2a:persistence-transaction-prepare:v1", body):
            raise PromotionInfrastructureIntegrityError("P2 PREPARE hash mismatch")
        return record

    def _apply_prepare(self, record: Mapping[str, object]) -> None:
        prepare = self._validate_prepare(record)
        transaction_id = prepare["transaction_id"]
        prior = self._prepared_transactions.get(transaction_id)
        if prior is not None and not self._same_record(prior, prepare):
            raise PromotionInfrastructureIntegrityError("P2 transaction identity conflicts with different PREPARE")
        self._prepared_transactions.setdefault(transaction_id, _deep_immutable(prepare))

    def _validate_commit(self, record: Mapping[str, object]) -> Mapping[str, object]:
        expected = {
            "schema_version", "persistence_root", "record_type", "transaction_schema", "transaction_id",
            "prepare_hash", "commit_hash",
        }
        if set(record) != expected:
            raise PromotionInfrastructureIntegrityError("unknown P2 COMMIT record shape")
        if record["schema_version"] != self.STORE_SCHEMA or record["persistence_root"] != self.root:
            raise PromotionInfrastructureIntegrityError("P2 COMMIT schema/root mismatch")
        if record["record_type"] != P2_TX_COMMIT or record["transaction_schema"] != _P2_TRANSACTION_SCHEMA:
            raise PromotionInfrastructureIntegrityError("unsupported P2 COMMIT schema")
        if not _is_p2_identifier(record["transaction_id"], "tx:p2a:") or type(record["prepare_hash"]) is not str:
            raise PromotionInfrastructureIntegrityError("invalid P2 COMMIT identity")
        body = {key: record[key] for key in expected if key != "commit_hash"}
        if record["commit_hash"] != CanonicalHashV1.hash("p2a:persistence-transaction-commit:v1", body):
            raise PromotionInfrastructureIntegrityError("P2 COMMIT hash mismatch")
        return record

    def _apply_commit(self, record: Mapping[str, object]) -> None:
        commit = self._validate_commit(record)
        transaction_id = commit["transaction_id"]
        prior_commit = self._committed_transactions.get(transaction_id)
        if prior_commit is not None:
            if not self._same_record(prior_commit, commit):
                raise PromotionInfrastructureIntegrityError("P2 transaction identity conflicts with different COMMIT")
            return
        prepare = self._prepared_transactions.get(transaction_id)
        if prepare is None:
            raise PromotionInfrastructureIntegrityError("P2 COMMIT has no prior PREPARE")
        if commit["prepare_hash"] != prepare["prepare_hash"]:
            raise PromotionInfrastructureIntegrityError("P2 COMMIT references the wrong PREPARE")
        self._apply_logical_operation(prepare["operation"], prepare["payload"])
        self._committed_transactions[transaction_id] = _deep_immutable(commit)

    def _apply_logical_operation(self, operation: object, payload: object) -> None:
        if not isinstance(payload, Mapping):
            raise PromotionInfrastructureIntegrityError("P2 operation payload must be a mapping")
        if operation == P2_CANONICAL_ARTIFACT_ENCODING_PROFILE:
            self._apply_profile(payload)
        elif operation == P2_REVIEW_RUN_WITH_SNAPSHOT:
            self._apply_run_commit(payload)
        elif operation == P2_PROMOTED_EVIDENCE_COMMIT:
            if self.root != "production" and not self._synthetic_enabled:
                raise PromotionInfrastructureIntegrityError(PRODUCTION_RULE_DISABLED)
            self._apply_evidence_commit(payload)
        else:
            raise PromotionInfrastructureIntegrityError("unknown P2 operation")

    @staticmethod
    def _fields(payload: Mapping[str, object], type_name: str) -> Mapping[str, object]:
        return _strict_envelope(payload, type_name)

    def _idempotent(self, index: dict[str, Mapping[str, object]], identity: str, payload: Mapping[str, object], message: str) -> None:
        frozen = _deep_immutable(payload)
        if not isinstance(frozen, Mapping):  # pragma: no cover - defensive guard
            raise PromotionInfrastructureIntegrityError("indexed artifact must be a mapping")
        prior = index.get(identity)
        if prior is not None and CanonicalHashV1.canonical_json_utf8(prior) != CanonicalHashV1.canonical_json_utf8(frozen):
            raise PromotionInfrastructureIntegrityError(message)
        index.setdefault(identity, frozen)

    def _apply_profile(self, payload: Mapping[str, object]) -> None:
        fields = self._fields(payload, "FrozenCanonicalArtifactEncodingProfileV1")
        if fields["schema_version"] != "p2a.canonical-artifact-encoding-profile.v1":
            raise PromotionInfrastructureIntegrityError("unknown encoding profile schema")
        profiles = fields["type_profiles"]
        if type(profiles) not in (tuple, list) or len(profiles) != len(_TYPE_FIELDS):
            raise PromotionInfrastructureIntegrityError("encoding profile type table mismatch")
        profile_values: list[CanonicalTypeProfileV1] = []
        for item in profiles:
            if not isinstance(item, Mapping):
                raise PromotionInfrastructureIntegrityError("invalid encoding profile type entry")
            item_fields = self._fields(item, "CanonicalTypeProfileV1")
            name = item_fields["type_name"]
            if type(name) is not str or name not in _TYPE_FIELDS:
                raise PromotionInfrastructureIntegrityError("unknown encoding profile type/schema")
            if item_fields["schema"] != _TYPE_SCHEMAS[name] or type(item_fields["fields"]) not in (tuple, list) or tuple(item_fields["fields"]) != _TYPE_FIELDS[name]:
                raise PromotionInfrastructureIntegrityError("encoding profile schema or field table mismatch")
            profile_values.append(CanonicalTypeProfileV1(name, item_fields["schema"], tuple(item_fields["fields"])))
        if {item.type_name for item in profile_values} != set(_TYPE_FIELDS):
            raise PromotionInfrastructureIntegrityError("encoding profile does not define the exact v1 type set")
        try:
            profile = FrozenCanonicalArtifactEncodingProfileV1(
                _string(fields["profile_id"]), _string(fields["profile_version"]), tuple(profile_values), _string(fields["schema_version"])
            )
            encoder = P2CanonicalArtifactEncoderV1(profile)
        except (TypeError, ValueError) as exc:
            raise PromotionInfrastructureIntegrityError("invalid encoding profile") from exc
        profile_hash = encoder.profile_hash
        if CanonicalHashV1.canonical_json_utf8(encoder.encode(profile)) != CanonicalHashV1.canonical_json_utf8(payload):
            raise PromotionInfrastructureIntegrityError("encoding profile is not canonical under its exact table")
        human = (profile.profile_id, profile.profile_version)
        previous_hash = self._profile_humans.get(human)
        if previous_hash is not None and previous_hash != profile_hash:
            raise PromotionInfrastructureIntegrityError("encoding profile identity conflicts with different content")
        self._idempotent(self._profiles, profile_hash, payload, "encoding profile hash conflicts with different payload")
        self._profile_objects.setdefault(profile_hash, profile)
        self._profile_humans[human] = profile_hash

    def _apply_run_commit(self, payload: Mapping[str, object]) -> None:
        fields = self._fields(payload, "P2ReviewRunWithSnapshotV1")
        run = self._fields(fields["run"], "ReviewRun")
        archive = self._fields(fields["archive"], "PromotionSnapshotArchiveV1")
        if fields["schema_version"] != "p2a.review-run-with-snapshot.v1":
            raise PromotionInfrastructureIntegrityError("unknown run/snapshot schema")
        if archive["schema_version"] != "p2a.promotion-snapshot-archive.v1" or run["schema_version"] != "1":
            raise PromotionInfrastructureIntegrityError("unknown nested run/archive schema")
        if fields["encoding_profile_hash"] not in self._profiles:
            raise PromotionInfrastructureIntegrityError("run/snapshot references unknown encoding profile")
        profile = self._profile_objects[fields["encoding_profile_hash"]]
        encoder = P2CanonicalArtifactEncoderV1(profile)
        authoritative_run = _decode_run(fields["run"])
        if CanonicalHashV1.canonical_json_utf8(encoder.encode(authoritative_run)) != CanonicalHashV1.canonical_json_utf8(fields["run"]):
            raise PromotionInfrastructureIntegrityError("ReviewRun is not canonical under exact historical profile")
        raw_bytes = archive["canonical_snapshot_json_utf8"]
        if not isinstance(raw_bytes, Mapping) or set(raw_bytes) != {"$bytes"} or type(raw_bytes["$bytes"]) is not str:
            raise PromotionInfrastructureIntegrityError("invalid snapshot archive bytes")
        raw = _decode_unpadded_base64url(raw_bytes["$bytes"])
        if run["review_run_id"] != archive["review_run_id"] or run["episode_id"] != archive["episode_id"]:
            raise PromotionInfrastructureIntegrityError("run/archive identity mismatch")
        _validated_p1_snapshot_bytes(raw, authoritative_run.episode_id)
        p1_hash = "sha256:" + hashlib.sha256(raw).hexdigest()
        if run["input_snapshot_hash"] != p1_hash or archive["input_snapshot_hash"] != p1_hash:
            raise PromotionInfrastructureIntegrityError("run/archive P1 snapshot hash mismatch")
        archive_body = {key: archive[key] for key in _TYPE_FIELDS["PromotionSnapshotArchiveV1"] if key != "archive_payload_hash"}
        if archive["archive_payload_hash"] != CanonicalHashV1.hash("p2a:promotion-snapshot-archive:v1", archive_body):
            raise PromotionInfrastructureIntegrityError("archive payload hash mismatch")
        body = {
            "schema_version": fields["schema_version"], "run": fields["run"], "archive": fields["archive"],
            "encoding_profile_hash": fields["encoding_profile_hash"],
        }
        if fields["logical_commit_hash"] != CanonicalHashV1.hash("p2a:review-run-with-snapshot:v1", body):
            raise PromotionInfrastructureIntegrityError("run/snapshot logical commit mismatch")
        self._idempotent(self._run_commits, authoritative_run.review_run_id, payload, "run/snapshot identity conflicts with different payload")

    def _apply_evidence_commit(self, payload: Mapping[str, object]) -> None:
        fields = self._fields(payload, "P2PromotedEvidenceCommitV1")
        evidence = self._fields(fields["evidence"], "ReviewEvidence")
        receipt = self._fields(fields["receipt"], "PromotionReceiptV1")
        if fields["schema_version"] != "p2a.promoted-evidence-commit.v1":
            raise PromotionInfrastructureIntegrityError("unknown evidence commit schema")
        if evidence["schema_version"] != "1" or receipt["schema_version"] != "p2a.promotion-receipt.v1":
            raise PromotionInfrastructureIntegrityError("unknown nested evidence/receipt schema")
        profile_hash = fields["encoding_profile_hash"]
        if profile_hash not in self._profiles:
            raise PromotionInfrastructureIntegrityError("evidence commit references unknown encoding profile")
        profile = self._profile_objects[profile_hash]
        encoder = P2CanonicalArtifactEncoderV1(profile)
        authoritative_evidence = _decode_evidence(fields["evidence"])
        if (
            self.root == "production"
            and (
                authoritative_evidence.producer
                != EXPLICIT_CORRECTION_OF_EXACT_HOST_OUTPUT_V1
                or f"rule:{EXPLICIT_CORRECTION_OF_EXACT_HOST_OUTPUT_V1}"
                not in authoritative_evidence.provenance
            )
        ):
            raise PromotionInfrastructureIntegrityError(PRODUCTION_RULE_DISABLED)
        if CanonicalHashV1.canonical_json_utf8(encoder.encode(authoritative_evidence)) != CanonicalHashV1.canonical_json_utf8(fields["evidence"]):
            raise PromotionInfrastructureIntegrityError("ReviewEvidence is not canonical under exact historical profile")
        archived_run_commit = self._run_commits.get(evidence["source_review_run_id"])
        if archived_run_commit is None:
            raise PromotionInfrastructureIntegrityError("evidence commit references a Run without exact P2 archive")
        archived_run_commit_fields = self._fields(archived_run_commit, "P2ReviewRunWithSnapshotV1")
        authoritative_run = _decode_run(archived_run_commit_fields["run"])
        if archived_run_commit_fields["encoding_profile_hash"] != profile_hash:
            raise PromotionInfrastructureIntegrityError("evidence/run encoding profile lineage mismatch")
        if authoritative_run.episode_id != authoritative_evidence.source_episode_id:
            raise PromotionInfrastructureIntegrityError("evidence/Run Episode lineage mismatch")
        matching_findings = [candidate for candidate in authoritative_run.findings if candidate.finding_id == authoritative_evidence.source_finding_id]
        if len(matching_findings) != 1:
            raise PromotionInfrastructureIntegrityError("evidence source Finding is not contained in archived ReviewRun")
        authoritative_finding = matching_findings[0]
        if (
            authoritative_finding.review_run_id != authoritative_evidence.source_review_run_id
            or authoritative_finding.episode_id != authoritative_evidence.source_episode_id
            or authoritative_evidence.source_review_run_id != authoritative_run.review_run_id
            or authoritative_evidence.created_at != authoritative_finding.created_at
        ):
            raise PromotionInfrastructureIntegrityError("evidence/Finding lineage mismatch")
        semantic = EvidenceSemanticBodyV1.from_evidence(authoritative_evidence, encoder)
        derived_evidence_id = EvidenceIdentityBodyV1(
            "p2a.evidence-identity.v1", semantic.semantic_body_hash, profile_hash
        ).evidence_id()
        if authoritative_evidence.evidence_id != derived_evidence_id or not _is_p2_identifier(authoritative_evidence.evidence_id, "evidence:p2a:"):
            raise PromotionInfrastructureIntegrityError("evidence identity is not derived from semantic body")
        evidence_hash = CanonicalHashV1.hash("p2a:review-evidence-payload:v1", encoder.encode(authoritative_evidence))
        if receipt["evidence_id"] != authoritative_evidence.evidence_id or receipt["evidence_payload_hash"] != evidence_hash:
            raise PromotionInfrastructureIntegrityError("receipt/evidence binding mismatch")
        expected_receipt = "receipt:p2a:" + CanonicalHashV1.digest("p2a:receipt-identity:v1", {
            "schema_version": "p2a.receipt-identity.v1", "evidence_id": authoritative_evidence.evidence_id,
            "evidence_payload_hash": evidence_hash, "encoding_profile_hash": profile_hash,
        })
        if receipt["receipt_id"] != expected_receipt or receipt["encoding_profile_hash"] != profile_hash:
            raise PromotionInfrastructureIntegrityError("receipt identity mismatch")
        body = {"schema_version": fields["schema_version"], "evidence": fields["evidence"], "receipt": fields["receipt"], "encoding_profile_hash": profile_hash}
        if fields["logical_commit_hash"] != CanonicalHashV1.hash("p2a:promoted-evidence-commit:v1", body):
            raise PromotionInfrastructureIntegrityError("evidence logical commit mismatch")
        self._idempotent(self._evidence_commits, authoritative_evidence.evidence_id, payload, "evidence identity conflicts with different payload")

    def record_encoding_profile(self, profile: FrozenCanonicalArtifactEncodingProfileV1, encoder: P2CanonicalArtifactEncoderV1) -> str:
        if type(profile) is not FrozenCanonicalArtifactEncodingProfileV1 or type(encoder) is not P2CanonicalArtifactEncoderV1:
            raise PromotionInfrastructureIntegrityError("profile registration requires exact frozen profile and encoder")
        if canonical_artifact_encoding_profile_hash(profile) != encoder.profile_hash:
            raise PromotionInfrastructureIntegrityError("encoder/profile hash mismatch")
        payload = encoder.encode(profile)
        self._record_transaction(P2_CANONICAL_ARTIFACT_ENCODING_PROFILE, payload)
        return canonical_artifact_encoding_profile_hash(profile)

    def record_run_with_snapshot(self, run: ReviewRun, snapshot: ReviewInputSnapshot, encoder: P2CanonicalArtifactEncoderV1) -> P2ReviewRunWithSnapshotV1:
        if encoder.profile_hash not in self._profiles:
            raise PromotionInfrastructureIntegrityError("exact encoding profile must be registered before P2 archive")
        commit = P2ReviewRunWithSnapshotV1.create(run, snapshot, encoder)
        commit.validate(encoder)
        payload = encoder.encode(commit)
        self._record_transaction(P2_REVIEW_RUN_WITH_SNAPSHOT, payload)
        return commit

    def _record_enabled_production_candidate(
        self,
        candidate: SyntheticPromotionCandidateV1,
        encoder: P2CanonicalArtifactEncoderV1,
        gate: ProductionPromotionGateV1,
        *,
        rule_id: str,
    ) -> P2PromotedEvidenceCommitV1:
        """Persist one candidate only through an enabled exact production rule.

        This intentionally remains an internal composition primitive.  It is
        not a generic public evidence writer: the rule owner must establish
        the factual/semantic joins before calling it.
        """
        if (
            self.root != "production"
            or self._synthetic_enabled
            or type(candidate) is not SyntheticPromotionCandidateV1
            or type(encoder) is not P2CanonicalArtifactEncoderV1
            or type(gate) is not ProductionPromotionGateV1
            or rule_id not in _P2R1_PRODUCTION_RULES
            or candidate.evidence.producer != rule_id
        ):
            raise PromotionInfrastructureIntegrityError(PRODUCTION_RULE_DISABLED)
        decision = gate.evaluate(PromotionCommand(candidate.finding.finding_id, rule_id))
        if not decision.accepted:
            raise PromotionInfrastructureIntegrityError(PRODUCTION_RULE_DISABLED)
        commit = _materialize_synthetic_commit(candidate, encoder)
        if encoder.profile_hash not in self._profiles or commit.encoding_profile_hash != encoder.profile_hash:
            raise PromotionInfrastructureIntegrityError("production commit profile is unavailable")
        commit.validate(encoder)
        self._record_transaction(P2_PROMOTED_EVIDENCE_COMMIT, encoder.encode(commit))
        return commit

    def require_archive(self, run: ReviewRun) -> Mapping[str, object]:
        record = self._run_commits.get(run.review_run_id)
        if record is None:
            raise LegacyArchiveUnavailableError("LEGACY_ARCHIVE_UNAVAILABLE")
        detached = _deep_immutable(_deep_detached_json(record))
        if not isinstance(detached, Mapping):  # pragma: no cover - defensive guard
            raise PromotionInfrastructureIntegrityError("archive record must be a mapping")
        return detached

    @property
    def evidence_commits(self) -> tuple[Mapping[str, object], ...]:
        returned: list[Mapping[str, object]] = []
        for record in self._evidence_commits.values():
            detached = _deep_immutable(_deep_detached_json(record))
            if not isinstance(detached, Mapping):  # pragma: no cover - defensive guard
                raise PromotionInfrastructureIntegrityError("Evidence commit must be a mapping")
            returned.append(detached)
        return tuple(returned)


class SyntheticP2PromotionStore(P2PromotionStore):
    """Separate test composition root; it cannot be selected on production Store."""

    def __init__(self, path: str | Path) -> None:
        self._initialize(path, root="synthetic-test", synthetic_enabled=True)

    def record_synthetic_evidence(
        self,
        candidate: SyntheticPromotionCandidateV1,
        encoder: P2CanonicalArtifactEncoderV1,
    ) -> P2PromotedEvidenceCommitV1:
        commit = _materialize_synthetic_commit(candidate, encoder)
        if encoder.profile_hash not in self._profiles or commit.encoding_profile_hash != encoder.profile_hash:
            raise PromotionInfrastructureIntegrityError("synthetic commit profile is unavailable")
        commit.validate(encoder)
        payload = encoder.encode(commit)
        self._record_transaction(P2_PROMOTED_EVIDENCE_COMMIT, payload)
        return commit


class P2ValidatedEvidenceReader:
    """Read durably committed production Evidence, never raw candidates."""

    def __init__(self, store: P2PromotionStore) -> None:
        if type(store) not in (P2PromotionStore, SyntheticP2PromotionStore):
            raise PromotionInfrastructureIntegrityError("validated reader requires a built-in P2 store")
        self._store = store

    def validated_evidence(self) -> tuple[ReviewEvidence, ...]:
        if self._store.root != "production":
            return ()
        decoded: list[ReviewEvidence] = []
        for payload in self._store.evidence_commits:
            fields = self._store._fields(payload, "P2PromotedEvidenceCommitV1")
            evidence = _decode_evidence(fields["evidence"])
            if evidence.producer not in _P2R1_PRODUCTION_RULES:
                raise PromotionInfrastructureIntegrityError("unknown production Evidence rule")
            decoded.append(evidence)
        return tuple(decoded)

    def status_for_raw_evidence(self, evidence: ReviewEvidence) -> str:
        if type(evidence) is not ReviewEvidence:
            raise PromotionInfrastructureIntegrityError("expected raw ReviewEvidence")
        return NOT_P2_VALID

    def archive_for_run(self, run: ReviewRun) -> Mapping[str, object]:
        return self._store.require_archive(run)
