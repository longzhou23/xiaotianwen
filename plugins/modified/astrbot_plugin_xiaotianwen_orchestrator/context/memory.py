"""Iris query budgets, versioned caches and request-local provider memoization."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Generic, TypeVar

from ..contracts import ContextSection, ContractValidationError
from ..contracts.validation import require_non_empty_string
from .assembler import ContextAssemblyResult
from .budgets import ContextAssemblyPolicy


T = TypeVar("T")


def normalize_memory_query(value: str) -> str:
    text = require_non_empty_string(value, "query").lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def is_low_information(value: str) -> bool:
    without_mentions = re.sub(r"@[A-Za-z0-9_.:-]+", "", value)
    compact = re.sub(r"[\s@，。！？!?、,.~～]+", "", without_mentions)
    return len(compact) <= 1 or compact in {"戳", "嗯", "哦", "好"}


@dataclass(frozen=True, slots=True)
class MemoryQueryKey:
    normalized_query: str
    memory_version: str
    provider_version: str

    @classmethod
    def build(cls, query: str, memory_version: str, provider_version: str) -> "MemoryQueryKey":
        return cls(
            normalize_memory_query(query),
            require_non_empty_string(memory_version, "memory_version"),
            require_non_empty_string(provider_version, "provider_version"),
        )


@dataclass(frozen=True, slots=True)
class _TimedValue(Generic[T]):
    value: T
    expires_at: float


class AsyncSingleFlightCache(Generic[T]):
    """A short-TTL cache where concurrent identical queries share one loader."""

    def __init__(self, *, max_entries: int = 256) -> None:
        if type(max_entries) is not int or max_entries <= 0:
            raise ContractValidationError("max_entries must be positive")
        self.max_entries = max_entries
        self._values: dict[MemoryQueryKey, _TimedValue[T]] = {}
        self._inflight: dict[MemoryQueryKey, asyncio.Task[T]] = {}
        self._lock = asyncio.Lock()

    async def get_or_load(
        self,
        key: MemoryQueryKey,
        loader: Callable[[], Awaitable[T]],
        *,
        now: float,
        ttl_seconds: float,
    ) -> tuple[T, bool]:
        if ttl_seconds <= 0:
            raise ContractValidationError("ttl_seconds must be positive")
        async with self._lock:
            cached = self._values.get(key)
            if cached is not None and now < cached.expires_at:
                return cached.value, True
            task = self._inflight.get(key)
            owner = task is None
            if task is None:
                task = asyncio.create_task(loader())
                self._inflight[key] = task
        try:
            value = await task
        finally:
            if owner:
                async with self._lock:
                    self._inflight.pop(key, None)
        if owner:
            async with self._lock:
                self._values[key] = _TimedValue(value, now + ttl_seconds)
                while len(self._values) > self.max_entries:
                    self._values.pop(next(iter(self._values)))
        return value, not owner


@dataclass(frozen=True, slots=True)
class MemoryBudgetPolicy:
    group_total_chars: int = 8_000
    private_total_chars: int = 16_000
    group_source_caps: tuple[tuple[str, int], ...] = (
        ("short_history", 2_000),
        ("iris_l2", 2_000),
        ("iris_l3", 1_500),
        ("iris_profile", 1_000),
        ("iris_affection", 500),
    )
    private_source_caps: tuple[tuple[str, int], ...] = (
        ("short_history", 4_000),
        ("iris_l2", 4_000),
        ("iris_l3", 2_500),
        ("iris_profile", 2_000),
        ("iris_affection", 800),
    )

    def assembly_policy(self, session_id: str) -> ContextAssemblyPolicy:
        private = session_id.startswith("private:")
        return ContextAssemblyPolicy(
            total_budget_chars=self.private_total_chars if private else self.group_total_chars,
            source_caps=dict(self.private_source_caps if private else self.group_source_caps),
        )


class TurnContextMemo:
    """Build a provider chain once per request; permit at most one explicit refresh."""

    def __init__(self, *, max_requests: int = 1_000) -> None:
        self.max_requests = max_requests
        self._results: dict[str, ContextAssemblyResult] = {}
        self._refresh_used: set[str] = set()

    def get_or_build(
        self,
        request_id: str,
        builder: Callable[[], ContextAssemblyResult],
        *,
        memory_refresh: bool = False,
    ) -> tuple[ContextAssemblyResult, bool]:
        if request_id in self._results and not memory_refresh:
            return self._results[request_id], True
        if memory_refresh and request_id in self._refresh_used:
            raise ContractValidationError("memory_refresh is allowed at most once per request")
        result = builder()
        if not isinstance(result, ContextAssemblyResult):
            raise ContractValidationError("context builder must return ContextAssemblyResult")
        self._results[request_id] = result
        if memory_refresh:
            self._refresh_used.add(request_id)
        while len(self._results) > self.max_requests:
            oldest = next(iter(self._results))
            self._results.pop(oldest, None)
            self._refresh_used.discard(oldest)
        return result, False


def deduplicate_relationship_sections(sections: tuple[ContextSection, ...]) -> tuple[ContextSection, ...]:
    """Prefer Iris authority when generic context duplicates relationship data."""

    iris_relationship = any(
        section.source in {"iris_l3", "iris_profile", "iris_affection"}
        for section in sections
    )
    if not iris_relationship:
        return sections
    return tuple(
        section
        for section in sections
        if section.source not in {"relationship_summary", "affection_summary"}
    )
