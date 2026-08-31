"""Repeatable P2 performance summaries and non-sending gate templates."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Iterable

from ..contracts.validation import ContractValidationError, require_non_empty_string, require_positive_int


@dataclass(frozen=True, slots=True)
class PerformanceSample:
    route: str
    latency_ms: float
    input_tokens: int = 0
    output_tokens: int = 0
    request_count: int = 1
    delivery_count: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "route", require_non_empty_string(self.route, "route"))
        if type(self.latency_ms) not in (int, float) or self.latency_ms < 0:
            raise ContractValidationError("latency_ms must be a non-negative number")
        for name in ("input_tokens", "output_tokens", "request_count", "delivery_count"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ContractValidationError(f"{name} must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class PerformanceSummary:
    route: str
    sample_count: int
    p50_ms: float
    p95_ms: float
    input_tokens: int
    output_tokens: int
    requests: int
    deliveries: int
    minimum_samples: int

    @property
    def status(self) -> str:
        return "READY" if self.sample_count >= self.minimum_samples else "INSUFFICIENT_DATA"

    def to_dict(self) -> dict[str, object]:
        return {
            "route": self.route,
            "sample_count": self.sample_count,
            "p50_ms": self.p50_ms,
            "p95_ms": self.p95_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "requests": self.requests,
            "deliveries": self.deliveries,
            "minimum_samples": self.minimum_samples,
            "status": self.status,
        }


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * fraction + 0.999999) - 1))
    return ordered[index]


def summarize_performance(samples: Iterable[PerformanceSample], *, route: str, minimum_samples: int = 20) -> PerformanceSummary:
    route = require_non_empty_string(route, "route")
    minimum_samples = require_positive_int(minimum_samples, "minimum_samples")
    values = [sample for sample in samples if sample.route == route]
    latencies = [float(sample.latency_ms) for sample in values]
    return PerformanceSummary(
        route,
        len(values),
        float(median(latencies)) if latencies else 0.0,
        _percentile(latencies, 0.95),
        sum(sample.input_tokens for sample in values),
        sum(sample.output_tokens for sample in values),
        sum(sample.request_count for sample in values),
        sum(sample.delivery_count for sample in values),
        minimum_samples,
    )


def compare_performance(baseline: PerformanceSummary, candidate: PerformanceSummary, *, max_regression_ratio: float = 0.10) -> dict[str, object]:
    if baseline.route != candidate.route:
        raise ContractValidationError("performance comparison routes must match")
    if type(max_regression_ratio) not in (int, float) or max_regression_ratio < 0:
        raise ContractValidationError("max_regression_ratio must be non-negative")
    if baseline.status != "READY" or candidate.status != "READY":
        status = "INSUFFICIENT_DATA"
    elif baseline.p95_ms <= 0:
        status = "INFO"
    elif candidate.p95_ms > baseline.p95_ms * (1 + float(max_regression_ratio)):
        status = "BLOCKER"
    else:
        status = "PASS"
    return {
        "route": baseline.route,
        "status": status,
        "baseline_p95_ms": baseline.p95_ms,
        "candidate_p95_ms": candidate.p95_ms,
        "max_regression_ratio": float(max_regression_ratio),
    }


@dataclass(frozen=True, slots=True)
class CanarySlot:
    index: int
    turn_id: str
    expected: str = "observe_only"

    def to_dict(self) -> dict[str, object]:
        return {"index": self.index, "turn_id": self.turn_id, "expected": self.expected}


@dataclass(frozen=True, slots=True)
class CanaryPlan:
    count: int
    slots: tuple[CanarySlot, ...]
    sends_enabled: bool = False

    @classmethod
    def build(cls, *, count: int = 100, prefix: str = "canary") -> "CanaryPlan":
        count = require_positive_int(count, "count")
        prefix = require_non_empty_string(prefix, "prefix")
        slots = tuple(CanarySlot(index, f"{prefix}-{index:03d}") for index in range(1, count + 1))
        return cls(count, slots, False)

    def to_dict(self) -> dict[str, object]:
        return {"count": self.count, "sends_enabled": self.sends_enabled, "slots": [slot.to_dict() for slot in self.slots]}


@dataclass(frozen=True, slots=True)
class LongRunObservationTemplate:
    name: str
    duration_hours: int
    target: str
    required_metrics: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", require_non_empty_string(self.name, "name"))
        if type(self.duration_hours) is not int or self.duration_hours <= 0:
            raise ContractValidationError("duration_hours must be positive")
        object.__setattr__(self, "target", require_non_empty_string(self.target, "target"))
        object.__setattr__(self, "required_metrics", tuple(require_non_empty_string(item, "required_metric") for item in self.required_metrics))

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "duration_hours": self.duration_hours,
            "target": self.target,
            "required_metrics": list(self.required_metrics),
            "status": "NOT_STARTED",
        }


def default_long_run_templates() -> tuple[LongRunObservationTemplate, ...]:
    return (
        LongRunObservationTemplate(
            "orchestrator-shadow-24h",
            24,
            "isolated_or_authorized_shadow_only",
            ("route", "request_count", "delivery_count", "cache_hits", "p95_ms", "errors", "late_outputs"),
        ),
        LongRunObservationTemplate(
            "snowluma-72h",
            72,
            "authorized_snowluma_instance",
            ("container", "webui", "qq_login", "onebot", "astrbot", "min_send", "duplicate_events", "reconnects"),
        ),
    )
