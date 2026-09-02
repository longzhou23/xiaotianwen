"""ReviewStore implementations: protocol, in-memory, append-only JSONL."""

from __future__ import annotations

from datetime import datetime
import json
import logging
from pathlib import Path
from typing import Protocol, runtime_checkable
from uuid import uuid4

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
    ReviewEvidence,
    ReviewEvidenceRef,
    ReviewFinding,
    ReviewRun,
    ReviewStatus,
)

logger = logging.getLogger(__name__)


class ReviewStoreIntegrityError(ValueError):
    """An immutable Review artifact conflicts with identity or lineage."""


@runtime_checkable
class ReviewStore(Protocol):
    def record_review_run(self, run: ReviewRun) -> ReviewRun: ...
    def get_review_run(self, review_run_id: str) -> ReviewRun | None: ...
    def list_review_runs_for_episode(self, episode_id: str) -> tuple[ReviewRun, ...]: ...
    def get_finding(self, finding_id: str) -> ReviewFinding | None: ...
    def record_evidence(self, evidence: ReviewEvidence) -> ReviewEvidence: ...
    def list_evidence_for_episode(self, episode_id: str) -> tuple[ReviewEvidence, ...]: ...


def _ref_to_dict(ref: ReviewEvidenceRef) -> dict:
    return {"ref_id": ref.ref_id, "source_type": ref.source_type.value, "evidence_kind": ref.evidence_kind.value}


def _ref_from_dict(data: dict) -> ReviewEvidenceRef:
    return ReviewEvidenceRef(data["ref_id"], EvidenceSourceType(data["source_type"]), EvidenceKind(data["evidence_kind"]))


def _attr_to_dict(attr: AttributionRef) -> dict:
    return {"target_type": attr.target_type.value, "ref_id": attr.ref_id}


def _attr_from_dict(data: dict) -> AttributionRef:
    return AttributionRef(AttributionTargetType(data["target_type"]), data["ref_id"])


def _scope_to_dict(scope: BehaviorScope) -> dict:
    return {
        "channel": scope.channel,
        "directedness": scope.directedness,
        "intent_domain": scope.intent_domain,
        "topic_hint": scope.topic_hint,
        "tool_used": scope.tool_used,
    }


def _scope_from_dict(data: dict) -> BehaviorScope:
    return BehaviorScope(
        channel=data["channel"],
        directedness=data["directedness"],
        intent_domain=data.get("intent_domain"),
        topic_hint=data.get("topic_hint"),
        tool_used=data.get("tool_used"),
    )


def _prop_to_dict(prop: LocalEvidenceProposition) -> dict:
    return {
        "dimension": prop.dimension.value,
        "context_refs": list(prop.context_refs),
        "behavior_refs": list(prop.behavior_refs),
        "observation_refs": list(prop.observation_refs),
        "statement": prop.statement,
    }


def _prop_from_dict(data: dict) -> LocalEvidenceProposition:
    return LocalEvidenceProposition(
        ReviewDimension(data["dimension"]),
        tuple(data.get("context_refs") or ()),
        tuple(data.get("behavior_refs") or ()),
        tuple(data.get("observation_refs") or ()),
        data.get("statement", ""),
    )


def _finding_to_dict(f: ReviewFinding) -> dict:
    return {
        "finding_id": f.finding_id,
        "review_run_id": f.review_run_id,
        "episode_id": f.episode_id,
        "dimension": f.dimension.value,
        "finding_type": f.finding_type.value,
        "claim": f.claim,
        "evidence_refs": [_ref_to_dict(r) for r in f.evidence_refs],
        "attributed_to": _attr_to_dict(f.attributed_to),
        "confidence": f.confidence.value,
        "causal_attribution": f.causal_attribution.value,
        "interpretation_producer": f.interpretation_producer.value,
        "created_at": f.created_at.isoformat(),
        "producer": f.producer,
        "model": f.model,
        "model_version": f.model_version,
        "prompt_version": f.prompt_version,
        "provenance": list(f.provenance),
    }


def _finding_from_dict(data: dict) -> ReviewFinding:
    return ReviewFinding(
        finding_id=data["finding_id"],
        review_run_id=data["review_run_id"],
        episode_id=data["episode_id"],
        dimension=ReviewDimension(data["dimension"]),
        finding_type=FindingType(data["finding_type"]),
        claim=data["claim"],
        evidence_refs=tuple(_ref_from_dict(r) for r in data["evidence_refs"]),
        attributed_to=_attr_from_dict(data["attributed_to"]),
        confidence=Confidence(data["confidence"]),
        causal_attribution=CausalAttribution(data["causal_attribution"]),
        interpretation_producer=InterpretationProducer(data["interpretation_producer"]),
        created_at=datetime.fromisoformat(data["created_at"]),
        producer=data["producer"],
        model=data.get("model"),
        model_version=data.get("model_version"),
        prompt_version=data.get("prompt_version"),
        provenance=tuple(data.get("provenance") or ()),
    )


def _run_to_dict(run: ReviewRun) -> dict:
    return {
        "review_run_id": run.review_run_id,
        "episode_id": run.episode_id,
        "status": run.status.value,
        "input_snapshot_hash": run.input_snapshot_hash,
        "created_at": run.created_at.isoformat(),
        "model": run.model,
        "model_version": run.model_version,
        "prompt_version": run.prompt_version,
        "schema_version": run.schema_version,
        "raw_output_digest": run.raw_output_digest,
        "findings": [_finding_to_dict(f) for f in run.findings],
        "producer": run.producer,
        "provenance": list(run.provenance),
    }


def _run_from_dict(data: dict) -> ReviewRun:
    return ReviewRun(
        review_run_id=data["review_run_id"],
        episode_id=data["episode_id"],
        status=ReviewStatus(data["status"]),
        input_snapshot_hash=data["input_snapshot_hash"],
        created_at=datetime.fromisoformat(data["created_at"]),
        model=data.get("model"),
        model_version=data.get("model_version"),
        prompt_version=data.get("prompt_version"),
        schema_version=data.get("schema_version", "1"),
        raw_output_digest=data.get("raw_output_digest"),
        findings=tuple(_finding_from_dict(f) for f in data.get("findings") or ()),
        producer=data.get("producer", "review_engine"),
        provenance=tuple(data.get("provenance") or ()),
    )


def _evidence_to_dict(ev: ReviewEvidence) -> dict:
    return {
        "evidence_id": ev.evidence_id,
        "source_review_run_id": ev.source_review_run_id,
        "source_finding_id": ev.source_finding_id,
        "source_episode_id": ev.source_episode_id,
        "dimension": ev.dimension.value,
        "proposition": _prop_to_dict(ev.proposition),
        "scope": _scope_to_dict(ev.scope),
        "evidence_refs": [_ref_to_dict(r) for r in ev.evidence_refs],
        "confidence": ev.confidence.value,
        "causal_attribution": ev.causal_attribution.value,
        "attributed_to": _attr_to_dict(ev.attributed_to),
        "interpretation_producer": ev.interpretation_producer.value,
        "created_at": ev.created_at.isoformat(),
        "producer": ev.producer,
        "model": ev.model,
        "model_version": ev.model_version,
        "prompt_version": ev.prompt_version,
        "schema_version": ev.schema_version,
        "provenance": list(ev.provenance),
    }


def _evidence_from_dict(data: dict) -> ReviewEvidence:
    return ReviewEvidence(
        evidence_id=data["evidence_id"],
        source_review_run_id=data["source_review_run_id"],
        source_finding_id=data["source_finding_id"],
        source_episode_id=data["source_episode_id"],
        dimension=ReviewDimension(data["dimension"]),
        proposition=_prop_from_dict(data["proposition"]),
        scope=_scope_from_dict(data["scope"]),
        evidence_refs=tuple(_ref_from_dict(r) for r in data["evidence_refs"]),
        confidence=Confidence(data["confidence"]),
        causal_attribution=CausalAttribution(data["causal_attribution"]),
        attributed_to=_attr_from_dict(data["attributed_to"]),
        interpretation_producer=InterpretationProducer(data["interpretation_producer"]),
        created_at=datetime.fromisoformat(data["created_at"]),
        producer=data["producer"],
        model=data.get("model"),
        model_version=data.get("model_version"),
        prompt_version=data.get("prompt_version"),
        schema_version=data.get("schema_version", "1"),
        provenance=tuple(data.get("provenance") or ()),
    )


def _canonical_payload_equal(left: dict, right: dict) -> bool:
    return json.dumps(left, ensure_ascii=False, sort_keys=True, separators=(",", ":")) == json.dumps(
        right,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class InMemoryReviewStore:
    def __init__(self) -> None:
        self._runs: dict[str, ReviewRun] = {}
        self._findings: dict[str, ReviewFinding] = {}
        self._evidences: dict[str, ReviewEvidence] = {}

    def _validate_run(self, run: ReviewRun) -> None:
        finding_ids: set[str] = set()
        for finding in run.findings:
            if finding.review_run_id != run.review_run_id:
                raise ReviewStoreIntegrityError("Finding does not belong to its ReviewRun")
            if finding.episode_id != run.episode_id:
                raise ReviewStoreIntegrityError("Finding Episode does not match ReviewRun Episode")
            if finding.finding_id in finding_ids:
                raise ReviewStoreIntegrityError("duplicate Finding identity within ReviewRun")
            finding_ids.add(finding.finding_id)
            existing = self._findings.get(finding.finding_id)
            if existing is not None and existing != finding:
                raise ReviewStoreIntegrityError("Finding identity conflicts with existing immutable Finding")

    def _validate_evidence(self, evidence: ReviewEvidence) -> None:
        run = self._runs.get(evidence.source_review_run_id)
        if run is None:
            raise ReviewStoreIntegrityError("ReviewEvidence references an unknown ReviewRun")
        finding = self._findings.get(evidence.source_finding_id)
        if finding is None:
            raise ReviewStoreIntegrityError("ReviewEvidence references an unknown Finding")
        if finding.review_run_id != run.review_run_id:
            raise ReviewStoreIntegrityError("Finding does not belong to ReviewEvidence source ReviewRun")
        if finding.episode_id != evidence.source_episode_id or run.episode_id != evidence.source_episode_id:
            raise ReviewStoreIntegrityError("ReviewEvidence Episode lineage mismatch")
        if not any(candidate.finding_id == finding.finding_id and candidate == finding for candidate in run.findings):
            raise ReviewStoreIntegrityError("ReviewEvidence Finding is not contained in source ReviewRun")

    def record_review_run(self, run: ReviewRun) -> ReviewRun:
        self._validate_run(run)
        existing = self._runs.get(run.review_run_id)
        if existing is not None:
            if not _canonical_payload_equal(_run_to_dict(existing), _run_to_dict(run)):
                raise ReviewStoreIntegrityError("ReviewRun identity conflicts with different immutable payload")
            return existing
        self._runs[run.review_run_id] = run
        for finding in run.findings:
            self._findings[finding.finding_id] = finding
        return run

    def get_review_run(self, review_run_id: str) -> ReviewRun | None:
        return self._runs.get(review_run_id)

    def list_review_runs_for_episode(self, episode_id: str) -> tuple[ReviewRun, ...]:
        return tuple(run for run in self._runs.values() if run.episode_id == episode_id)

    def get_finding(self, finding_id: str) -> ReviewFinding | None:
        return self._findings.get(finding_id)

    def record_evidence(self, evidence: ReviewEvidence) -> ReviewEvidence:
        self._validate_evidence(evidence)
        existing = self._evidences.get(evidence.evidence_id)
        if existing is not None:
            if not _canonical_payload_equal(_evidence_to_dict(existing), _evidence_to_dict(evidence)):
                raise ReviewStoreIntegrityError("ReviewEvidence identity conflicts with different immutable payload")
            return existing
        self._evidences[evidence.evidence_id] = evidence
        return evidence

    def list_evidence_for_episode(self, episode_id: str) -> tuple[ReviewEvidence, ...]:
        return tuple(ev for ev in self._evidences.values() if ev.source_episode_id == episode_id)


class AppendOnlyReviewStore(InMemoryReviewStore):
    """Minimal append-only ReviewStore using full immutable snapshots."""

    SCHEMA_VERSION = 1

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._replay()

    def _append(self, operation_kind: str, payload: dict) -> None:
        record = {
            "schema_version": self.SCHEMA_VERSION,
            "operation_id": uuid4().hex,
            "operation_kind": operation_kind,
            "payload": payload,
        }
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _replay(self) -> None:
        if not self._path.exists():
            return
        lines = self._path.read_text(encoding="utf-8").splitlines()
        for line in lines:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                if record.get("schema_version") != self.SCHEMA_VERSION:
                    raise ReviewStoreIntegrityError("unsupported or missing ReviewStore schema_version")
                op = record.get("operation_kind")
                payload = record.get("payload") or {}
                if op == "REVIEW_RUN":
                    run = _run_from_dict(payload)
                    InMemoryReviewStore.record_review_run(self, run)
                elif op == "REVIEW_EVIDENCE":
                    ev = _evidence_from_dict(payload)
                    InMemoryReviewStore.record_evidence(self, ev)
                else:
                    raise ValueError(f"unknown operation: {op!r}")
            except Exception as exc:
                logger.error("Review log replay failed at line: %s", exc)
                raise

    def record_review_run(self, run: ReviewRun) -> ReviewRun:
        existing = self._runs.get(run.review_run_id)
        result = super().record_review_run(run)
        if existing is None:
            self._append("REVIEW_RUN", _run_to_dict(run))
        return result

    def record_evidence(self, evidence: ReviewEvidence) -> ReviewEvidence:
        existing = self._evidences.get(evidence.evidence_id)
        result = super().record_evidence(evidence)
        if existing is None:
            self._append("REVIEW_EVIDENCE", _evidence_to_dict(evidence))
        return result
