"""Deterministic P0 Trigger Controller.

This controller only decides whether a behaviour loop merits activation.  It
does not choose content, participation, an Intent, or a response.  No LLM is
called, and all uncertain group messages fail closed.
"""

from __future__ import annotations

from .contracts import ExitReason, ResolvedEvent, TriggerDecision, TriggerSnapshot


class TriggerController:
    """Level-0 rules from the frozen architecture, intentionally conservative."""

    owner = "Trigger Controller"

    def __init__(self, *, self_entity: str) -> None:
        self.self_entity = self_entity

    def evaluate(self, event: ResolvedEvent) -> TriggerDecision:
        """Compatibility entry point for existing callers/tests."""
        if event.mode == "private":
            return TriggerDecision(True, "private message", 3)
        if any(entity.entity_id == self.self_entity for entity in event.mentioned_entities):
            return TriggerDecision(True, "explicit mention of SELF", 3)
        if event.reply_to is not None and event.reply_to.entity_id == self.self_entity:
            return TriggerDecision(True, "reply to SELF", 3)
        return TriggerDecision(
            False,
            "no deterministic activation signal; ordinary group message fails closed",
            1,
            ExitReason.TRIGGER_NO,
        )

    def evaluate_snapshot(self, snapshot: TriggerSnapshot) -> TriggerDecision:
        """Use only frozen snapshot inputs; legacy values are modifiers, not writers."""
        direct = self.evaluate(snapshot.experience.event)
        legacy = snapshot.legacy_signals
        backoff = legacy.threshold.get("backoff_level", 0)
        backoff = backoff if isinstance(backoff, int) and not isinstance(backoff, bool) else 0
        modifier = max(0, min(backoff, 2))
        if direct.should_start_loop:
            if not modifier and not legacy.willingness:
                return direct
            willingness = f"; legacy willingness={legacy.willingness}" if legacy.willingness else ""
            return TriggerDecision(
                True,
                f"{direct.reason}{willingness}; threshold backoff modifier={modifier}",
                max(1, direct.score - modifier),
            )

        if legacy.activation_signal:
            score = max(1, 3 - modifier - min(legacy.consecutive_reply_penalty, 2))
            return TriggerDecision(
                True,
                f"legacy activation signal: {legacy.activation_signal}; threshold backoff modifier={modifier}",
                score,
            )
        return direct
