"""Read-only adapter from the existing Iris proactive state into P0 signals."""

from __future__ import annotations

from typing import Any

from .contracts import LegacyProactiveSignals


class LegacyIrisProactiveSignalAdapter:
    """Maps legacy fields without calling state mutation or changing legacy policy."""

    owner = "Legacy Iris Proactive Signal Adapter"

    def __init__(self, state_manager: Any | None = None) -> None:
        self._state_manager = state_manager

    def read(self, event: Any) -> LegacyProactiveSignals:
        """Synchronous compatibility read for unit callers without a Legacy lock."""
        return self._read_snapshot(event)

    async def read_consistent(self, event: Any) -> LegacyProactiveSignals:
        """Copy Legacy fields under its owner lock without advancing its state machine."""
        group_id = event.get_group_id() if hasattr(event, "get_group_id") else None
        get_lock = getattr(self._state_manager, "get_lock", None)
        if group_id and callable(get_lock):
            async with get_lock(group_id):
                return self._read_snapshot(event)
        return self._read_snapshot(event)

    def _read_snapshot(self, event: Any) -> LegacyProactiveSignals:
        info = event.get_extra("iris_decision") if hasattr(event, "get_extra") else None
        info = info if isinstance(info, dict) else {}
        group_id = event.get_group_id() if hasattr(event, "get_group_id") else None
        data = None
        # Access the existing in-memory state directly to keep this adapter read-only;
        # read_consistent() calls this while the Legacy owner lock is held.
        groups = getattr(self._state_manager, "_groups", None)
        if group_id and isinstance(groups, dict):
            data = groups.get(group_id)

        threshold = {
            "message_count": getattr(data, "msg_count", None),
            "backoff_level": getattr(data, "backoff_level", None),
        }
        state_value = getattr(getattr(data, "state", None), "value", "")
        return LegacyProactiveSignals(
            activation_signal=info.get("motive") if isinstance(info.get("motive"), str) else None,
            willingness=getattr(data, "willingness", None),
            threshold={key: value for key, value in threshold.items() if value is not None},
            cooldown=state_value == "cooldown",
            consecutive_reply_penalty=max(0, int(getattr(data, "consecutive_replies", 0) or 0)),
            skip_signal=bool(info.get("skip_signal", False)),
            topic_drift_signal=bool(info.get("drifted", False)),
            post_evaluation_signal=bool(info.get("post_evaluation_signal", False)),
        )
