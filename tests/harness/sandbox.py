"""Validated write boundary for disposable test artifacts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class SandboxViolation(RuntimeError):
    """A test attempted to write outside its owned artifact directory."""

    exit_code = 3


_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")


@dataclass(frozen=True, slots=True)
class RunSandbox:
    """A run-owned directory under artifacts/test-runs and nothing else."""

    repository_root: Path
    run_id: str
    root: Path

    @classmethod
    def create(cls, repository_root: Path, run_id: str) -> "RunSandbox":
        if not _RUN_ID.fullmatch(run_id):
            raise SandboxViolation("invalid run id for sandbox")
        artifact_root = (repository_root / "artifacts" / "test-runs").resolve()
        root = (artifact_root / run_id).resolve()
        try:
            root.relative_to(artifact_root)
        except ValueError as exc:
            raise SandboxViolation("sandbox escaped artifacts/test-runs") from exc
        root.mkdir(parents=True, exist_ok=False)
        return cls(repository_root=repository_root.resolve(), run_id=run_id, root=root)

    def resolve_write(self, relative_path: str | Path) -> Path:
        candidate = Path(relative_path)
        if candidate.is_absolute():
            raise SandboxViolation("absolute output paths are forbidden")
        resolved = (self.root / candidate).resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise SandboxViolation(f"write escaped run sandbox: {candidate}") from exc
        return resolved

    def mkdir(self, relative_path: str | Path) -> Path:
        target = self.resolve_write(relative_path)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def write_text(self, relative_path: str | Path, content: str) -> Path:
        target = self.resolve_write(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
        return target

    def write_json(self, relative_path: str | Path, payload: Any) -> Path:
        serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        return self.write_text(relative_path, serialized)
