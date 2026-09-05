"""Bounded, runtime-owned Episode lifecycle administration.

This owner decides only durable Episode state transitions.  It deliberately
does not interpret content, call a model, or implement Review/P2/P2r0/P2r.1
logic; completion remains owned by the existing production coordinator.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from .episode import Episode, EpisodeState
from .episode_store import EpisodeStore
from .outcome import OutcomeObservation

logger = logging.getLogger(__name__)

OPEN_INACTIVITY = timedelta(minutes=15)
SOFT_CLOSE_GRACE = timedelta(minutes=15)
SCAN_INTERVAL_SECONDS = 60


class EpisodeLifecycleOwnerV1:
    """One bounded periodic owner for durable Episode lifecycle transitions."""

    owner = "Episode Lifecycle Owner V1"

    def __init__(
        self,
        store: EpisodeStore,
        *,
        complete_finalized: Callable[[Episode, tuple[OutcomeObservation, ...]], object | None],
        completion_satisfied: Callable[[Episode, tuple[OutcomeObservation, ...]], bool],
        now: Callable[[], datetime] | None = None,
        scan_interval_seconds: int = SCAN_INTERVAL_SECONDS,
    ) -> None:
        if not isinstance(store, EpisodeStore):
            raise TypeError("EpisodeLifecycleOwnerV1 requires an EpisodeStore")
        if not callable(complete_finalized) or not callable(completion_satisfied):
            raise TypeError("EpisodeLifecycleOwnerV1 requires completion callbacks")
        if type(scan_interval_seconds) is not int or scan_interval_seconds <= 0:
            raise ValueError("scan_interval_seconds must be a positive int")
        self._store = store
        self._complete_finalized = complete_finalized
        self._completion_satisfied = completion_satisfied
        self._now = now or (lambda: datetime.now().astimezone())
        self._scan_interval_seconds = scan_interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()

    @property
    def task(self) -> asyncio.Task[None] | None:
        return self._task

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        """Start at most one bounded periodic task; repeated starts are safe."""
        if self.running:
            return
        self._stopped.clear()
        self._task = asyncio.create_task(
            self._run(), name="iris-episode-lifecycle-owner-v1"
        )

    async def shutdown(self) -> None:
        """Stop the one periodic task without changing any Episode state."""
        task = self._task
        if task is None:
            return
        self._stopped.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    async def _run(self) -> None:
        while not self._stopped.is_set():
            try:
                self.scan_once()
            except Exception:
                logger.exception("Episode lifecycle scan failed closed")
            try:
                await asyncio.wait_for(
                    self._stopped.wait(), timeout=self._scan_interval_seconds
                )
            except TimeoutError:
                pass

    def scan_once(self, *, now: datetime | None = None) -> None:
        """Perform one deterministic bounded scan over the current store state."""
        scan_time = now or self._now()
        for episode in self._store.all_episodes():
            self._scan_episode(episode, scan_time)

    def _scan_episode(self, episode: Episode, now: datetime) -> None:
        if episode.state in (EpisodeState.OPEN, EpisodeState.INTERRUPTED):
            if now - episode.last_activity_at >= OPEN_INACTIVITY:
                self._store.transition_state(
                    episode.episode_id,
                    EpisodeState.SOFT_CLOSED,
                    reason="episode_lifecycle_inactivity",
                    at=now,
                    preserve_last_activity=True,
                )
            return

        if episode.state is EpisodeState.SOFT_CLOSED:
            # Both values are durable Episode facts.  A genuine event attached
            # while SOFT_CLOSED updates last_activity_at and extends the grace;
            # lifecycle/restart transitions explicitly do not.
            anchor = max(
                episode.soft_closed_at or episode.last_activity_at,
                episode.last_activity_at,
            )
            if now - anchor < SOFT_CLOSE_GRACE:
                return
            finalized = self._store.transition_state(
                episode.episode_id,
                EpisodeState.FINALIZED,
                reason="episode_lifecycle_grace_elapsed",
                at=now,
                preserve_last_activity=True,
            )
            self._complete_if_needed(finalized)
            return

        if episode.state is EpisodeState.FINALIZED:
            self._complete_if_needed(episode)

    def _complete_if_needed(self, episode: Episode) -> None:
        # This is the J1 durable cutoff, never all current Outcomes and never
        # OutcomeObservation.observed_at ordering.
        finalized_outcomes = self._finalized_outcomes(episode.episode_id)
        try:
            if self._completion_satisfied(episode, finalized_outcomes):
                return
        except Exception:
            logger.exception("Episode completion status could not be verified")
            return
        try:
            self._complete_finalized(episode, finalized_outcomes)
        except Exception:
            logger.exception("Episode completion failed after durable finalization")

    def _finalized_outcomes(self, episode_id: str) -> tuple[OutcomeObservation, ...]:
        getter = getattr(self._store, "get_finalized_outcomes", None)
        if not callable(getter):
            # The production owner must not approximate J1's durable cut-off.
            raise TypeError("EpisodeStore lacks durable finalized-outcome authority")
        outcomes = getter(episode_id)
        if not isinstance(outcomes, tuple) or not all(
            type(item) is OutcomeObservation for item in outcomes
        ):
            raise TypeError("EpisodeStore returned invalid finalized-outcome authority")
        return outcomes


__all__ = [
    "OPEN_INACTIVITY",
    "SCAN_INTERVAL_SECONDS",
    "SOFT_CLOSE_GRACE",
    "EpisodeLifecycleOwnerV1",
]
