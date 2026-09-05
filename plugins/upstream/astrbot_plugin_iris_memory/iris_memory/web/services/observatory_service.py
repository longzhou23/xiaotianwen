"""Read-only projection of the frozen P1 cognitive-review foundation.

This service is deliberately not a second cognitive authority.  It reads the
public EpisodeStore/ReviewStore APIs, and its preview path uses a fresh
InMemoryReviewStore for each request.  No route may use this service to append
Episode, Outcome, Review, Iris, or behavioural state.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Mapping

from iris_memory.cognitive.contracts import (
    BehaviorExecutionRecord, BehaviorTrace, DivergenceType, GroundingEnforcement,
    HostResult, OutputProducer, OutputState, ShadowComparison, TraceStage,
    TriggerDecision,
)
from iris_memory.cognitive.episode import Episode, EpisodeEventKind, EpisodeEventRef, EpisodeState
from iris_memory.cognitive.episode_store import EpisodeStore
from iris_memory.cognitive.execution_observatory import ExecutionRecordObservatory
from iris_memory.cognitive.outcome import OutcomeExplicitness, OutcomeKind, OutcomeObservation
from iris_memory.cognitive.review import EvidenceSourceType, ReviewRun
from iris_memory.cognitive.review_service import (
    ReviewInputSnapshot, compute_input_snapshot_hash, evaluate_review_eligibility, review_episode,
)
from iris_memory.cognitive.review_store import InMemoryReviewStore, ReviewStore


def _json_value(value: Any) -> Any:
    """Convert frozen contracts to safe JSON without retaining live objects."""
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, frozenset, set)):
        return [_json_value(item) for item in value]
    if is_dataclass(value):
        # ``asdict`` deep-copies MappingProxyType fields used by frozen cognitive
        # contracts and therefore raises.  Read declared fields directly instead.
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
    # The service never serializes arbitrary supplied facts.  This final branch
    # only protects the management response if a future public contract grows.
    return {"unavailable_type": type(value).__name__}


def _timestamp(value: datetime | None) -> str | None:
    return _json_value(value) if value else None


class P1ObservatoryService:
    """Thin read model and non-persistent preview runner for the P1 UI."""

    PROMOTION_REASON = (
        "Promotion 当前未接入有效的 production composition；观测台保持只读、fail-closed。"
    )

    def __init__(
        self,
        episode_store: EpisodeStore | None = None,
        review_store: ReviewStore | None = None,
        execution_records: Mapping[str, BehaviorExecutionRecord] | None = None,
        execution_observatory: ExecutionRecordObservatory | None = None,
        p2r0_store: Any | None = None,
        runtime_state: Mapping[str, Any] | None = None,
    ) -> None:
        self._episode_store = episode_store
        self._review_store = review_store
        self._p2r0_store = p2r0_store
        self._runtime_state = dict(runtime_state or {})
        # A caller may inject immutable records for tests/demo integration.  The
        # production runtime currently does not expose this source, so absent
        # records are reported as unavailable rather than reconstructed.
        self._execution_records = dict(execution_records or {})
        self._execution_observatory = execution_observatory

    @property
    def available(self) -> bool:
        return self._episode_store is not None

    def summary(self) -> dict[str, Any]:
        if self._episode_store is None:
            return self._unavailable_summary()
        episodes = self._episode_store.all_episodes()
        outcomes = self._episode_store.get_outcomes()
        runs: list[ReviewRun] = []
        evidence_count = 0
        review_data_available = self._review_store is not None
        review_error: str | None = None
        if self._review_store is not None:
            try:
                for episode in episodes:
                    runs.extend(self._review_store.list_review_runs_for_episode(episode.episode_id))
                    evidence_count += len(self._review_store.list_evidence_for_episode(episode.episode_id))
            except Exception:
                review_data_available = False
                review_error = "review_store_unavailable"
        promotion_enabled = self._state_bool("promotion_enabled")
        lifecycle_enabled = self._state_bool("lifecycle_enabled")
        review_enabled = self._state_bool("review_enabled")
        rules = self._state_rules()
        phase = "P2l.1" if lifecycle_enabled else "P2r.1" if promotion_enabled else "P1"
        review_status = (
            "UNAVAILABLE"
            if not review_data_available
            else "ENABLED"
            if review_enabled
            else "DISABLED"
        )
        review_runs_value: int | str = len(runs) if review_data_available else "Unavailable"
        findings_value: int | str = sum(len(run.findings) for run in runs) if review_data_available else "Unavailable"
        evidence_value: int | str = evidence_count if review_data_available else "Unavailable"
        return {
            "available": True,
            "phase": phase,
            "episodes": len(episodes),
            "finalized_episodes": sum(e.state is EpisodeState.FINALIZED for e in episodes),
            "outcomes": len(outcomes),
            "review_runs": review_runs_value,
            "review_findings": findings_value,
            "review_evidence": evidence_value,
            "review_store": "AVAILABLE" if review_data_available else "UNAVAILABLE",
            "review_store_error": review_error,
            "review_run_count_source": (
                "runtime_owned_persisted_review_store" if review_data_available else "Unavailable"
            ),
            "finding_count_source": (
                "runtime_owned_persisted_review_store" if review_data_available else "Unavailable"
            ),
            "evidence_count_source": (
                "runtime_owned_persisted_review_store" if review_data_available else "Unavailable"
            ),
            "review_status_counts": (
                {status.value: sum(run.status is status for run in runs) for status in {run.status for run in runs}}
                if review_data_available
                else None
            ),
            "lifecycle": {
                "enabled": lifecycle_enabled,
                "status": "ENABLED" if lifecycle_enabled else "DISABLED",
            },
            "review": {
                "enabled": review_enabled,
                "status": review_status,
            },
            "preview_available": True,
            "promotion": {
                "enabled": promotion_enabled,
                "status": "ENABLED" if promotion_enabled else "DISABLED / FAIL-CLOSED",
                "rules": list(rules),
                "rule_count": len(rules),
                "reason": (
                    "当前尚无 ReviewFinding 满足生产唯一允许的明确纠正规则。"
                    if promotion_enabled
                    else self.PROMOTION_REASON
                ),
            },
            "semantic_evaluator": self._runtime_state.get("semantic_evaluator"),
            "behavioral_learning": {
                "enabled": self._state_bool("p2b_enabled"),
                "status": "ENABLED" if self._state_bool("p2b_enabled") else "DISABLED",
                "label": "P2b 尚未启用",
            },
        }

    def list_episodes(
        self, *, state: str | None = None, query: str | None = None, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        if self._episode_store is None:
            return {"available": False, "episodes": [], "total": 0, "reason": "episode_store_not_wired"}
        try:
            wanted = EpisodeState(state) if state and state != "ALL" else None
        except ValueError as exc:
            raise ValueError("invalid episode state") from exc
        needle = (query or "").strip().casefold()
        items = []
        for episode in self._episode_store.all_episodes():
            if wanted and episode.state is not wanted:
                continue
            searchable = " ".join([episode.episode_id, episode.root_event_id] + [ref.ref_id for ref in episode.event_refs]).casefold()
            if needle and needle not in searchable:
                continue
            outcomes = self._episode_store.get_outcomes(episode.episode_id)
            items.append(self._episode_summary(episode, outcomes))
        items.sort(key=lambda item: (item["last_activity_at"] or "", item["episode_id"]), reverse=True)
        total = len(items)
        return {"available": True, "episodes": items[offset : offset + limit], "total": total, "limit": limit, "offset": offset}

    def episode_detail(self, episode_id: str) -> dict[str, Any]:
        episode = self._require_episode(episode_id)
        outcomes = self._episode_store.get_outcomes(episode_id)  # type: ignore[union-attr]
        persisted = self._persisted_review(episode_id)
        snapshot = self.snapshot_debug_view(episode_id)
        attachments = self._attachment_views(episode, outcomes)
        archive = self._persisted_archive(episode_id)
        return {
            "available": True,
            "episode": self._episode_view(episode),
            "outcomes": [self._outcome_view(outcome, episode) for outcome in outcomes],
            "timeline": self._timeline(episode, outcomes, persisted),
            "attachments": attachments,
            "review": persisted,
            "archive": archive,
            "snapshot": snapshot,
            # Pure read-model projection for the default human-readable view.
            # It never becomes an Episode/Outcome/Review source of truth.
            "human": self._human_detail(episode, outcomes, persisted, snapshot, attachments),
            "raw": {"episode": self._episode_view(episode), "outcomes": _json_value(outcomes), "review": persisted, "archive": archive, "snapshot": snapshot},
        }

    def persisted_review(self, episode_id: str) -> dict[str, Any]:
        self._require_episode(episode_id)
        return self._persisted_review(episode_id)

    def preview_review(self, episode_id: str) -> dict[str, Any]:
        """Run frozen review logic only against request-local in-memory state."""
        episode = self._require_episode(episode_id)
        outcomes = self._episode_store.get_outcomes(episode_id)  # type: ignore[union-attr]
        eligibility = evaluate_review_eligibility(episode, outcomes)
        facts, missing = self._attached_execution_facts(episode)
        if missing and any(ref.kind is EpisodeEventKind.HOST_OUTPUT for ref in episode.event_refs):
            return self._preview_response(
                eligibility=eligibility, run=None, facts=facts, unavailable_reason="execution_records_not_wired", missing=missing
            )
        preview_store = InMemoryReviewStore()
        run = review_episode(episode, outcomes, preview_store, fact_envelopes=facts)
        return self._preview_response(eligibility=eligibility, run=run, facts=facts, missing=missing)

    def snapshot_debug_view(self, episode_id: str) -> dict[str, Any]:
        episode = self._require_episode(episode_id)
        outcomes = self._episode_store.get_outcomes(episode_id)  # type: ignore[union-attr]
        facts, missing = self._attached_execution_facts(episode)
        try:
            snapshot = ReviewInputSnapshot(episode, outcomes, facts)
            return {
                "available": True,
                "hash": compute_input_snapshot_hash(episode, outcomes, facts),
                "fact_payload_hashed": True,
                "fact_deep_snapshotted": True,
                "episode_attachment": "ENFORCED",
                "event_count": len(episode.event_refs),
                "outcome_count": len(outcomes),
                "canonical_fact_count": len(snapshot.fact_envelopes),
                "source_types": sorted({source.value for source, _ in snapshot.fact_envelopes}),
                "canonical_facts": [
                    {
                        "source_type": source.value,
                        "ref_id": ref_id,
                        "schema_version": envelope.schema_version,
                        "payload": self._safe_fact_view(facts[(source, ref_id)]),
                    }
                    for (source, ref_id), envelope in snapshot.fact_envelopes.items()
                ],
                "execution_records_unavailable": missing,
            }
        except ValueError as exc:
            return {"available": False, "status": "REJECTED", "reason": str(exc), "execution_records_unavailable": missing}

    def demo_cases(self) -> list[dict[str, str]]:
        return [
            {"id": "correction", "title": "Correction", "summary": "Host output → explicit correction → Finding → zero Evidence"},
            {"id": "acknowledgement", "title": "Acknowledgement", "summary": "Host output → explicit acknowledgement → Finding → zero Evidence"},
            {"id": "late-feedback", "title": "Late Feedback", "summary": "Later feedback explicitly targets an old finalized Episode"},
            {"id": "silence", "title": "Silence / No Feedback", "summary": "No feedback never fabricates negative Finding or Evidence"},
            {"id": "unattached", "title": "Rejected Unattached Fact", "summary": "Typed Host execution not attached to Episode is rejected at snapshot boundary"},
        ]

    def demo_case(self, case_id: str) -> dict[str, Any]:
        if case_id not in {case["id"] for case in self.demo_cases()}:
            raise KeyError(case_id)
        episode, outcomes, records = self._demo_fixture(case_id)
        store = _DemoEpisodeStore(episode, outcomes)
        service = P1ObservatoryService(store, execution_records=records)
        detail = service.episode_detail(episode.episode_id)
        preview = service.preview_review(episode.episode_id)
        if case_id == "unattached":
            bad_ref = next(ref for ref in episode.event_refs if ref.kind is EpisodeEventKind.HOST_OUTPUT)
            bad_record = self._execution_record(event_id="demo:unattached")
            try:
                ReviewInputSnapshot(episode, outcomes, {(EvidenceSourceType.HOST_RESULT, bad_ref.ref_id): bad_record})
            except ValueError as exc:
                detail["rejection"] = {"status": "REJECTED", "reason": str(exc)}
        return {"demo": True, "case_id": case_id, "detail": detail, "preview": preview}

    def _require_episode(self, episode_id: str) -> Episode:
        if self._episode_store is None:
            raise RuntimeError("episode_store_not_wired")
        episode = self._episode_store.get_episode(episode_id)
        if episode is None:
            raise KeyError(episode_id)
        return episode

    def _persisted_review(self, episode_id: str) -> dict[str, Any]:
        if self._review_store is None:
            return {"available": False, "status": "NOT_WIRED", "runs": [], "evidence": []}
        try:
            runs = self._review_store.list_review_runs_for_episode(episode_id)
            evidence = self._review_store.list_evidence_for_episode(episode_id)
        except Exception:
            return {"available": False, "status": "UNAVAILABLE", "runs": [], "evidence": []}
        return {"available": True, "status": "AVAILABLE" if runs else "NO_PERSISTED_REVIEW", "runs": _json_value(runs), "evidence": _json_value(evidence)}

    def _persisted_archive(self, episode_id: str) -> dict[str, Any]:
        """Project the runtime-owned P2r0 archive without opening another store."""
        if self._p2r0_store is None:
            return {"available": False, "status": "UNAVAILABLE", "count": "Unavailable", "archives": []}
        try:
            archives = tuple(item for item in self._p2r0_store.archives if item.episode_id == episode_id)
        except Exception:
            return {"available": False, "status": "UNAVAILABLE", "count": "Unavailable", "archives": []}
        return {"available": True, "status": "AVAILABLE", "count": len(archives), "archives": _json_value(archives)}

    def _state_bool(self, key: str) -> bool:
        return self._runtime_state.get(key) is True

    def _state_rules(self) -> tuple[str, ...]:
        raw = self._runtime_state.get("promotion_rules", ())
        if isinstance(raw, (tuple, list, frozenset, set)):
            return tuple(item for item in raw if isinstance(item, str))
        return ()

    def _attached_execution_facts(self, episode: Episode) -> tuple[dict[tuple[EvidenceSourceType, str], object], list[str]]:
        facts: dict[tuple[EvidenceSourceType, str], object] = {}
        missing: list[str] = []
        for ref in episode.event_refs:
            if ref.kind is not EpisodeEventKind.HOST_OUTPUT:
                continue
            record = self._execution_records.get(ref.ref_id)
            if record is None and self._execution_observatory is not None:
                record = self._execution_observatory.find_for_event_ref(ref)
            if record is None:
                missing.append(ref.ref_id)
            else:
                facts[(EvidenceSourceType.HOST_RESULT, ref.ref_id)] = record
        return facts, missing

    def _attachment_views(self, episode: Episode, outcomes: tuple[OutcomeObservation, ...]) -> list[dict[str, Any]]:
        views = []
        for ref in episode.event_refs:
            source_type = EvidenceSourceType.EPISODE_EVENT
            fact: object = ref
            if ref.kind is EpisodeEventKind.HOST_OUTPUT:
                source_type = EvidenceSourceType.HOST_RESULT
                fact = self._execution_records.get(ref.ref_id)
                if fact is None and self._execution_observatory is not None:
                    fact = self._execution_observatory.find_for_event_ref(ref)
                if fact is None:
                    views.append({"ref_id": ref.ref_id, "source_type": source_type.value, "status": "UNAVAILABLE", "reason": "execution_record_not_wired"})
                    continue
            try:
                ReviewInputSnapshot(episode, outcomes, {(source_type, ref.ref_id): fact})
                views.append({"ref_id": ref.ref_id, "source_type": source_type.value, "status": "ATTACHED", "canonical_payload": self._safe_fact_view(fact)})
            except ValueError as exc:
                views.append({"ref_id": ref.ref_id, "source_type": source_type.value, "status": "REJECTED", "reason": str(exc)})
        return views

    def _timeline(self, episode: Episode, outcomes: tuple[OutcomeObservation, ...], review: dict[str, Any]) -> list[dict[str, Any]]:
        entries = [
            {"at": _timestamp(ref.observed_at), "kind": ref.kind.value, "ref_id": ref.ref_id, "source_event_id": ref.source_event_id, "trace_id": ref.trace_id}
            for ref in episode.event_refs
        ]
        entries.extend({"at": _timestamp(outcome.observed_at), "kind": "OUTCOME", "ref_id": outcome.observation_id, "outcome_kind": outcome.kind.value, "late_feedback": self._is_late(outcome, episode)} for outcome in outcomes)
        for run in review.get("runs", []):
            entries.append({"at": run.get("created_at"), "kind": "REVIEW", "ref_id": run.get("review_run_id"), "status": run.get("status")})
        return sorted(entries, key=lambda item: item.get("at") or "")

    def _outcome_view(self, outcome: OutcomeObservation, episode: Episode) -> dict[str, Any]:
        payload = {
            "observation_id": outcome.observation_id,
            "target_episode_id": outcome.target_episode_id,
            "kind": outcome.kind.value,
            "observed_at": _timestamp(outcome.observed_at),
            "source_event_id": outcome.source_event_id,
            "source_ref_id": outcome.source_ref_id,
            "explicitness": outcome.explicitness.value,
            "confidence": outcome.confidence,
            "evidence": list(outcome.evidence),
            "producer": outcome.producer,
            "provenance": list(outcome.provenance),
        }
        payload["late_feedback"] = self._is_late(outcome, episode)
        return payload

    @staticmethod
    def _is_late(outcome: OutcomeObservation, episode: Episode) -> bool:
        return bool(episode.finalized_at and outcome.observed_at > episode.finalized_at and outcome.target_episode_id == episode.episode_id)

    @staticmethod
    def _episode_summary(episode: Episode, outcomes: tuple[OutcomeObservation, ...]) -> dict[str, Any]:
        return {
            "episode_id": episode.episode_id,
            "state": episode.state.value,
            "root_event_id": episode.root_event_id,
            "opened_at": _timestamp(episode.opened_at),
            "last_activity_at": _timestamp(episode.last_activity_at),
            "finalized_at": _timestamp(episode.finalized_at),
            "event_count": len(episode.event_refs),
            "outcome_count": len(outcomes),
            "topic_hint_available": bool(episode.topic_hint),
            "human": P1ObservatoryService._human_counts(episode, outcomes),
        }

    @staticmethod
    def _human_counts(episode: Episode, outcomes: tuple[OutcomeObservation, ...]) -> dict[str, Any]:
        """Return structural, read-only counts; never infer turns by division."""
        refs = episode.event_refs
        return {
            "lifecycle_label": {
                EpisodeState.OPEN: "进行中",
                EpisodeState.SOFT_CLOSED: "暂时结束，等待后续",
                EpisodeState.FINALIZED: "已结束并封存",
                EpisodeState.INTERRUPTED: "运行中断",
            }[episode.state],
            "interaction_turns": sum(ref.kind is EpisodeEventKind.EXPERIENCE for ref in refs),
            "cognitive_decisions": sum(
                ref.kind in {EpisodeEventKind.COGNITIVE_PROPOSAL, EpisodeEventKind.NO_INTENT}
                for ref in refs
            ),
            "no_intent": sum(ref.kind is EpisodeEventKind.NO_INTENT for ref in refs),
            "host_outputs": sum(ref.kind is EpisodeEventKind.HOST_OUTPUT for ref in refs),
            "dispatches": sum(ref.kind is EpisodeEventKind.DISPATCH for ref in refs),
            "outcomes": len(outcomes),
        }

    def _human_detail(
        self,
        episode: Episode,
        outcomes: tuple[OutcomeObservation, ...],
        review: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        attachments: list[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Project operational facts into labels for the simple UI view only."""
        counts = self._human_counts(episode, outcomes)
        host_attachments = [item for item in attachments if item.get("source_type") == EvidenceSourceType.HOST_RESULT.value]
        verified = sum(item.get("status") == "ATTACHED" for item in host_attachments)
        unavailable = sum(item.get("status") == "UNAVAILABLE" for item in host_attachments)
        rejected = sum(item.get("status") == "REJECTED" for item in host_attachments)
        host_total = counts["host_outputs"]
        if host_total == 0:
            host_integrity = "NO_HOST_OUTPUT"
        elif verified == host_total:
            host_integrity = "COMPLETE"
        else:
            host_integrity = "PARTIAL"
        runs = review.get("runs", [])
        review_storage = review.get("status")
        review_runs_value: int | str = (
            len(runs) if review_storage not in {"NOT_WIRED", "UNAVAILABLE"} else "Unavailable"
        )
        review_findings_value: int | str = (
            sum(len(run.get("findings", [])) for run in runs)
            if review_storage not in {"NOT_WIRED", "UNAVAILABLE"}
            else "Unavailable"
        )
        return {
            **counts,
            "shadow_observation": True,
            "host_fact_integrity": host_integrity,
            "verified_host_facts": verified,
            "unavailable_host_facts": unavailable,
            "rejected_host_facts": rejected,
            "snapshot_available": bool(snapshot.get("available")),
            "snapshot_content_frozen": bool(snapshot.get("fact_deep_snapshotted")),
            "snapshot_payload_hashed": bool(snapshot.get("fact_payload_hashed")),
            "snapshot_attachment_enforced": snapshot.get("episode_attachment") == "ENFORCED",
            "review_storage": review_storage,
            "review_status": review.get("status"),
            "review_runs": review_runs_value,
            "review_findings": review_findings_value,
            "promotion_enabled": self._state_bool("promotion_enabled"),
            "promotion_rules": list(self._state_rules()),
            "p2b_enabled": self._state_bool("p2b_enabled"),
        }

    @staticmethod
    def _episode_view(episode: Episode) -> dict[str, Any]:
        """Safe Episode view: retain structural facts, never message-topic text."""
        return {
            "episode_id": episode.episode_id,
            "scope_id": episode.scope_id,
            "state": episode.state.value,
            "root_event_id": episode.root_event_id,
            "opened_at": _timestamp(episode.opened_at),
            "last_activity_at": _timestamp(episode.last_activity_at),
            "soft_closed_at": _timestamp(episode.soft_closed_at),
            "finalized_at": _timestamp(episode.finalized_at),
            "revision": episode.revision,
            "event_refs": [P1ObservatoryService._safe_fact_view(ref) for ref in episode.event_refs],
            "unresolved_refs": list(episode.unresolved_refs),
            "topic_hint_available": bool(episode.topic_hint),
            "provenance": list(episode.provenance),
        }

    @staticmethod
    def _safe_fact_view(fact: object) -> dict[str, Any]:
        """Expose only the structural observability fields needed by the UI."""
        if isinstance(fact, EpisodeEventRef):
            return {
                "ref_id": fact.ref_id,
                "kind": fact.kind.value,
                "source_event_id": fact.source_event_id,
                "trace_id": fact.trace_id,
                "execution_record_id": fact.execution_record_id,
                "observed_at": _timestamp(fact.observed_at),
            }
        if isinstance(fact, BehaviorExecutionRecord):
            trace, host, comparison = fact.trace, fact.host_result, fact.comparison
            return {
                "trace_id": trace.trace_id,
                "event_id": trace.event_id,
                "runtime_mode": trace.runtime_mode.value,
                "created_at": _timestamp(trace.created_at),
                "stage": fact.stage.value,
                "revision": fact.revision,
                "updated_at": _timestamp(fact.updated_at),
                "host_result": {
                    "legacy_fallthrough": host.legacy_fallthrough,
                    "output_generated": host.output_generated,
                    "output_nonempty": host.output_nonempty,
                    "dispatch_observed": host.dispatch_observed,
                    "output_state": host.output_state.value,
                    "producer": host.producer.value,
                    "applied_enforcement": host.applied_enforcement.value,
                    "delivery_status": host.delivery_status.value,
                },
                "comparison": {
                    "cognitive_would_participate": comparison.cognitive_would_participate,
                    "cognitive_would_reply": comparison.cognitive_would_reply,
                    "legacy_replied": comparison.legacy_replied,
                    "legacy_output_present": comparison.legacy_output_present,
                    "divergence": comparison.divergence.value,
                },
            }
        return _json_value(fact)

    @classmethod
    def _preview_response(cls, *, eligibility: Any, run: ReviewRun | None, facts: Mapping[Any, Any], unavailable_reason: str | None = None, missing: list[str] | None = None) -> dict[str, Any]:
        return {"preview": True, "persisted": False, "eligibility": _json_value(eligibility), "run": _json_value(run) if run else None, "evidence_count": 0, "promotion": {"enabled": False, "status": "DISABLED / FAIL-CLOSED", "reason": cls.PROMOTION_REASON}, "canonical_fact_count": len(facts), "unavailable_reason": unavailable_reason, "execution_records_unavailable": missing or []}

    @staticmethod
    def _unavailable_summary() -> dict[str, Any]:
        unavailable = "Unavailable"
        return {"available": False, "reason": "episode_store_not_wired", "episodes": unavailable, "finalized_episodes": unavailable, "outcomes": unavailable, "review_runs": unavailable, "review_findings": unavailable, "review_evidence": unavailable, "review_store": "UNAVAILABLE", "review_run_count_source": unavailable, "finding_count_source": unavailable, "evidence_count_source": unavailable, "preview_available": False, "lifecycle": {"enabled": False, "status": "UNAVAILABLE"}, "review": {"enabled": False, "status": "UNAVAILABLE"}, "promotion": {"enabled": False, "status": "UNAVAILABLE", "rules": [], "rule_count": 0, "reason": "episode_store_not_wired"}, "behavioral_learning": {"enabled": False, "status": "DISABLED", "label": "P2b 尚未启用"}}

    def _demo_fixture(self, case_id: str) -> tuple[Episode, tuple[OutcomeObservation, ...], dict[str, BehaviorExecutionRecord]]:
        now = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
        trace = self._execution_record(event_id="demo:event")
        host_ref = EpisodeEventRef(
            f"HOST_OUTPUT:demo:event:{trace.trace.trace_id}", EpisodeEventKind.HOST_OUTPUT,
            "demo:event", trace.trace.trace_id, f"{trace.trace.trace_id}:1", now,
        )
        episode = Episode("episode:demo:root", "demo", EpisodeState.FINALIZED, "demo:event", now, now, event_refs=(host_ref,), finalized_at=now, provenance=("p1_observatory_demo",))
        records = {host_ref.ref_id: trace}
        outcomes: tuple[OutcomeObservation, ...] = ()
        if case_id in {"correction", "late-feedback"}:
            observed = now + timedelta(minutes=5) if case_id == "late-feedback" else now
            outcomes = (OutcomeObservation("outcome:demo:correction", episode.episode_id, OutcomeKind.EXPLICIT_CORRECTION, observed, source_event_id="demo:later" if case_id == "late-feedback" else "demo:reply", explicitness=OutcomeExplicitness.EXPLICIT, provenance=("p1_observatory_demo",)),)
        elif case_id == "acknowledgement":
            outcomes = (OutcomeObservation("outcome:demo:ack", episode.episode_id, OutcomeKind.EXPLICIT_ACKNOWLEDGEMENT, now, source_event_id="demo:reply", explicitness=OutcomeExplicitness.EXPLICIT, provenance=("p1_observatory_demo",)),)
        elif case_id == "silence":
            episode = Episode("episode:demo:silence", "demo", EpisodeState.FINALIZED, "demo:silence", now, now, finalized_at=now, provenance=("p1_observatory_demo",))
            records = {}
        return episode, outcomes, records

    @staticmethod
    def _execution_record(*, event_id: str) -> BehaviorExecutionRecord:
        trace = BehaviorTrace(event_id=event_id, trigger=TriggerDecision(True, "p1_observatory_demo", 1), participation=None, intent=None, grounding=None, exit_reason=None)
        host = HostResult(True, True, True, False, OutputState.OUTPUT_READY, OutputProducer.LEGACY_HOST, GroundingEnforcement.NOT_APPLIED)
        comparison = ShadowComparison(True, True, None, True, True, DivergenceType.MATCH_REPLY)
        return BehaviorExecutionRecord(trace, host, comparison, TraceStage.HOST_OUTPUT, 1)


class _DemoEpisodeStore:
    """Private read-only store facade used only for per-request demo fixtures."""
    def __init__(self, episode: Episode, outcomes: tuple[OutcomeObservation, ...]) -> None:
        self._episode, self._outcomes = episode, outcomes
    def get_episode(self, episode_id: str) -> Episode | None:
        return self._episode if episode_id == self._episode.episode_id else None
    def get_outcomes(self, episode_id: str | None = None) -> tuple[OutcomeObservation, ...]:
        return self._outcomes if episode_id in (None, self._episode.episode_id) else ()
    def all_episodes(self) -> tuple[Episode, ...]:
        return (self._episode,)
