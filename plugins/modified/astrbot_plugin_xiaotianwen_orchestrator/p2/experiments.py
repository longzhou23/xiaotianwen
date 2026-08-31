"""Explicitly isolated P2 experiments; production is never an implicit target."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from ..contracts.validation import ContractValidationError, JsonValue, ensure_json_value, require_identifier, require_non_empty_string, require_positive_int


@dataclass(frozen=True, slots=True)
class ExperimentSpec:
    experiment_id: str
    feature: str
    branch: str
    session_id: str
    variable: str
    enabled: bool = False
    production: bool = False

    def __post_init__(self) -> None:
        for name in ("experiment_id", "branch", "session_id"):
            object.__setattr__(self, name, require_identifier(getattr(self, name), name))
        object.__setattr__(self, "feature", require_non_empty_string(self.feature, "feature"))
        object.__setattr__(self, "variable", require_non_empty_string(self.variable, "variable"))
        if type(self.enabled) is not bool or type(self.production) is not bool:
            raise ContractValidationError("experiment enabled/production must be boolean")
        if self.production:
            raise ContractValidationError("P2 experiments cannot target production")


@dataclass(frozen=True, slots=True)
class ExperimentObservation:
    experiment_id: str
    status: str
    metrics: Mapping[str, JsonValue]

    def to_dict(self) -> dict[str, object]:
        return {"experiment_id": self.experiment_id, "status": self.status, "metrics": dict(self.metrics)}


class ExperimentLedger:
    def __init__(self, spec: ExperimentSpec, *, max_observations: int = 1_000) -> None:
        self.spec = spec
        self.max_observations = require_positive_int(max_observations, "max_observations")
        self._observations: list[ExperimentObservation] = []
        self.aborted = False

    @property
    def runnable(self) -> bool:
        return self.spec.enabled and not self.aborted

    def record(self, *, status_code: int, metrics: Mapping[str, object] | None = None) -> ExperimentObservation:
        if type(status_code) is not int or status_code < 100 or status_code > 599:
            raise ContractValidationError("status_code must be an HTTP status integer")
        if not self.runnable:
            status = "DISABLED" if not self.spec.enabled else "ABORTED"
        elif status_code == 400:
            self.aborted = True
            status = "ABORTED_400"
        else:
            status = "COMPLETED" if 200 <= status_code < 300 else "FAILED"
        checked = ensure_json_value(dict(metrics or {}), "experiment metrics")
        if not isinstance(checked, dict):
            raise ContractValidationError("experiment metrics must be a mapping")
        observation = ExperimentObservation(self.spec.experiment_id, status, checked)
        self._observations.append(observation)
        if len(self._observations) > self.max_observations:
            del self._observations[: len(self._observations) - self.max_observations]
        return observation

    def snapshot(self) -> tuple[ExperimentObservation, ...]:
        return tuple(self._observations)
