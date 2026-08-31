"""Narrow read policy for P0-owned files and protected instance-path detection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class PathAccessViolation(PermissionError):
    """A harness operation attempted to access an instance or recovery path."""

    exit_code = 3


_PROTECTED_COMPONENTS = frozenset({"private", "secrets", "local-secrets", "recovery", ".ssh", "runtime"})


@dataclass(frozen=True, slots=True)
class RepositoryPathPolicy:
    """Allow checked-in public test inputs while rejecting protected data roots."""

    repository_root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "repository_root", self.repository_root.resolve())

    def assert_safe_read(self, path: Path) -> Path:
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(self.repository_root)
        except ValueError as exc:
            raise PathAccessViolation("P0 harness may not read outside its public repository root") from exc
        lowered = {part.lower() for part in relative.parts}
        if lowered & _PROTECTED_COMPONENTS:
            raise PathAccessViolation("P0 harness may not read private, secret, runtime or recovery data")
        return resolved

    def assert_safe_fixture_asset(self, path: Path) -> Path:
        resolved = self.assert_safe_read(path)
        allowed_root = (self.repository_root / "tests" / "fixtures" / "assets").resolve()
        try:
            resolved.relative_to(allowed_root)
        except ValueError as exc:
            raise PathAccessViolation("fixture assets must live under tests/fixtures/assets") from exc
        return resolved
