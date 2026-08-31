"""Static checks for the active SnowLuma/AstrBot deployment surface."""

from __future__ import annotations

from pathlib import Path


def test_active_deploy_surface_is_snowluma_only_and_restart_is_pull_free() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    active_files = (
        "deploy/start.sh",
        "deploy/stop.sh",
        "deploy/restart.sh",
        "deploy/status.sh",
        "deploy/update.sh",
        "deploy/verify.sh",
        "deploy/snowluma-live/compose.yml",
        "deploy/astrbot/compose.yml",
    )
    contents = {relative: (repository_root / relative).read_text(encoding="utf-8").lower() for relative in active_files}

    assert all("napcat" not in text for text in contents.values())
    assert "pull" not in contents["deploy/restart.sh"]
    assert "snowluma" in contents["deploy/start.sh"]
    assert "container_running astrbot" in contents["deploy/verify.sh"]
    assert "container_running snowluma" in contents["deploy/verify.sh"]
    assert "127.0.0.1:6200" in contents["deploy/verify.sh"]
    assert "127.0.0.1:5099" in contents["deploy/verify.sh"]
    assert "403" in contents["deploy/verify.sh"]
