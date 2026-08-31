"""Regression coverage for the local P1 fake integration entry point."""

from __future__ import annotations

from pathlib import Path

from tests.harness.p1_integration import run_p1_fake_suite


def test_p1_fake_suite_is_complete_and_does_not_require_services() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    report = run_p1_fake_suite(repository_root)

    assert [item.status for item in report.checks] == ["PASSED"] * 4
    assert report.observations
    assert {item["capture_mode"] for item in report.observations} == {"COMPLETE", "PARTIAL", "NOT_CONNECTED"}
    assert any(item.get("payload", {}).get("display") == "P1 synthetic message" for item in report.observations)
