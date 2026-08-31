"""Safe operational contracts for layered health and recovery checks."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
from typing import Iterable

from ..contracts.validation import ContractValidationError, require_non_empty_string, require_positive_int


class HealthState(str, Enum):
    CONNECTED = "CONNECTED"
    NOT_CONNECTED = "NOT_CONNECTED"
    UNKNOWN = "UNKNOWN"
    FAILED = "FAILED"


_HEALTH_LAYERS = ("container", "webui", "qq_login", "onebot", "astrbot", "min_send")


@dataclass(frozen=True, slots=True)
class ServiceHealthSnapshot:
    service: str
    layers: tuple[tuple[str, HealthState], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "service", require_non_empty_string(self.service, "service"))
        names = [name for name, _ in self.layers]
        if len(names) != len(set(names)):
            raise ContractValidationError("health layer names must be unique")
        unknown = [name for name in names if name not in _HEALTH_LAYERS]
        if unknown:
            raise ContractValidationError(f"unsupported health layer: {unknown[0]}")
        if any(not isinstance(state, HealthState) for _, state in self.layers):
            raise ContractValidationError("health states must be HealthState values")

    @property
    def runtime_usable(self) -> bool:
        required = {"container", "astrbot"}
        return all(dict(self.layers).get(name) is HealthState.CONNECTED for name in required)

    @property
    def account_usable(self) -> bool:
        required = {"qq_login", "onebot", "min_send"}
        return all(dict(self.layers).get(name) is HealthState.CONNECTED for name in required)

    @property
    def overall(self) -> str:
        values = dict(self.layers).values()
        if any(state is HealthState.FAILED for state in values):
            return "FAILED"
        if any(state is not HealthState.CONNECTED for state in values):
            return "NOT_VERIFIED"
        return "READY"

    def to_dict(self) -> dict[str, object]:
        return {
            "service": self.service,
            "overall": self.overall,
            "runtime_usable": self.runtime_usable,
            "account_usable": self.account_usable,
            "layers": {name: state.value for name, state in self.layers},
        }


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3

    def __post_init__(self) -> None:
        object.__setattr__(self, "max_attempts", require_positive_int(self.max_attempts, "max_attempts"))

    def next_action(self, *, attempt: int, failed_layer: str) -> str:
        if failed_layer == "qq_login" and attempt >= self.max_attempts:
            return "MANUAL_INTERVENTION"
        return "RETRY" if attempt < self.max_attempts else "STOP"


@dataclass(frozen=True, slots=True)
class SQLiteBackupSet:
    main: str
    wal: str
    shm: str

    @classmethod
    def for_main(cls, main: str) -> "SQLiteBackupSet":
        if not isinstance(main, str) or not main.strip():
            raise ContractValidationError("SQLite main path must be non-empty")
        if not main.lower().endswith(".db"):
            raise ContractValidationError("SQLite main path must end with .db")
        return cls(main, f"{main}-wal", f"{main}-shm")

    def paths(self) -> tuple[str, str, str]:
        return self.main, self.wal, self.shm


@dataclass(frozen=True, slots=True)
class BackupManifest:
    source: str
    files: tuple[tuple[str, int, str], ...]
    schema_version: int = 1

    @classmethod
    def from_metadata(cls, source: str, files: Iterable[tuple[str, int, str]]) -> "BackupManifest":
        normalized: list[tuple[str, int, str]] = []
        for path, size, digest in files:
            path = require_non_empty_string(path, "backup path").replace("\\", "/")
            parsed = PurePosixPath(path)
            if parsed.is_absolute() or ".." in parsed.parts or any(part in {"private", "secrets", ".ssh"} for part in parsed.parts):
                raise ContractValidationError("backup manifest path is outside the allowed relative scope")
            if type(size) is not int or size < 0:
                raise ContractValidationError("backup file size must be non-negative")
            digest = require_non_empty_string(digest, "sha256")
            if len(digest) != 64 or any(char not in "0123456789abcdefABCDEF" for char in digest):
                raise ContractValidationError("backup sha256 must be a 64-character hex digest")
            normalized.append((path, size, digest.lower()))
        return cls(require_non_empty_string(source, "source"), tuple(normalized))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "file_count": len(self.files),
            "files": [{"path": path, "size": size, "sha256": digest} for path, size, digest in self.files],
        }


def audit_active_adapter_names(names: Iterable[str]) -> tuple[str, ...]:
    """Return active NapCat references; historical documentation is out of scope."""

    return tuple(name for name in names if "napcat" in str(name).lower())
