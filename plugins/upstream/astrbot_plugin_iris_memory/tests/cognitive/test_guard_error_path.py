"""P0.6 Guard error behavior must not silently fall through to Legacy."""

from unittest.mock import MagicMock

import pytest

import main
from iris_memory.cognitive.contracts import RuntimeMode


class _MutatingPreAdapter:
    def __init__(self, runtime: MagicMock, after_mode: RuntimeMode):
        self.runtime = runtime
        self.after_mode = after_mode

    def attach(self, event):
        self.runtime.runtime_mode = self.after_mode
        raise RuntimeError("broken adapter")


@pytest.mark.asyncio
async def test_guard_adapter_exception_stops_event_and_records_explicit_error(monkeypatch):
    runtime = MagicMock()
    runtime.runtime_mode = RuntimeMode.GUARD
    runtime.pre_adapter = _MutatingPreAdapter(runtime, RuntimeMode.SHADOW)
    monkeypatch.setattr(main, "get_cognitive_runtime", lambda: runtime)

    plugin = object.__new__(main.IrisMemoryPlugin)
    plugin._state = object()
    event = MagicMock()

    assert await plugin._handle_cognitive_behavior(event) is True
    event.set_extra.assert_called_once_with("iris_cognitive_runtime_error", "RUNTIME_ERROR")
    event.stop_event.assert_called_once()


@pytest.mark.asyncio
async def test_shadow_snapshot_survives_global_mode_change_before_error(monkeypatch):
    runtime = MagicMock()
    runtime.runtime_mode = RuntimeMode.SHADOW
    runtime.pre_adapter = _MutatingPreAdapter(runtime, RuntimeMode.GUARD)
    monkeypatch.setattr(main, "get_cognitive_runtime", lambda: runtime)

    plugin = object.__new__(main.IrisMemoryPlugin)
    plugin._state = object()
    event = MagicMock()

    assert await plugin._handle_cognitive_behavior(event) is False
    event.stop_event.assert_not_called()
    # SHADOW failures are not recorded as GUARD RUNTIME_ERROR terminals.
    event.set_extra.assert_not_called()


@pytest.mark.asyncio
async def test_guard_snapshot_survives_global_mode_change_to_shadow_before_error(monkeypatch):
    runtime = MagicMock()
    runtime.runtime_mode = RuntimeMode.GUARD
    runtime.pre_adapter = _MutatingPreAdapter(runtime, RuntimeMode.SHADOW)
    monkeypatch.setattr(main, "get_cognitive_runtime", lambda: runtime)

    plugin = object.__new__(main.IrisMemoryPlugin)
    plugin._state = object()
    event = MagicMock()

    assert await plugin._handle_cognitive_behavior(event) is True
    event.stop_event.assert_called_once()
    event.set_extra.assert_called_once_with("iris_cognitive_runtime_error", "RUNTIME_ERROR")
