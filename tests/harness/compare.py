"""Structural baseline comparison for redacted offline observations."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

from .config import FRAMEWORK_VERSION
from .path_policy import RepositoryPathPolicy
from .redact import redact_value


class DifferenceLevel(str, Enum):
    BLOCKER = "BLOCKER"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    PERFORMANCE = "PERFORMANCE"
    INFO = "INFO"


@dataclass(frozen=True, slots=True)
class Difference:
    path: str
    level: DifferenceLevel
    expected: Any
    actual: Any
    message: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["level"] = self.level.value
        return redact_value(payload)


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    case_id: str
    baseline_name: str
    candidate_name: str
    differences: tuple[Difference, ...]
    baseline_present: bool = True

    @property
    def has_blocker(self) -> bool:
        return any(item.level is DifferenceLevel.BLOCKER for item in self.differences)

    @property
    def status(self) -> str:
        if not self.baseline_present:
            return "NOT_VERIFIED"
        return "FAILED" if self.has_blocker else "PASS"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "case_id": self.case_id,
            "baseline_name": self.baseline_name,
            "candidate_name": self.candidate_name,
            "baseline_present": self.baseline_present,
            "status": self.status,
            "differences": [item.to_dict() for item in self.differences],
        }


_IGNORE_KEYS = frozenset({"run_id", "generated_at", "duration_ms", "wall_clock", "artifact_path"})
_BLOCKER_TOKENS = frozenset(
    {
        "main_reply_requests",
        "deliveries",
        "write_tool_calls",
        "duplicate_write_tool_attempts",
        "route",
        "security_safe",
        "audit_before_delivery",
        "late_delivery_count",
        "invalid_tool_call_ids",
        "network_attempts",
        "sandbox_violation",
        "capture_mode",
    }
)
_REVIEW_TOKENS = frozenset({"context", "tool", "image", "media", "call_id", "model", "usage", "fallback"})
_PERFORMANCE_TOKENS = frozenset({"token", "p50", "p95", "duration", "latency", "chars", "bytes"})


def _difference_level(path: str) -> DifferenceLevel:
    tokens = {item for item in re.split(r"[.\[\]_/-]+", path.lower()) if item}
    if tokens & _BLOCKER_TOKENS or any(token in path.lower() for token in _BLOCKER_TOKENS):
        return DifferenceLevel.BLOCKER
    if tokens & _REVIEW_TOKENS or any(token in path.lower() for token in _REVIEW_TOKENS):
        return DifferenceLevel.REVIEW_REQUIRED
    if tokens & _PERFORMANCE_TOKENS or any(token in path.lower() for token in _PERFORMANCE_TOKENS):
        return DifferenceLevel.PERFORMANCE
    return DifferenceLevel.INFO


def canonical_observation(result: Any) -> dict[str, Any]:
    """Normalize a ReplayResult-like object to stable comparison fields only."""

    requests = getattr(result, "requests", ())
    tools = getattr(result, "tools", ())
    outputs = getattr(result, "outputs", ())
    trace = getattr(result, "trace", ())
    return redact_value(
        {
            "schema_version": 1,
            "framework_version": FRAMEWORK_VERSION,
            "case_id": getattr(result, "case_id", "unknown"),
            "summary": dict(getattr(result, "summary", {})),
            "requests": [
                {
                    "role": item.get("role"),
                    "status": item.get("status"),
                    "stream": bool(item.get("payload", {}).get("stream", False)),
                    "finish_reason": item.get("response", {}).get("finish_reason"),
                }
                for item in requests
            ],
            "tools": [
                {"name": item.get("name"), "effect": item.get("effect"), "status": item.get("status")}
                for item in tools
            ],
            "outputs": [
                {"stage": item.get("stage"), "type": item.get("type"), "delivery": bool(item.get("delivery_id"))}
                for item in outputs
            ],
            "trace_kinds": [item.get("kind") for item in trace],
        }
    )


def _compare(expected: Any, actual: Any, path: str, output: list[Difference]) -> None:
    if isinstance(expected, Mapping) and isinstance(actual, Mapping):
        keys = sorted(set(expected) | set(actual), key=str)
        for key in keys:
            if str(key) in _IGNORE_KEYS:
                continue
            nested = f"{path}.{key}"
            if key not in expected:
                output.append(Difference(nested, _difference_level(nested), "<missing>", actual[key], "unexpected field"))
            elif key not in actual:
                output.append(Difference(nested, _difference_level(nested), expected[key], "<missing>", "missing field"))
            else:
                _compare(expected[key], actual[key], nested, output)
        return
    if isinstance(expected, Sequence) and not isinstance(expected, (str, bytes, bytearray)) and isinstance(actual, Sequence) and not isinstance(actual, (str, bytes, bytearray)):
        if len(expected) != len(actual):
            output.append(Difference(path, _difference_level(path), len(expected), len(actual), "sequence length changed"))
        for index, (old, new) in enumerate(zip(expected, actual)):
            _compare(old, new, f"{path}[{index}]", output)
        return
    if expected != actual:
        output.append(Difference(path, _difference_level(path), expected, actual, "value changed"))


def compare_observations(
    *,
    case_id: str,
    baseline: Mapping[str, Any] | None,
    candidate: Mapping[str, Any],
    baseline_name: str = "approved",
    candidate_name: str = "current",
) -> ComparisonResult:
    if baseline is None:
        return ComparisonResult(case_id, baseline_name, candidate_name, (), baseline_present=False)
    differences: list[Difference] = []
    _compare(redact_value(dict(baseline)), redact_value(dict(candidate)), "$", differences)
    return ComparisonResult(case_id, baseline_name, candidate_name, tuple(differences), baseline_present=True)


class BaselineStore:
    """Explicit-only committed Golden storage; normal runs only read it."""

    def __init__(self, repository_root: Path) -> None:
        self.root = repository_root / "tests" / "baselines" / "approved"

    @staticmethod
    def _path(case_id: str) -> str:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,119}", case_id):
            raise ValueError("invalid case id for baseline")
        return f"{case_id}.json"

    def load(self, case_id: str) -> dict[str, Any] | None:
        path = self.root / self._path(case_id)
        if not path.is_file():
            return None
        RepositoryPathPolicy(self.root.parents[2]).assert_safe_read(path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError(f"invalid approved baseline: {path}")
        observation = payload.get("observation")
        if not isinstance(observation, dict):
            raise ValueError(f"approved baseline has no observation: {path}")
        return observation

    def write_approved(
        self,
        *,
        case_id: str,
        observation: Mapping[str, Any],
        reason: str,
        source_ref: str | None,
    ) -> Path:
        """Write only after the caller has performed an explicit approval action."""

        if not reason.strip():
            raise ValueError("baseline approval requires a non-empty reason")
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / self._path(case_id)
        payload = redact_value(
            {
                "schema_version": 1,
                "framework_version": FRAMEWORK_VERSION,
                "case_id": case_id,
                "approval_reason": reason.strip(),
                "source_ref": source_ref,
                "observation": dict(observation),
            }
        )
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path
