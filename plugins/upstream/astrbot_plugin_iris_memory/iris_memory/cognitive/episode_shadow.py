"""Shadow-only Episode observation integration.

This module never changes Host authority.  All failures are diagnostic and
fail open for the Legacy/current-turn behavior.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from .contracts import (
    BehaviorExecutionRecord,
    BehaviorTrace,
    CanonicalExperience,
    RuntimeMode,
)
from .episode import (
    BoundaryAction,
    Episode,
    EpisodeEventKind,
    EpisodeEventRef,
    EpisodeState,
    make_episode_event_ref_id,
    make_episode_id,
)
from .episode_assembler import EpisodeAssembler
from .episode_store import EpisodeStore
from .outcome import (
    OutcomeExplicitness,
    OutcomeKind,
    OutcomeObservation,
    make_outcome_observation_id,
)
from .outcome_collector import OutcomeCollector

logger = logging.getLogger(__name__)


class EpisodeShadowObserver:
    owner = "Episode Shadow Observer"

    def __init__(
        self,
        store: EpisodeStore,
        *,
        self_entity: str = "agent:xiaotianwen",
        outcome_collector: OutcomeCollector | None = None,
        native_host_reply_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        self.store = store
        self.assembler = EpisodeAssembler(self_entity=self_entity)
        self.outcome_collector = outcome_collector or OutcomeCollector()
        self._native_host_reply_resolver = native_host_reply_resolver

    def bind_native_host_reply_resolver(
        self, resolver: Callable[[str], str | None] | None
    ) -> None:
        """Bind the runtime-owned P2r0 exact Host identity resolver.

        The resolver returns only an existing Episode HOST_OUTPUT EventRef id.
        EpisodeStore remains the final authority for EventRef-to-Episode lineage.
        """
        if resolver is not None and not callable(resolver):
            raise TypeError("native Host reply resolver must be callable")
        self._native_host_reply_resolver = resolver

    def observe_behavior_trace(
        self,
        experience: CanonicalExperience,
        trace: BehaviorTrace,
    ) -> None:
        if trace.runtime_mode is not RuntimeMode.SHADOW:
            return
        try:
            reply_event_id = self._reply_event_id(experience)
            native_host_episode: Episode | None = None
            if reply_event_id is not None:
                native_host_episode = self._resolve_native_host_reply_episode(
                    experience.event.event_id
                )
                if (
                    native_host_episode is not None
                    and native_host_episode.state is EpisodeState.FINALIZED
                ):
                    self._observe_finalized_feedback(
                        experience, trace, native_host_episode
                    )
                    return

                historical = self.store.find_episode_by_source_event_id(reply_event_id)
                if historical is not None and historical.state is EpisodeState.FINALIZED:
                    self._observe_finalized_feedback(experience, trace, historical)
                    return

            active = self.store.find_active_by_scope(experience.event.session_id)
            event_to_episode: dict[str, str] = {}
            for ep in active:
                for ref in ep.event_refs:
                    if ref.source_event_id:
                        event_to_episode[ref.source_event_id] = ep.episode_id
            if (
                reply_event_id is not None
                and native_host_episode is not None
                and native_host_episode.state
                in (EpisodeState.OPEN, EpisodeState.SOFT_CLOSED)
                and any(ep.episode_id == native_host_episode.episode_id for ep in active)
            ):
                # Preserve the existing assembler policy by presenting the
                # exact native Host reply as an exact member-event resolution.
                event_to_episode[reply_event_id] = native_host_episode.episode_id
            decision = self.assembler.decide(
                experience,
                active_episodes=active,
                event_to_episode=event_to_episode,
            )
            if decision.action is BoundaryAction.NO_EPISODE:
                return
            if decision.action is BoundaryAction.NEW:
                ep = self._create_episode(experience)
            elif decision.action is BoundaryAction.ATTACH and decision.episode_id is not None:
                ep = self.store.get_episode(decision.episode_id)
                if ep is None:
                    return
            else:
                return
            ep = self._append_experience(ep, experience)
            self._append_proposal(ep, trace)
            if decision.action is BoundaryAction.ATTACH:
                self._record_outcomes(experience, target_episode_id=ep.episode_id)
        except Exception:
            logger.exception("Episode shadow observation failed closed; Host unaffected")

    def _resolve_native_host_reply_episode(self, source_event_id: str) -> Episode | None:
        resolver = self._native_host_reply_resolver
        if resolver is None:
            return None
        try:
            host_ref_id = resolver(source_event_id)
        except Exception:
            logger.exception("Exact native Host reply resolution failed closed")
            return None
        if type(host_ref_id) is not str or not host_ref_id:
            return None
        episode = self.store.find_episode_by_event_ref(host_ref_id)
        if episode is None:
            return None
        matches = tuple(
            ref
            for ref in episode.event_refs
            if ref.ref_id == host_ref_id and ref.kind is EpisodeEventKind.HOST_OUTPUT
        )
        return episode if len(matches) == 1 else None

    @staticmethod
    def _reply_event_id(experience: CanonicalExperience) -> str | None:
        raw = experience.event.raw_metadata
        if isinstance(raw, dict) or hasattr(raw, "get"):
            value = raw.get("reply_event_id")
            if isinstance(value, str) and value:
                return value
        return None

    def _observe_finalized_feedback(
        self,
        experience: CanonicalExperience,
        trace: BehaviorTrace,
        old_episode: Episode,
    ) -> None:
        """Create/append a current Episode and target the old FINALIZED Episode.

        The old Episode is never modified.  OutcomeCollector may create an
        OutcomeObservation whose target is the old finalized history.
        """
        ep = self._create_episode(experience)
        ep = self._append_experience(ep, experience)
        self._append_proposal(ep, trace)
        self._record_outcomes(experience, target_episode_id=old_episode.episode_id)

    def _record_outcomes(
        self,
        experience: CanonicalExperience,
        *,
        target_episode_id: str,
    ) -> None:
        for outcome in self.outcome_collector.classify_experience(
            experience,
            target_episode_id=target_episode_id,
        ):
            try:
                self.store.record_outcome(outcome)
            except Exception:
                logger.exception(
                    "Outcome record failed; Host unaffected (target=%s)",
                    target_episode_id,
                )

    def _record_dispatch_outcome(
        self,
        ep: Episode,
        record: BehaviorExecutionRecord,
    ) -> None:
        outcome = OutcomeObservation(
            observation_id=make_outcome_observation_id(
                ep.episode_id,
                OutcomeKind.DISPATCH_OBSERVED,
                source_event_id=record.trace.event_id,
                source_ref_id=f"{record.trace.trace_id}:{record.revision}",
            ),
            target_episode_id=ep.episode_id,
            kind=OutcomeKind.DISPATCH_OBSERVED,
            observed_at=record.updated_at,
            source_event_id=record.trace.event_id,
            source_ref_id=f"{record.trace.trace_id}:{record.revision}",
            explicitness=OutcomeExplicitness.STRUCTURAL,
            evidence=("dispatch_observed",),
            producer="episode_shadow_observer",
            provenance=("episode_shadow_observer",),
        )
        try:
            self.store.record_outcome(outcome)
        except Exception:
            logger.exception("Dispatch outcome record failed; Host unaffected")

    def observe_host_output(self, record: BehaviorExecutionRecord) -> None:
        if record.trace.runtime_mode is not RuntimeMode.SHADOW:
            return
        if not record.host_result.output_nonempty:
            return
        try:
            ep = self.store.find_episode_by_trace_id(record.trace.trace_id)
            if ep is None:
                ep = self.store.create_episode(
                    Episode(
                        episode_id=make_episode_id(
                            record.trace.situation_lite.scope_id if record.trace.situation_lite else "runtime",
                            record.trace.event_id,
                        ),
                        scope_id=record.trace.situation_lite.scope_id if record.trace.situation_lite else "runtime",
                        state=EpisodeState.OPEN,
                        root_event_id=record.trace.event_id,
                        opened_at=record.updated_at,
                        last_activity_at=record.updated_at,
                        provenance=("episode_shadow_observer",),
                    )
                )
            ref = self._host_ref(record)
            self.store.append_event_ref(ep.episode_id, ref)
        except Exception:
            logger.exception("Episode host-output shadow observation failed; Host unaffected")

    def observe_dispatch(self, record: BehaviorExecutionRecord) -> None:
        if record.trace.runtime_mode is not RuntimeMode.SHADOW:
            return
        try:
            ep = self.store.find_episode_by_trace_id(record.trace.trace_id)
            if ep is None:
                return
            ref = EpisodeEventRef(
                ref_id=make_episode_event_ref_id(
                    EpisodeEventKind.DISPATCH,
                    source_event_id=record.trace.event_id,
                    trace_id=record.trace.trace_id,
                    execution_record_id=f"{record.trace.trace_id}:{record.revision}",
                ),
                kind=EpisodeEventKind.DISPATCH,
                source_event_id=record.trace.event_id,
                trace_id=record.trace.trace_id,
                execution_record_id=f"{record.trace.trace_id}:{record.revision}",
                observed_at=record.updated_at,
            )
            self.store.append_event_ref(ep.episode_id, ref)
            self._record_dispatch_outcome(ep, record)
        except Exception:
            logger.exception("Episode dispatch shadow observation failed; Host unaffected")

    def _create_episode(self, experience: CanonicalExperience) -> Episode:
        now = experience.event.occurred_at
        episode_id = make_episode_id(experience.event.session_id, experience.event.event_id)
        return self.store.create_episode(
            Episode(
                episode_id=episode_id,
                scope_id=experience.event.session_id,
                state=EpisodeState.OPEN,
                root_event_id=experience.event.event_id,
                opened_at=now,
                last_activity_at=now,
                topic_hint=(experience.event.content or "").strip()[:80] or None,
                provenance=("episode_shadow_observer",),
            )
        )

    def _append_experience(self, ep: Episode, experience: CanonicalExperience) -> Episode:
        ref = EpisodeEventRef(
            ref_id=make_episode_event_ref_id(
                EpisodeEventKind.EXPERIENCE,
                source_event_id=experience.event.event_id,
            ),
            kind=EpisodeEventKind.EXPERIENCE,
            source_event_id=experience.event.event_id,
            observed_at=experience.event.occurred_at,
            actor_entity=experience.event.actor,
        )
        return self.store.append_event_ref(ep.episode_id, ref)

    def _append_proposal(self, ep: Episode, trace: BehaviorTrace) -> Episode:
        kind = EpisodeEventKind.COGNITIVE_PROPOSAL
        if trace.exit_reason is not None:
            if trace.exit_reason.value == "TRIGGER_NO":
                kind = EpisodeEventKind.TRIGGER_NO
            elif trace.exit_reason.value == "NO_INTENT":
                kind = EpisodeEventKind.NO_INTENT
            elif trace.exit_reason.value == "SILENCE_SELECTED":
                kind = EpisodeEventKind.INTENTIONAL_SILENCE
            elif trace.exit_reason.value == "GROUNDING_FAILED":
                kind = EpisodeEventKind.COGNITIVE_PROPOSAL
            elif trace.exit_reason.value == "RUNTIME_ERROR":
                kind = EpisodeEventKind.GUARD_BLOCKED
        ref = EpisodeEventRef(
            ref_id=make_episode_event_ref_id(
                kind,
                source_event_id=trace.event_id,
                trace_id=trace.trace_id,
            ),
            kind=kind,
            source_event_id=trace.event_id,
            trace_id=trace.trace_id,
            observed_at=trace.created_at,
            actor_entity=ep.participants[0] if ep.participants else None,
        )
        return self.store.append_event_ref(ep.episode_id, ref)

    @staticmethod
    def _host_ref(record: BehaviorExecutionRecord) -> EpisodeEventRef:
        trace = record.trace
        return EpisodeEventRef(
            ref_id=make_episode_event_ref_id(
                EpisodeEventKind.HOST_OUTPUT,
                source_event_id=trace.event_id,
                trace_id=trace.trace_id,
                execution_record_id=f"{trace.trace_id}:{record.revision}",
            ),
            kind=EpisodeEventKind.HOST_OUTPUT,
            source_event_id=trace.event_id,
            trace_id=trace.trace_id,
            execution_record_id=f"{trace.trace_id}:{record.revision}",
            observed_at=record.updated_at,
        )
