"""Runtime-owned historical wiring for P2r0 factual reply-link archives.

This module composes already committed factual captures with an exact P2a
ReviewRun/snapshot.  It deliberately contains no semantic promotion rule and
is never used by the request-local Observatory preview path.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .episode import EpisodeEventKind
from .promotion_infrastructure import (
    CanonicalHashV1,
    P2CanonicalArtifactEncoderV1,
    P2PromotionStore,
    P2ReviewRunWithSnapshotV1,
    default_encoding_profile_v1,
)
from .reply_link_authority import (
    FACT_CAPTURE_BINDING_SCHEMA,
    P2R0_HOST_OUTPUT_FACT_CAPTURE,
    P2R0_INBOUND_REPLY_FACT_CAPTURE,
    FactCaptureAuthorityBindingV1,
    HostOutputMessageIdentityFactV1,
    InboundReplyReferenceFactV1,
    P2r0Store,
    P2rReplyLinkFactArchiveV1,
)
from .review import ReviewRun
from .review_service import (
    ReviewInputSnapshot,
    compute_input_snapshot_hash,
    evaluate_review_eligibility,
    review_episode,
)
from .review_store import ReviewStore

logger = logging.getLogger(__name__)


class HistoricalArchiveWiringError(ValueError):
    """The factual archive could not be created without guessing authority."""


class ProductionReviewCompletionCoordinator:
    """Explicit finalized-Episode owner for production Review + archive.

    This coordinator is intentionally separate from Observatory Preview.  It
    only accepts a finalized, review-eligible Episode, persists the exact P1
    ReviewRun into its durable ReviewStore, and then invokes the runtime-owned
    historical archive service.
    """

    owner = "Production Review Completion"

    def __init__(
        self,
        archive_service: P2r0HistoricalArchiveService,
        review_store: ReviewStore,
        *,
        evidence_promoter: object | None = None,
    ) -> None:
        if not isinstance(archive_service, P2r0HistoricalArchiveService):
            raise HistoricalArchiveWiringError("completion requires archive wiring service")
        if not isinstance(review_store, ReviewStore):
            raise HistoricalArchiveWiringError("completion requires a ReviewStore")
        self._archive_service = archive_service
        self._review_store = review_store
        self._evidence_promoter = evidence_promoter

    @property
    def review_store(self) -> ReviewStore:
        return self._review_store

    def complete_episode(
        self,
        episode: Any,
        outcomes: tuple[Any, ...] | list[Any] = (),
        *,
        fact_envelopes: Mapping[tuple[Any, str], object] | None = None,
        deterministic_engine: Any | None = None,
        model_engine: Any | None = None,
    ) -> ReviewRun | None:
        from .episode import Episode, EpisodeState

        if type(episode) is not Episode or episode.state is not EpisodeState.FINALIZED:
            return None
        outcome_tuple = tuple(outcomes)
        if evaluate_review_eligibility(episode, outcome_tuple).decision.value != "REVIEW":
            return None
        try:
            snapshot = ReviewInputSnapshot(episode, outcome_tuple, fact_envelopes or {})
            input_hash = compute_input_snapshot_hash(episode, outcome_tuple, fact_envelopes or {})
            run_id = f"run:production:{input_hash.removeprefix('sha256:')}"
            # Finding IDs are intentionally fresh P1 candidate identities.
            # A completed production Run, however, is immutable.  Reuse the
            # exact prior Run for the same frozen snapshot instead of asking an
            # engine to create a conflicting second candidate set.
            run = self._review_store.get_review_run(run_id)
            if run is not None:
                if run.episode_id != episode.episode_id or run.input_snapshot_hash != input_hash:
                    raise HistoricalArchiveWiringError("production ReviewRun identity conflicts with frozen input")
            else:
                run = review_episode(
                    episode,
                    outcome_tuple,
                    self._review_store,
                    fact_envelopes=fact_envelopes,
                    deterministic_engine=deterministic_engine,
                    model_engine=model_engine,
                    review_run_id=run_id,
                    created_at=episode.finalized_at or episode.last_activity_at,
                )
            if run is None:
                return None
            # This is the only production completion trigger.  The archive
            # service commits P2 before selecting/recording P2r0 facts.
            archive = self._archive_service.archive_review_run(run, snapshot)
            # Promotion is deliberately downstream of both durable P2 Run and
            # P2r0 archive commits.  Its failure is observational only and
            # never rolls back an ordinary completion or chat behavior.
            if archive is not None and self._evidence_promoter is not None:
                promote = getattr(self._evidence_promoter, "promote_completed_review", None)
                if callable(promote):
                    try:
                        promote(run, snapshot)
                    except Exception:
                        logger.exception("P2r.1 production promotion failed closed")
            return run
        except Exception as exc:  # noqa: BLE001 - background review is fail-closed
            logger.warning("Production Review completion failed: %s", exc)
            return None

    complete_finalized_episode = complete_episode


def _fields(payload: object) -> Mapping[str, object] | None:
    if not isinstance(payload, Mapping):
        return None
    fields = payload.get("fields")
    return fields if isinstance(fields, Mapping) else None


def _transaction_id_for_fact(store: P2r0Store, fact_id: str, operation: str) -> str:
    """Return the one committed capture transaction for ``fact_id``.

    PREPARE/COMMIT records are read only through the store's public immutable
    transaction views.  The JSONL file is not scanned and a PREPARE-only fact
    is never treated as captured.
    """
    committed = {
        item.get("transaction_id")
        for item in store.committed_transactions
        if isinstance(item, Mapping) and isinstance(item.get("transaction_id"), str)
    }
    matches: list[str] = []
    for prepare in store.prepared_transactions:
        if not isinstance(prepare, Mapping):
            continue
        tx_id = prepare.get("transaction_id")
        if tx_id not in committed or prepare.get("operation") != operation:
            continue
        payload = _fields(prepare.get("payload"))
        if payload is not None and payload.get("fact_id") == fact_id:
            matches.append(tx_id)  # type: ignore[arg-type]
    if len(matches) != 1:
        raise HistoricalArchiveWiringError(
            f"fact {fact_id!r} does not have exactly one committed capture transaction"
        )
    return matches[0]


def _episode_host_facts(
    episode: Any, facts: tuple[HostOutputMessageIdentityFactV1, ...]
) -> tuple[HostOutputMessageIdentityFactV1, ...]:
    refs = tuple(ref for ref in episode.event_refs if ref.kind is EpisodeEventKind.HOST_OUTPUT)
    selected: list[HostOutputMessageIdentityFactV1] = []
    for fact in facts:
        matches = tuple(
            ref for ref in refs
            if ref.ref_id == fact.host_output_event_ref_id
            and ref.source_event_id == fact.source_event_id
            and ref.trace_id == fact.trace_id
            and ref.execution_record_id == fact.host_output_execution_record_id
        )
        if len(matches) > 1:
            raise HistoricalArchiveWiringError("ambiguous Episode HOST_OUTPUT reference")
        if len(matches) == 1:
            selected.append(fact)
    return tuple(sorted(selected, key=lambda item: item.fact_id))


def _episode_inbound_facts(
    episode: Any, facts: tuple[InboundReplyReferenceFactV1, ...]
) -> tuple[InboundReplyReferenceFactV1, ...]:
    source_ids = {
        ref.source_event_id
        for ref in episode.event_refs
        if isinstance(ref.source_event_id, str) and ref.source_event_id
    }
    selected = [fact for fact in facts if fact.source_event_id in source_ids]
    return tuple(sorted(selected, key=lambda item: item.fact_id))


class P2r0HistoricalArchiveService:
    """Own the explicit post-Review P2a/P2r0 archival transaction.

    ``archive_review_run`` is an explicit lifecycle operation.  It is not
    called by ``review_episode`` or Observatory preview, which must remain
    request-local and side-effect free.
    """

    owner = "P2r0 Historical Archive Wiring"

    def __init__(self, p2_store: P2PromotionStore, p2r0_store: P2r0Store) -> None:
        if type(p2_store) is not P2PromotionStore:
            raise HistoricalArchiveWiringError("archive wiring requires production P2PromotionStore")
        if type(p2r0_store) is not P2r0Store:
            raise HistoricalArchiveWiringError("archive wiring requires authoritative P2r0Store")
        self._p2_store = p2_store
        self._p2r0_store = p2r0_store
        self._encoder = P2CanonicalArtifactEncoderV1(default_encoding_profile_v1())
        # Profile registration is append-only and idempotent; it establishes
        # the exact profile before any ReviewRun/snapshot can be committed.
        if self._encoder.profile_hash not in self._p2_store._profiles:
            self._p2_store.record_encoding_profile(self._encoder.profile, self._encoder)

    @property
    def p2_store(self) -> P2PromotionStore:
        return self._p2_store

    @property
    def p2r0_store(self) -> P2r0Store:
        return self._p2r0_store

    def _select_facts(
        self, snapshot: ReviewInputSnapshot
    ) -> tuple[tuple[HostOutputMessageIdentityFactV1, ...], tuple[InboundReplyReferenceFactV1, ...], tuple[FactCaptureAuthorityBindingV1, ...]]:
        hosts = _episode_host_facts(snapshot.episode, self._p2r0_store.host_output_facts)
        inbound = _episode_inbound_facts(snapshot.episode, self._p2r0_store.inbound_reply_facts)
        bindings: list[FactCaptureAuthorityBindingV1] = []
        for fact in hosts:
            tx_id = _transaction_id_for_fact(self._p2r0_store, fact.fact_id, P2R0_HOST_OUTPUT_FACT_CAPTURE)
            bindings.append(FactCaptureAuthorityBindingV1(FACT_CAPTURE_BINDING_SCHEMA, fact.fact_id, tx_id))
        for fact in inbound:
            tx_id = _transaction_id_for_fact(self._p2r0_store, fact.fact_id, P2R0_INBOUND_REPLY_FACT_CAPTURE)
            bindings.append(FactCaptureAuthorityBindingV1(FACT_CAPTURE_BINDING_SCHEMA, fact.fact_id, tx_id))
        return hosts, inbound, tuple(sorted(bindings, key=lambda item: item.fact_id))

    def archive_review_run(
        self, run: ReviewRun, snapshot: ReviewInputSnapshot
    ) -> P2rReplyLinkFactArchiveV1 | None:
        """Persist one immutable Run+snapshot and its factual P2r0 archive.

        Archive persistence failures are diagnosed and return ``None`` so a
        normal chat/send lifecycle is never blocked.  The P2 Run remains
        authoritative when the later P2r0 archive append fails.
        """
        if type(run) is not ReviewRun or type(snapshot) is not ReviewInputSnapshot:
            raise HistoricalArchiveWiringError("archive requires exact ReviewRun and ReviewInputSnapshot")
        if run.episode_id != snapshot.episode.episode_id:
            raise HistoricalArchiveWiringError("ReviewRun and snapshot Episode mismatch")
        try:
            p2_run = self._p2_store.record_run_with_snapshot(run, snapshot, self._encoder)
            return self.archive_committed_run(p2_run, snapshot)
        except Exception as exc:  # noqa: BLE001 - archive is observational and fail-closed
            logger.warning("P2r0 historical archive unavailable: %s", exc)
            return None

    def archive_committed_run(
        self, p2_run: P2ReviewRunWithSnapshotV1, snapshot: ReviewInputSnapshot
    ) -> P2rReplyLinkFactArchiveV1 | None:
        """Archive only an already persisted, exact P2 Run+snapshot.

        This lower-level entry point intentionally does not backfill a missing
        P2 authority.  It is useful for replay/retry and makes the ordering
        boundary explicit for future lifecycle callers.
        """
        if type(p2_run) is not P2ReviewRunWithSnapshotV1 or type(snapshot) is not ReviewInputSnapshot:
            raise HistoricalArchiveWiringError("archive requires an exact committed P2 Run/snapshot")
        if p2_run.run.episode_id != snapshot.episode.episode_id:
            raise HistoricalArchiveWiringError("P2 Run and snapshot Episode mismatch")
        try:
            persisted = self._p2_store.require_archive(p2_run.run)
            expected = self._encoder.encode(p2_run)
            if CanonicalHashV1.canonical_json_utf8(persisted) != CanonicalHashV1.canonical_json_utf8(expected):
                raise HistoricalArchiveWiringError("persisted P2 Run differs from supplied authoritative commit")
            hosts, inbound, bindings = self._select_facts(snapshot)
            archive = P2rReplyLinkFactArchiveV1.from_p2_run_snapshot(
                p2_run,
                p2r0_encoding_profile_hash=self._p2r0_store.encoder.profile_hash,
                host_output_facts=hosts,
                inbound_reply_facts=inbound,
                fact_capture_authority=bindings,
            )
            existing = tuple(item for item in self._p2r0_store.archives if item.review_run_id == p2_run.run.review_run_id)
            if existing:
                if len(existing) == 1 and existing[0] == archive:
                    return existing[0]
                raise HistoricalArchiveWiringError("immutable archive already exists with different facts")
            self._p2r0_store.record_archive(archive, authoritative_p2_run=p2_run)
            return archive
        except Exception as exc:  # noqa: BLE001 - archive is observational and fail-closed
            logger.warning("P2r0 historical archive unavailable: %s", exc)
            return None


def create_runtime_archive_service(data_dir: str | Path, p2r0_store: P2r0Store) -> P2r0HistoricalArchiveService:
    """Create the one production-owned P2 store for a plugin runtime."""
    root = Path(data_dir) / "cognitive" / "p2a-review-runs"
    root.mkdir(parents=True, exist_ok=True)
    return P2r0HistoricalArchiveService(P2PromotionStore(root / "promotion.jsonl"), p2r0_store)


# Concise aliases for runtime composition and future explicit lifecycle callers.
HistoricalArchiveWiring = P2r0HistoricalArchiveService


__all__ = [
    "HistoricalArchiveWiring",
    "HistoricalArchiveWiringError",
    "P2r0HistoricalArchiveService",
    "create_runtime_archive_service",
]
