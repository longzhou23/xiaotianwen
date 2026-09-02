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
        "当前 Review 可以形成可审计 Finding，但尚未冻结安全的 Evidence promotion contract，"
        "因此不会自动形成长期学习证据。"
    )

    def __init__(
        self,
        episode_store: EpisodeStore | None = None,
        review_store: ReviewStore | None = None,
        execution_records: Mapping[str, BehaviorExecutionRecord] | None = None,
        execution_observatory: ExecutionRecordObservatory | None = None,
    ) -> None:
        self._episode_store = episode_store
        self._review_store = review_store
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
        if self._review_store is not None:
            for episode in episodes:
                runs.extend(self._review_store.list_review_runs_for_episode(episode.episode_id))
                evidence_count += len(self._review_store.list_evidence_for_episode(episode.episode_id))
        return {
            "available": True,
            "episodes": len(episodes),
            "finalized_episodes": sum(e.state is EpisodeState.FINALIZED for e in episodes),
            "outcomes": len(outcomes),
            "review_runs": len(runs),
            "review_findings": sum(len(run.findings) for run in runs),
            "review_evidence": evidence_count,
            "review_store": "AVAILABLE" if self._review_store else "NOT_WIRED",
            "preview_available": True,
            "promotion": {
                "enabled": False,
                "status": "DISABLED / FAIL-CLOSED",
                "reason": self.PROMOTION_REASON,
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
        return {
            "available": True,
            "episode": self._episode_view(episode),
            "outcomes": [self._outcome_view(outcome, episode) for outcome in outcomes],
            "timeline": self._timeline(episode, outcomes, persisted),
            "attachments": self._attachment_views(episode, outcomes),
            "review": persisted,
            "snapshot": snapshot,
            "raw": {"episode": self._episode_view(episode), "outcomes": _json_value(outcomes), "review": persisted, "snapshot": snapshot},
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
        runs = self._review_store.list_review_runs_for_episode(episode_id)
        evidence = self._review_store.list_evidence_for_episode(episode_id)
        return {"available": True, "status": "AVAILABLE" if runs else "NO_PERSISTED_REVIEW", "runs": _json_value(runs), "evidence": _json_value(evidence)}

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
        return {"episode_id": episode.episode_id, "state": episode.state.value, "root_event_id": episode.root_event_id, "opened_at": _timestamp(episode.opened_at), "last_activity_at": _timestamp(episode.last_activity_at), "finalized_at": _timestamp(episode.finalized_at), "event_count": len(episode.event_refs), "outcome_count": len(outcomes), "topic_hint_available": bool(episode.topic_hint)}

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
        return {"available": False, "reason": "episode_store_not_wired", "episodes": 0, "finalized_episodes": 0, "outcomes": 0, "review_runs": 0, "review_findings": 0, "review_evidence": 0, "review_store": "NOT_WIRED", "preview_available": False, "promotion": {"enabled": False, "status": "DISABLED / FAIL-CLOSED", "reason": P1ObservatoryService.PROMOTION_REASON}}

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
