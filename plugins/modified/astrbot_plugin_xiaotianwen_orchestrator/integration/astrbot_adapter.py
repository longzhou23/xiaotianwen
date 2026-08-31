"""Explicit AstrBot observation adapter; it never monkey-patches the host."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..contracts.validation import ContractValidationError, require_identifier
from .observer import ObservationAdapter, RuntimeObservation


def _attr(value: object, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    try:
        candidate = getattr(value, name)
    except Exception:
        return default
    return default if callable(candidate) else candidate


def _count(value: object) -> int:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return len(value)
    return 0


def _tool_count(value: object) -> int:
    """Count both generic tool_calls and AstrBot's tools_call_* fields."""

    direct = _attr(value, "tool_calls", None)
    if direct is not None:
        count = _count(direct)
        if count:
            return count
    for name in ("tools_call_ids", "tools_call_name", "tools_call_args"):
        count = _count(_attr(value, name, ()))
        if count:
            return count
    direct_tool_set = _count(_attr(value, "tools", ()))
    if direct_tool_set:
        return direct_tool_set
    tool_set = _attr(value, "func_tool", None)
    return _count(_attr(tool_set, "tools", ()))


def _usage(value: object) -> dict[str, int]:
    names = (
        "input_tokens",
        "prompt_tokens",
        "output_tokens",
        "completion_tokens",
        "total_tokens",
        "cached_tokens",
        "input_other",
        "input_cached",
        "output",
    )
    result: dict[str, int] = {}
    for name in names:
        raw = _attr(value, name)
        if isinstance(raw, bool):
            continue
        if isinstance(raw, int) and raw >= 0:
            result[name] = raw
    return result


class AstrBotObservationAdapter:
    """Bridge called by an isolated plugin/test hook at explicit boundaries."""

    def __init__(self, observer: ObservationAdapter) -> None:
        if not isinstance(observer, ObservationAdapter):
            raise ContractValidationError("AstrBot adapter requires ObservationAdapter")
        self.observer = observer

    def provider_request(
        self,
        request: object,
        *,
        request_id: str,
        role: str,
        model: str = "unknown",
        stream: bool | None = None,
        parent_request_id: str | None = None,
        timestamp: float | None = None,
    ) -> RuntimeObservation:
        request_id = require_identifier(request_id, "request_id")
        contexts = _attr(request, "contexts", ())
        tools = _attr(request, "tools", ())
        prompt = _attr(request, "prompt")
        resolved_model = model if model != "unknown" else _attr(request, "model", None) or "unknown"
        return self.observer.request_started(
            request_id,
            role=role,
            model=str(resolved_model),
            message_count=_count(contexts),
            stream=bool(_attr(request, "stream", False) if stream is None else stream),
            tool_count=_tool_count(tools) or _tool_count(_attr(request, "func_tool", None)),
            prompt=prompt if isinstance(prompt, str) else None,
            parent_request_id=parent_request_id,
            timestamp=timestamp,
        )

    def provider_response(
        self,
        response: object,
        *,
        request_id: str,
        role: str,
        parent_request_id: str | None = None,
        timestamp: float | None = None,
    ) -> RuntimeObservation:
        tool_call_count = _tool_count(response)
        finish_reason = _attr(response, "finish_reason", None) or _attr(response, "reason", None)
        if not finish_reason:
            finish_reason = "tool_calls" if tool_call_count else "stop"
        usage = _usage(_attr(response, "usage", None))
        return self.observer.request_completed(
            require_identifier(request_id, "request_id"),
            role=role,
            finish_reason=str(finish_reason),
            usage=usage,
            tool_call_count=tool_call_count,
            parent_request_id=parent_request_id,
            timestamp=timestamp,
        )

    def provider_error(self, *, request_id: str, role: str, error: BaseException | str, timestamp: float | None = None) -> RuntimeObservation:
        error_class = type(error).__name__ if isinstance(error, BaseException) else str(error).split(":", 1)[0]
        return self.observer.request_failed(
            require_identifier(request_id, "request_id"), role=role, error_class=error_class or "ProviderError", timestamp=timestamp
        )

    def log(
        self,
        level: str,
        message: str,
        *,
        capture_text: bool = False,
        request_id: str | None = None,
        timestamp: float | None = None,
    ) -> RuntimeObservation:
        return self.observer.log(
            level=level,
            message=message,
            capture_text=capture_text,
            request_id=request_id,
            timestamp=timestamp,
        )
