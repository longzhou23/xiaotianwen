"""Stable configuration and repository discovery for the local test harness.

This module deliberately has no dependency on AstrBot, Docker, or a plugin's
runtime requirements. It is shared by the command-line runner and local UI.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


FRAMEWORK_VERSION = "0.1.0"


class FrameworkConfigError(RuntimeError):
    """Raised when the harness cannot establish a safe, local configuration."""


@dataclass(frozen=True, slots=True)
class ProfileDefinition:
    """A deliberately small P0 profile declaration."""

    name: str
    description: str
    catalogs: tuple[str, ...]
    selected_case_ids: tuple[str, ...] = ()
    run_plugin_matrix: bool = False
    requires_docker: bool = False


PROFILES: dict[str, ProfileDefinition] = {
    "quick": ProfileDefinition(
        name="quick",
        description="Fast offline structural smoke suite.",
        catalogs=("p0_cases",),
        selected_case_ids=(
            "p0-group-text-single",
            "p0-group-text-debounce",
            "p0-image-summary-reuse",
            "p0-cancel-late-output",
            "p0-prompt-injection-block",
        ),
        run_plugin_matrix=True,
    ),
    "refactor": ProfileDefinition(
        name="refactor",
        description="All P0 functional replays plus eligible plugin unit suites.",
        catalogs=("p0_cases",),
        run_plugin_matrix=True,
    ),
    "full-offline": ProfileDefinition(
        name="full-offline",
        description="All P0 functional and injection fixture validation without real services.",
        catalogs=("p0_cases", "p0_injection_cases"),
        run_plugin_matrix=True,
    ),
    "integration": ProfileDefinition(
        name="integration",
        description="Reserved P1 isolated integration profile; never connects to production ports.",
        catalogs=(),
        requires_docker=True,
    ),
    "ui": ProfileDefinition(
        name="ui",
        description="Starts the loopback-only Local Test Console.",
        catalogs=(),
    ),
}


def framework_root() -> Path:
    """Return the checked-in repository root without trusting the current CWD."""

    return Path(__file__).resolve().parents[2]


def find_repository_root(start: Path | None = None) -> Path:
    """Find a xiaotianwen repository root from an arbitrary working directory."""

    candidates: list[Path] = []
    if start is not None:
        resolved = start.expanduser().resolve()
        candidates.extend((resolved, *resolved.parents))
    module_root = framework_root()
    candidates.extend((module_root, *module_root.parents))
    for candidate in candidates:
        if (candidate / "Todo.md").is_file() and (candidate / "plugins").is_dir():
            return candidate
    raise FrameworkConfigError("Could not locate repository root containing Todo.md and plugins/.")


def profile_definition(name: str) -> ProfileDefinition:
    try:
        return PROFILES[name]
    except KeyError as exc:
        choices = ", ".join(sorted(PROFILES))
        raise FrameworkConfigError(f"Unknown profile {name!r}; expected one of: {choices}") from exc


def create_run_id(prefix: str = "run") -> str:
    """Create an artifact identifier; nondeterminism is isolated to artifacts only."""

    timestamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    return f"{prefix}-{timestamp}-{os.getpid()}"


def git_environment(repository_root: Path) -> dict[str, Any]:
    """Collect a small, non-secret Git/environment description for a report."""

    def git(*args: str) -> str | None:
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=repository_root,
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if completed.returncode != 0:
            return None
        return completed.stdout.strip()

    dirty_output = git("status", "--short") or ""
    return {
        "schema_version": 1,
        "framework_version": FRAMEWORK_VERSION,
        "python": sys.version.split()[0],
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "timezone": "UTC",
        # Reports are portable artifacts. Do not persist a workstation path or
        # local account name merely to explain where Git was queried.
        "repository_root": "<repository-root>",
        "git_ref": git("rev-parse", "--short", "HEAD"),
        "git_branch": git("branch", "--show-current"),
        "dirty": bool(dirty_output),
        "dirty_paths": [line[3:] if len(line) > 3 else line for line in dirty_output.splitlines()],
    }


def profile_as_dict(profile: ProfileDefinition) -> dict[str, Any]:
    """Return a JSON-safe profile representation for reports and the local UI."""

    return asdict(profile)
