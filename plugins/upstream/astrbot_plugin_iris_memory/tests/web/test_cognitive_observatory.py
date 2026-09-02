"""P1 Cognitive Observatory read-only/API regression tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from quart import Quart

from iris_memory.cognitive.contracts import (
    BehaviorExecutionRecord, BehaviorTrace, DivergenceType, GroundingEnforcement,
    HostResult, OutputProducer, OutputState, ShadowComparison, TraceStage, TriggerDecision,
)
from iris_memory.cognitive.episode import Episode, EpisodeEventKind, EpisodeEventRef, EpisodeState
from iris_memory.cognitive.episode_store import InMemoryEpisodeStore
from iris_memory.cognitive.outcome import OutcomeExplicitness, OutcomeKind, OutcomeObservation
from iris_memory.cognitive.review_store import InMemoryReviewStore
from iris_memory.cognitive.review_service import review_episode
from iris_memory.cognitive.review import EvidenceSourceType
from iris_memory.web.routes import observatory as routes
from iris_memory.web.services.observatory_service import P1ObservatoryService


NOW = datetime(2026, 9, 2, 12, tzinfo=timezone.utc)


def _record(event_id: str = "event:1") -> BehaviorExecutionRecord:
    trace = BehaviorTrace(event_id, TriggerDecision(True, "test", 1), None, None, None, None)
    return BehaviorExecutionRecord(
        trace,
        HostResult(True, True, True, False, OutputState.OUTPUT_READY, OutputProducer.LEGACY_HOST, GroundingEnforcement.NOT_APPLIED),
        ShadowComparison(True, True, None, True, True, DivergenceType.MATCH_REPLY),
        TraceStage.HOST_OUTPUT,
        1,
        NOW,
    )


def _fixture(
    *,
    outcome_kind: OutcomeKind | None = OutcomeKind.EXPLICIT_CORRECTION,
    late: bool = False,
    episode_id: str = "episode:test:1",
    state: EpisodeState = EpisodeState.FINALIZED,
):
    record = _record()
    host_ref = EpisodeEventRef(
        f"HOST_OUTPUT:event:1:{record.trace.trace_id}", EpisodeEventKind.HOST_OUTPUT,
        "event:1", record.trace.trace_id, f"{record.trace.trace_id}:1", NOW,
    )
    episode = Episode(
        episode_id,
        "test",
        state,
        "event:1",
        NOW,
        NOW,
        event_refs=(host_ref,),
        finalized_at=NOW if state is EpisodeState.FINALIZED else None,
    )
    store = InMemoryEpisodeStore(); store.create_episode(episode)
    outcomes = ()
    if outcome_kind:
        outcome = OutcomeObservation("outcome:test:1", episode.episode_id, outcome_kind, NOW + timedelta(minutes=2) if late else NOW, source_event_id="event:new" if late else "event:2", explicitness=OutcomeExplicitness.EXPLICIT)
        store.record_outcome(outcome); outcomes = (outcome,)
    return store, episode, outcomes, {host_ref.ref_id: record}


def _four_turn_fixture(*, state: EpisodeState = EpisodeState.OPEN, include_extra_event: bool = False):
    refs: list[EpisodeEventRef] = []
    records: dict[str, BehaviorExecutionRecord] = {}
    for number in range(1, 5):
        event_id = f"event:{number}"
        record = _record(event_id)
        refs.extend((
            EpisodeEventRef(f"EXPERIENCE:{event_id}", EpisodeEventKind.EXPERIENCE, event_id, observed_at=NOW),
            EpisodeEventRef(f"NO_INTENT:{event_id}:{record.trace.trace_id}", EpisodeEventKind.NO_INTENT, event_id, record.trace.trace_id, observed_at=NOW),
            EpisodeEventRef(f"HOST_OUTPUT:{event_id}:{record.trace.trace_id}", EpisodeEventKind.HOST_OUTPUT, event_id, record.trace.trace_id, f"{record.trace.trace_id}:1", NOW),
            EpisodeEventRef(f"DISPATCH:{event_id}:{record.trace.trace_id}", EpisodeEventKind.DISPATCH, event_id, record.trace.trace_id, f"{record.trace.trace_id}:2", NOW),
        ))
        records[refs[-2].ref_id] = record
    if include_extra_event:
        refs.append(EpisodeEventRef("TOOL_RESULT:event:extra", EpisodeEventKind.TOOL_RESULT, "event:extra", observed_at=NOW))
    episode = Episode(
        "episode:four-turns",
        "private:test",
        state,
        "event:1",
        NOW,
        NOW,
        event_refs=tuple(refs),
        finalized_at=NOW if state is EpisodeState.FINALIZED else None,
    )
    store = InMemoryEpisodeStore(); store.create_episode(episode)
    outcomes = tuple(
        OutcomeObservation(
            f"outcome:four-turns:{number}", episode.episode_id, OutcomeKind.DISPATCH_OBSERVED,
            NOW, source_event_id=f"event:{number}", explicitness=OutcomeExplicitness.STRUCTURAL,
        )
        for number in range(1, 5)
    )
    for outcome in outcomes:
        store.record_outcome(outcome)
    return store, episode, outcomes, records


def test_listing_detail_snapshot_and_late_outcome_are_read_only():
    store, episode, outcomes, records = _fixture(late=True)
    service = P1ObservatoryService(store, execution_records=records)
    before = store.get_episode(episode.episode_id)
    listing = service.list_episodes(state="FINALIZED", query="event:1")
    detail = service.episode_detail(episode.episode_id)
    assert listing["total"] == 1
    assert detail["outcomes"][0]["late_feedback"] is True
    assert detail["attachments"][0]["status"] == "ATTACHED"
    assert detail["snapshot"]["fact_payload_hashed"] is True
    assert detail["snapshot"]["fact_deep_snapshotted"] is True
    assert store.get_episode(episode.episode_id) == before
    json.dumps(detail, ensure_ascii=False)


def test_preview_is_nonpersistent_and_correction_ack_remain_findings_only():
    for kind in (OutcomeKind.EXPLICIT_CORRECTION, OutcomeKind.EXPLICIT_ACKNOWLEDGEMENT):
        store, episode, _outcomes, records = _fixture(outcome_kind=kind)
        production_store = InMemoryReviewStore()
        service = P1ObservatoryService(store, production_store, records)
        preview = service.preview_review(episode.episode_id)
        assert preview["persisted"] is False
        assert preview["evidence_count"] == 0
        assert preview["run"]["status"] == "COMPLETED"
        assert preview["run"]["findings"]
        assert production_store.list_review_runs_for_episode(episode.episode_id) == ()
        assert production_store.list_evidence_for_episode(episode.episode_id) == ()


def test_persisted_review_is_projected_only_when_a_store_is_explicitly_available():
    store, episode, outcomes, records = _fixture()
    review_store = InMemoryReviewStore()
    fact_envelopes = {(EvidenceSourceType.HOST_RESULT, ref_id): record for ref_id, record in records.items()}
    run = review_episode(episode, outcomes, review_store, fact_envelopes=fact_envelopes)
    service = P1ObservatoryService(store, review_store, records)
    persisted = service.persisted_review(episode.episode_id)
    assert run is not None
    assert persisted["status"] == "AVAILABLE"
    assert persisted["runs"][0]["review_run_id"] == run.review_run_id
    assert persisted["evidence"] == []


def test_unattached_typed_execution_is_rejected_and_missing_execution_is_not_fabricated():
    store, episode, _outcomes, records = _fixture()
    wrong = _record("event:other")
    host_ref = episode.event_refs[0]
    rejected = P1ObservatoryService(store, execution_records={host_ref.ref_id: wrong})
    assert rejected.episode_detail(episode.episode_id)["attachments"][0]["status"] == "REJECTED"
    unavailable = P1ObservatoryService(store)
    preview = unavailable.preview_review(episode.episode_id)
    assert preview["unavailable_reason"] == "execution_records_not_wired"
    assert preview["evidence_count"] == 0


def test_empty_and_unavailable_sources_do_not_raise():
    empty = P1ObservatoryService(InMemoryEpisodeStore())
    assert empty.summary()["episodes"] == 0
    assert empty.list_episodes()["episodes"] == []
    unavailable = P1ObservatoryService()
    assert unavailable.summary()["available"] is False
    assert unavailable.list_episodes()["available"] is False


def test_human_view_counts_actual_structural_refs_without_event_division():
    store, episode, _outcomes, records = _four_turn_fixture(include_extra_event=True)
    service = P1ObservatoryService(store, execution_records=records)
    detail = service.episode_detail(episode.episode_id)
    human = detail["human"]
    assert len(episode.event_refs) == 17
    assert human["interaction_turns"] == 4
    assert human["host_outputs"] == 4
    assert human["dispatches"] == 4
    assert human["outcomes"] == 4
    assert human["no_intent"] == 4
    assert human["host_fact_integrity"] == "COMPLETE"
    assert human["verified_host_facts"] == 4
    assert service.list_episodes()["episodes"][0]["human"]["interaction_turns"] == 4


def test_human_view_reports_unavailable_records_and_p1_review_gate_conservatively():
    store, episode, _outcomes, _records = _four_turn_fixture(state=EpisodeState.INTERRUPTED)
    human = P1ObservatoryService(store).episode_detail(episode.episode_id)["human"]
    assert human["lifecycle_label"] == "运行中断"
    assert human["host_fact_integrity"] == "PARTIAL"
    assert human["verified_host_facts"] == 0
    assert human["unavailable_host_facts"] == 4
    assert human["review_storage"] == "NOT_WIRED"
    assert human["promotion_enabled"] is False


def test_human_view_keeps_engineering_raw_details_available_to_the_frontend():
    store, episode, _outcomes, records = _four_turn_fixture()
    detail = P1ObservatoryService(store, execution_records=records).episode_detail(episode.episode_id)
    assert detail["raw"]["episode"]["event_refs"]
    assert detail["snapshot"]["hash"]
    view = Path(__file__).parents[2] / "iris_memory" / "web" / "frontend" / "src" / "views" / "CognitiveObservatoryView.vue"
    source = view.read_text(encoding="utf-8")
    assert "viewMode" in source
    assert "工程视图" in source
    assert "Raw JSON · read-only" in source


def test_route_service_provider_reuses_current_runtime_episode_store(monkeypatch):
    store, episode, _outcomes, _records = _fixture(state=EpisodeState.OPEN)
    runtime = SimpleNamespace(
        episode_observer=SimpleNamespace(store=store),
        execution_observatory=None,
    )
    monkeypatch.setattr(routes, "get_cognitive_runtime", lambda: runtime)

    list_service = routes.get_observatory_service()
    detail_service = routes.get_observatory_service()

    assert list_service._episode_store is store
    assert detail_service._episode_store is store
    assert list_service.list_episodes()["episodes"][0]["episode_id"] == episode.episode_id
    assert detail_service.episode_detail(episode.episode_id)["episode"]["episode_id"] == episode.episode_id


def test_demo_cases_are_memory_only_and_cover_rejected_unattached_fact():
    service = P1ObservatoryService()
    correction = service.demo_case("correction")
    acknowledgement = service.demo_case("acknowledgement")
    late = service.demo_case("late-feedback")
    silence = service.demo_case("silence")
    unattached = service.demo_case("unattached")
    assert correction["demo"] is True and correction["preview"]["evidence_count"] == 0
    assert acknowledgement["preview"]["run"]["findings"]
    assert late["detail"]["outcomes"][0]["late_feedback"] is True
    assert silence["preview"]["run"] is None
    assert unattached["detail"]["rejection"]["status"] == "REJECTED"


class _Context:
    def __init__(self): self.routes = []
    def register_web_api(self, path, handler, methods, desc): self.routes.append((path, handler, methods))


@pytest.mark.asyncio
async def test_routes_return_json_and_error_statuses(monkeypatch):
    store, episode, _outcomes, records = _fixture()
    service = P1ObservatoryService(store, execution_records=records)
    monkeypatch.setattr(routes, "get_observatory_service", lambda: service)
    context = _Context(); routes.register_observatory_routes(context)
    app = Quart(__name__)
    for path, handler, methods in context.routes:
        app.add_url_rule(path, endpoint=path, view_func=handler, methods=methods)
    client = app.test_client()
    assert (await (await client.get("/astrbot_plugin_iris_memory/cognitive-observatory/summary")).get_json())["success"] is True
    assert (await (await client.get("/astrbot_plugin_iris_memory/cognitive-observatory/episodes?state=BAD")).get_json())["success"] is False
    assert (await client.get("/astrbot_plugin_iris_memory/cognitive-observatory/episodes/missing")).status_code == 404
    preview = await (await client.post(f"/astrbot_plugin_iris_memory/cognitive-observatory/episodes/{episode.episode_id}/preview")).get_json()
    assert preview["success"] is True and preview["evidence_count"] == 0


@pytest.mark.asyncio
async def test_listed_open_episode_id_round_trips_through_encoded_route(monkeypatch):
    episode_id = "episode:private:2986500364:runtime:136336110833632:标记 100%"
    store, episode, _outcomes, records = _fixture(
        episode_id=episode_id,
        state=EpisodeState.OPEN,
    )
    service = P1ObservatoryService(store, execution_records=records)
    episode_before = store.get_episode(episode_id)
    outcomes_before = store.get_outcomes(episode_id)
    monkeypatch.setattr(routes, "get_observatory_service", lambda: service)
    context = _Context()
    routes.register_observatory_routes(context)
    app = Quart(__name__)
    for path, handler, methods in context.routes:
        app.add_url_rule(path, endpoint=path, view_func=handler, methods=methods)
    client = app.test_client()

    listing = await (
        await client.get("/astrbot_plugin_iris_memory/cognitive-observatory/episodes")
    ).get_json()
    listed_id = listing["episodes"][0]["episode_id"]
    assert listed_id == episode_id

    encoded_id = quote(listed_id, safe="")
    response = await client.get(
        f"/astrbot_plugin_iris_memory/cognitive-observatory/episodes/{encoded_id}"
    )
    detail = await response.get_json()
    assert response.status_code == 200
    assert detail["episode"]["episode_id"] == listed_id
    assert detail["episode"]["state"] == "OPEN"
    assert detail["outcomes"][0]["target_episode_id"] == listed_id
    assert detail["review"]["runs"] == []

    preview_response = await client.post(
        f"/astrbot_plugin_iris_memory/cognitive-observatory/episodes/{encoded_id}/preview"
    )
    preview = await preview_response.get_json()
    assert preview_response.status_code == 200
    assert preview["eligibility"]["decision"] == "DEFER"
    assert preview["run"] is None
    assert preview["evidence_count"] == 0
    assert store.get_episode(episode_id) == episode_before
    assert store.get_outcomes(episode_id) == outcomes_before
