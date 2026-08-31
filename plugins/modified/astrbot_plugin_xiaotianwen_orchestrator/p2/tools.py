"""Conservative request-local tool effects, ordering and single-flight."""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..contracts import ToolExecutionPolicy
from ..contracts.validation import (
    ContractValidationError,
    JsonValue,
    canonical_json,
    ensure_json_value,
    require_identifier,
    require_non_empty_string,
    require_positive_int,
    sha256_text,
    structural_fingerprint,
)


_EFFECT_ALIASES = {"status": "write", "steal": "write"}
_SIDE_EFFECTS = frozenset({"send", "write"})
ToolHandler = Callable[["ToolCall"], object | Awaitable[object]]


def normalize_effect(effect: str) -> str:
    normalized = require_non_empty_string(effect, "effect").lower()
    return _EFFECT_ALIASES.get(normalized, normalized)


def trim_tool_result(value: object, *, tool_limit: int, provider_limit: int) -> str:
    """Trim at the tool boundary first, then apply the Provider fallback cap."""

    tool_limit = require_positive_int(tool_limit, "tool_limit")
    provider_limit = require_positive_int(provider_limit, "provider_limit")
    if isinstance(value, str):
        rendered = value
    else:
        checked = ensure_json_value(value, "tool result")
        rendered = canonical_json(checked)
    return rendered[:tool_limit][:provider_limit]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    policy: ToolExecutionPolicy
    tool_result_chars: int = 4_000

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", require_identifier(self.name, "tool name"))
        if not isinstance(self.policy, ToolExecutionPolicy):
            raise ContractValidationError("ToolSpec requires ToolExecutionPolicy")
        object.__setattr__(self, "tool_result_chars", require_positive_int(self.tool_result_chars, "tool_result_chars"))


@dataclass(frozen=True, slots=True)
class ToolCall:
    call_id: str
    name: str
    arguments: Mapping[str, JsonValue]
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "call_id", require_identifier(self.call_id, "call_id"))
        object.__setattr__(self, "name", require_identifier(self.name, "tool name"))
        if not isinstance(self.arguments, Mapping):
            raise ContractValidationError("tool arguments must be a mapping")
        checked = ensure_json_value(dict(self.arguments), "tool arguments")
        if not isinstance(checked, dict):
            raise ContractValidationError("tool arguments must be a JSON object")
        object.__setattr__(self, "arguments", checked)
        if self.idempotency_key is not None:
            object.__setattr__(self, "idempotency_key", require_identifier(self.idempotency_key, "idempotency_key"))

    @property
    def arguments_fingerprint(self) -> str:
        return structural_fingerprint(dict(self.arguments))


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    call_id: str
    name: str
    effect: str
    status: str
    result: str
    index: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "call_id", require_identifier(self.call_id, "call_id"))
        object.__setattr__(self, "name", require_identifier(self.name, "tool name"))
        object.__setattr__(self, "effect", require_non_empty_string(self.effect, "effect").lower())
        object.__setattr__(self, "status", require_non_empty_string(self.status, "status").lower())
        if not isinstance(self.result, str):
            raise ContractValidationError("tool result must be a string")
        object.__setattr__(self, "index", require_positive_int(self.index + 1, "index") - 1)

    def structural_summary(self) -> dict[str, object]:
        return {
            "call_id": self.call_id,
            "name": self.name,
            "effect": self.effect,
            "status": self.status,
            "result_chars": len(self.result),
            "result_hash": sha256_text(self.result),
            "index": self.index,
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._specs:
            raise ContractValidationError(f"tool already registered: {spec.name}")
        self._specs[spec.name] = spec

    def spec_for(self, name: str) -> ToolSpec:
        normalized = require_identifier(name, "tool name")
        return self._specs.get(
            normalized,
            ToolSpec(normalized, ToolExecutionPolicy.conservative_default()),
        )


class ToolExecutor:
    """Execute read tools in bounded groups and side effects serially."""

    def __init__(self, registry: ToolRegistry | None = None, *, max_read_concurrency: int = 3, now: Callable[[], float] | None = None) -> None:
        self.registry = registry or ToolRegistry()
        if type(max_read_concurrency) is not int or not 1 <= max_read_concurrency <= 3:
            raise ContractValidationError("max_read_concurrency must be between one and three")
        self.max_read_concurrency = max_read_concurrency
        self._now = now or time.monotonic
        self._read_cache: dict[tuple[str, str], tuple[float, str]] = {}
        self._read_flights: dict[tuple[str, str], asyncio.Task[str]] = {}
        self._effect_keys: set[tuple[str, str]] = set()
        self.max_observed_read_concurrency = 0
        self._active_reads = 0

    async def _invoke(self, handler: ToolHandler, call: ToolCall, spec: ToolSpec, *, provider_limit: int) -> str:
        value = handler(call)
        if inspect.isawaitable(value):
            value = await value
        return trim_tool_result(value, tool_limit=spec.tool_result_chars, provider_limit=provider_limit)

    async def _read_once(self, handler: ToolHandler, call: ToolCall, spec: ToolSpec, provider_limit: int) -> tuple[str, bool]:
        key = (call.name, call.arguments_fingerprint)
        now = self._now()
        cached = self._read_cache.get(key)
        if cached is not None and cached[0] > now:
            return cached[1], True
        task = self._read_flights.get(key)
        if task is not None:
            return await task, True
        task = asyncio.create_task(self._invoke(handler, call, spec, provider_limit=provider_limit))
        self._read_flights[key] = task
        try:
            result = await task
            if spec.policy.cache_ttl_seconds > 0:
                self._read_cache[key] = (self._now() + spec.policy.cache_ttl_seconds, result)
            return result, False
        finally:
            if self._read_flights.get(key) is task:
                self._read_flights.pop(key, None)

    async def _one(self, index: int, call: ToolCall, handler: ToolHandler, *, provider_limit: int) -> ToolExecutionResult:
        spec = self.registry.spec_for(call.name)
        effect = normalize_effect(spec.policy.effect)
        if effect in _SIDE_EFFECTS:
            key_material = call.idempotency_key or call.arguments_fingerprint
            identity = (call.name, key_material)
            if identity in self._effect_keys:
                return ToolExecutionResult(call.call_id, call.name, effect, "duplicate_suppressed", "", index)
            self._effect_keys.add(identity)
            try:
                result = await self._invoke(handler, call, spec, provider_limit=provider_limit)
            except Exception as exc:
                return ToolExecutionResult(call.call_id, call.name, effect, f"failed:{type(exc).__name__}", "", index)
            return ToolExecutionResult(call.call_id, call.name, effect, "completed", result, index)

        if spec.policy.can_run_in_parallel:
            self._active_reads += 1
            self.max_observed_read_concurrency = max(self.max_observed_read_concurrency, self._active_reads)
            try:
                result, reused = await self._read_once(handler, call, spec, provider_limit)
            except Exception as exc:
                return ToolExecutionResult(call.call_id, call.name, effect, f"failed:{type(exc).__name__}", "", index)
            finally:
                self._active_reads -= 1
            return ToolExecutionResult(call.call_id, call.name, effect, "deduplicated" if reused else "completed", result, index)

        try:
            result = await self._invoke(handler, call, spec, provider_limit=provider_limit)
        except Exception as exc:
            return ToolExecutionResult(call.call_id, call.name, effect, f"failed:{type(exc).__name__}", "", index)
        return ToolExecutionResult(call.call_id, call.name, effect, "completed", result, index)

    async def execute(self, calls: Sequence[ToolCall], handler: ToolHandler, *, provider_limit: int = 4_000) -> tuple[ToolExecutionResult, ...]:
        if not isinstance(calls, Sequence) or isinstance(calls, (str, bytes, bytearray)):
            raise ContractValidationError("tool calls must be a sequence")
        provider_limit = require_positive_int(provider_limit, "provider_limit")
        values = tuple(calls)
        if len({call.call_id for call in values}) != len(values):
            raise ContractValidationError("call_id must be unique within one request")
        results: list[ToolExecutionResult] = []
        index = 0
        while index < len(values):
            spec = self.registry.spec_for(values[index].name)
            if not spec.policy.can_run_in_parallel:
                results.append(await self._one(index, values[index], handler, provider_limit=provider_limit))
                index += 1
                continue
            end = index
            while end < len(values) and self.registry.spec_for(values[end].name).policy.can_run_in_parallel:
                end += 1
            for start in range(index, end, self.max_read_concurrency):
                batch = values[start : min(end, start + self.max_read_concurrency)]
                results.extend(
                    await asyncio.gather(
                        *(self._one(start + offset, call, handler, provider_limit=provider_limit) for offset, call in enumerate(batch))
                    )
                )
            index = end
        return tuple(sorted(results, key=lambda item: item.index))
