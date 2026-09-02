"""Situation-owned, short-lived state with no persistence, learning, or LLM."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import RLock

from .contracts import CanonicalExperience, ResolvedEvent, Situation, SituationFull, SituationLite


@dataclass(slots=True)
class _ScopeState:
    last_activity_at: object
    velocity: int = 0
    recent_self_action: str | None = None
    last_self_action_at: datetime | None = None
    episode_hint: str | None = None


class SituationBuilder:
    """Owns the P0 in-process situation cache and nothing else."""

    owner = "Situation Builder"

    _MAX_SCOPES = 512
    _MAX_EVENT_VIEWS = 2048

    def __init__(self) -> None:
        self._scopes: dict[str, _ScopeState] = {}
        self._event_views: dict[str, SituationLite] = {}
        self._lock = RLock()

    @staticmethod
    def _active(experience: CanonicalExperience) -> tuple[str, ...]:
        event = experience.event
        values = []
        if event.actor:
            values.append(event.actor.entity_id)
        values.extend(entity.entity_id for entity in event.mentioned_entities)
        if event.reply_to:
            values.append(event.reply_to.entity_id)
        return tuple(dict.fromkeys(values))

    def observe(self, experience: CanonicalExperience) -> SituationLite:
        """Update cheap state for every canonical event; no long summary is made."""
        with self._lock:
            event_key = f"{experience.event.session_id}:{experience.event.event_id}"
            cached = self._event_views.get(event_key)
            if cached is not None:
                return cached
            event = experience.event
            scope = event.session_id
            previous = self._scopes.get(scope)
            if previous is None:
                state = _ScopeState(last_activity_at=event.occurred_at, velocity=1)
                self._scopes[scope] = state
            else:
                elapsed = (event.occurred_at - previous.last_activity_at).total_seconds()
                state = previous
                if elapsed < 0:
                    # Out-of-order input timestamps must not move the recency clock
                    # backwards or silently reset velocity to 1.
                    state.velocity = state.velocity + 1
                else:
                    state.velocity = state.velocity + 1 if elapsed <= 60 else 1
                    state.last_activity_at = event.occurred_at

            topic = event.content.strip()[:80] or None
            state.episode_hint = f"episode:{scope}:{event.event_id}" if state.episode_hint is None else state.episode_hint
            reply_chain = (event.reply_to.entity_id,) if event.reply_to else ()
            lite = SituationLite(
                scope_id=scope,
                channel=event.mode,
                active_entities=self._active(experience),
                reply_chain=reply_chain,
                recent_self_action=state.recent_self_action,
                # No recency window is frozen.  Once an action exists this is
                # deliberately unknown rather than a permanent false fact.
                self_recently_spoke=False if state.last_self_action_at is None else None,
                current_topic_hint=topic,
                message_velocity=state.velocity,
                last_activity_at=state.last_activity_at,
                ongoing_episode_hint=state.episode_hint,
                last_self_action_at=state.last_self_action_at,
            )
            self._event_views[event_key] = lite
            self.cleanup()
            return lite

    def record_self_action(
        self, experience: CanonicalExperience, action: str, *, occurred_at: datetime
    ) -> None:
        """Record an observed output action without inventing a recency window."""
        with self._lock:
            state = self._scopes.get(experience.event.session_id)
            if state is None:
                state = _ScopeState(last_activity_at=experience.event.occurred_at, velocity=1)
                self._scopes[experience.event.session_id] = state
            state.recent_self_action = action
            state.last_self_action_at = occurred_at

    def cleanup(self) -> None:
        """Bound the in-process cache; this is capacity management, not Episode expiry."""
        while len(self._event_views) > self._MAX_EVENT_VIEWS:
            self._event_views.pop(next(iter(self._event_views)))
        if len(self._scopes) > self._MAX_SCOPES:
            oldest = sorted(self._scopes, key=lambda scope: self._scopes[scope].last_activity_at)
            for scope in oldest[: len(self._scopes) - self._MAX_SCOPES]:
                self._scopes.pop(scope, None)

    def build_full(self, experience: CanonicalExperience, lite: SituationLite) -> SituationFull:
        """Construct the frozen read-only view only after Trigger YES.

        Affect, relationship, BehaviouralPrior and Persona have no P0 writer;
        their empty mappings make that absence explicit instead of inventing data.
        """
        return SituationFull(
            experience=experience,
            lite=lite,
            runtime_memory_view=(),
            committed_affect={},
            committed_relationship={},
            behavioral_prior={},
            persona_read_only={},
        )

    def build(self, event: ResolvedEvent, previous: Situation | None = None) -> Situation:
        active = []
        if event.actor is not None:
            active.append(event.actor.entity_id)
        active.extend(entity.entity_id for entity in event.mentioned_entities)
        if event.reply_to is not None:
            active.append(event.reply_to.entity_id)
        active_entities = tuple(dict.fromkeys(active))

        if event.reply_to is not None:
            focus_type = "reply"
        elif event.mentioned_entities:
            focus_type = "mention"
        else:
            focus_type = "message"

        # No LLM topic extraction in P0.  Content remains an event-local summary.
        summary = event.content.strip()[:240]
        return Situation(
            episode_id=previous.episode_id if previous is not None else f"situation:{event.event_id}",
            shared_focus_type=focus_type,
            shared_focus_summary=summary,
            mode=event.mode,
            active_entities=active_entities,
            current_topic=(),
            self_already_spoke=previous.self_already_spoke if previous else False,
            self_last_action=previous.self_last_action if previous else None,
            self_last_action_at=previous.self_last_action_at if previous else None,
            unresolved_items=(),
            updated_at=event.occurred_at,
        )
