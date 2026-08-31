"""One explicit route policy table and redacted latency/token metrics."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from ..contracts import ALLOWED_ROUTES, ContractValidationError
from ..contracts.validation import require_non_negative_int, require_positive_int


@dataclass(frozen=True, slots=True)
class RouteTuning:
    reasoning_effort: str
    max_output_tokens: int
    streaming: bool = False
    status_after_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.reasoning_effort not in {"none", "low", "medium", "high"}:
            raise ContractValidationError("unsupported reasoning_effort")
        object.__setattr__(
            self,
            "max_output_tokens",
            require_positive_int(self.max_output_tokens, "max_output_tokens"),
        )
        if type(self.streaming) is not bool:
            raise ContractValidationError("streaming must be boolean")
        if self.status_after_seconds is not None and self.status_after_seconds <= 0:
            raise ContractValidationError("status_after_seconds must be positive")


DEFAULT_ROUTE_POLICY = {
    "chat": RouteTuning("low", 2_048, False, 3.0),
    "agent": RouteTuning("medium", 4_096, False, 3.0),
    "decision": RouteTuning("none", 128, False, None),
    "proactive": RouteTuning("low", 192, False, None),
    "vision": RouteTuning("low", 512, False, 3.0),
    "background": RouteTuning("low", 1_024, False, None),
}


@dataclass(frozen=True, slots=True)
class RoutePolicy:
    route: str
    tuning: RouteTuning


class RoutePolicyTable:
    def __init__(self, overrides: dict[str, RouteTuning] | None = None) -> None:
        self._table = dict(DEFAULT_ROUTE_POLICY)
        for route, tuning in (overrides or {}).items():
            if route not in ALLOWED_ROUTES or not isinstance(tuning, RouteTuning):
                raise ContractValidationError("invalid route policy override")
            self._table[route] = tuning

    def for_route(self, route: str) -> RoutePolicy:
        if route not in ALLOWED_ROUTES:
            raise ContractValidationError("unknown route")
        return RoutePolicy(route, self._table[route])


class RouteMetrics:
    """Bounded numeric observations; no prompts, outputs or user IDs."""

    def __init__(self, *, max_samples_per_route: int = 1_000) -> None:
        self.max_samples = require_positive_int(max_samples_per_route, "max_samples_per_route")
        self._samples: dict[str, list[tuple[float, int, int, bool]]] = {}

    def record(
        self,
        route: str,
        *,
        latency_ms: float,
        input_tokens: int,
        output_tokens: int,
        quality_passed: bool,
    ) -> None:
        if route not in ALLOWED_ROUTES:
            raise ContractValidationError("unknown route")
        if latency_ms < 0:
            raise ContractValidationError("latency_ms must be non-negative")
        sample = (
            float(latency_ms),
            require_non_negative_int(input_tokens, "input_tokens"),
            require_non_negative_int(output_tokens, "output_tokens"),
            bool(quality_passed),
        )
        samples = self._samples.setdefault(route, [])
        samples.append(sample)
        if len(samples) > self.max_samples:
            del samples[: len(samples) - self.max_samples]

    def summary(self, route: str) -> dict[str, int | float]:
        samples = self._samples.get(route, [])
        if not samples:
            return {"samples": 0, "quality_pass_rate": 0.0, "input_tokens": 0, "output_tokens": 0, "p95_ms": 0.0}
        sorted_latency = sorted(sample[0] for sample in samples)
        p95_index = max(0, ceil(len(sorted_latency) * 0.95) - 1)
        return {
            "samples": len(samples),
            "quality_pass_rate": sum(1 for sample in samples if sample[3]) / len(samples),
            "input_tokens": sum(sample[1] for sample in samples),
            "output_tokens": sum(sample[2] for sample in samples),
            "p95_ms": sorted_latency[p95_index],
        }
