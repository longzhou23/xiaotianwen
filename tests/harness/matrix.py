"""Safe plugin-test matrix orchestration for the repository-level harness."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .redact import redact_text, redact_value
from .path_policy import RepositoryPathPolicy


@dataclass(frozen=True, slots=True)
class PluginMatrixEntry:
    identifier: str
    path: str
    category: str
    profiles: tuple[str, ...]
    command: tuple[str, ...]
    timeout_seconds: int = 60
    offline: bool = True
    requires: tuple[str, ...] = ()
    enabled: bool = True
    not_run_reason: str | None = None


@dataclass(frozen=True, slots=True)
class PluginTestResult:
    identifier: str
    status: str
    duration_ms: int
    reason: str | None
    returncode: int | None
    command: tuple[str, ...]
    output: str

    def to_dict(self) -> dict[str, Any]:
        return redact_value(asdict(self))


def load_matrix(repository_root: Path) -> tuple[PluginMatrixEntry, ...]:
    """Load JSON syntax stored in a .yaml file (valid YAML 1.2, no PyYAML dependency)."""

    path = repository_root / "tests" / "plugin-matrix.yaml"
    RepositoryPathPolicy(repository_root).assert_safe_read(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("plugins") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise ValueError("plugin-matrix.yaml must contain a plugins list")
    normalized: list[PluginMatrixEntry] = []
    for item in entries:
        if not isinstance(item, dict):
            raise ValueError("plugin-matrix.yaml contains a non-object entry")
        command = item.get("command", ())
        if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
            raise ValueError(f"plugin matrix entry {item.get('id')!r} requires a command list")
        profiles = item.get("profiles", ())
        if not isinstance(profiles, list) or not all(isinstance(part, str) for part in profiles):
            raise ValueError(f"plugin matrix entry {item.get('id')!r} requires profile list")
        normalized.append(
            PluginMatrixEntry(
                identifier=str(item["id"]),
                path=str(item["path"]),
                category=str(item["category"]),
                profiles=tuple(profiles),
                command=tuple(command),
                timeout_seconds=int(item.get("timeout_seconds", 60)),
                offline=bool(item.get("offline", True)),
                requires=tuple(str(part) for part in item.get("requires", ())),
                enabled=bool(item.get("enabled", True)),
                not_run_reason=str(item["not_run_reason"]) if item.get("not_run_reason") else None,
            )
        )
    return tuple(normalized)


def _classify_failure(returncode: int, output: str) -> str:
    lowered = output.lower()
    if "modulenotfounderror" in lowered or "no module named" in lowered:
        return "MISSING_DEPENDENCY"
    if "collected 0 items" in lowered or "collection" in lowered and returncode in {2, 4}:
        return "COLLECTION_ERROR"
    return "FAILED"


def run_matrix(repository_root: Path, profile: str) -> tuple[PluginTestResult, ...]:
    """Run only explicitly eligible plugin tests, one subprocess per matrix entry."""

    result: list[PluginTestResult] = []
    harness_path = repository_root / "tests" / "harness"
    for entry in load_matrix(repository_root):
        if profile not in entry.profiles:
            continue
        entry_path = repository_root / entry.path
        if not entry.enabled:
            result.append(PluginTestResult(entry.identifier, "NOT_RUN", 0, entry.not_run_reason or "disabled", None, (), ""))
            continue
        if not entry.offline:
            result.append(PluginTestResult(entry.identifier, "NOT_RUN", 0, "not eligible for offline profile", None, (), ""))
            continue
        if not entry_path.exists():
            result.append(PluginTestResult(entry.identifier, "NOT_RUN", 0, "declared test path is absent", None, (), ""))
            continue
        missing = [name for name in entry.requires if importlib.util.find_spec(name) is None]
        if missing:
            result.append(PluginTestResult(entry.identifier, "MISSING_DEPENDENCY", 0, f"missing: {', '.join(missing)}", None, (), ""))
            continue
        command = tuple(sys.executable if part == "{python}" else part for part in entry.command)
        env = dict(os.environ)
        previous_pythonpath = env.get("PYTHONPATH", "")
        plugin_root = entry_path.parent if entry_path.name == "tests" else entry_path
        plugin_collection_root = plugin_root.parent
        env["PYTHONPATH"] = os.pathsep.join(
            [
                str(harness_path),
                str(repository_root),
                str(plugin_root),
                str(plugin_collection_root),
                previous_pythonpath,
            ]
        ).rstrip(os.pathsep)
        env["XTW_TEST_NETWORK_DENY"] = "1"
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                cwd=repository_root,
                env=env,
                capture_output=True,
                text=True,
                timeout=max(1, entry.timeout_seconds),
                check=False,
            )
            output = redact_text((completed.stdout + completed.stderr)[-12_000:])
            status = "PASSED" if completed.returncode == 0 else _classify_failure(completed.returncode, output)
            reason = None if status == "PASSED" else "subprocess returned non-zero"
            returncode: int | None = completed.returncode
        except subprocess.TimeoutExpired as exc:
            output = redact_text(((exc.stdout or "") + (exc.stderr or ""))[-12_000:])
            status, reason, returncode = "TIMEOUT", f"exceeded {entry.timeout_seconds}s", None
        except OSError as exc:
            output = redact_text(str(exc))
            status, reason, returncode = "ENVIRONMENT_ERROR", "failed to start subprocess", None
        duration_ms = int((time.monotonic() - started) * 1_000)
        result.append(PluginTestResult(entry.identifier, status, duration_ms, reason, returncode, command, output))
    return tuple(result)
