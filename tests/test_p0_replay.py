from __future__ import annotations

import json
from pathlib import Path

from tests.harness.config import profile_definition
from tests.harness.redact import REDACTED_SECRET
from tests.harness.replay import ReplayEngine, build_interactive_case, load_case_catalog
from tests.harness.report import write_run_report
from tests.harness.sandbox import RunSandbox


def test_functional_catalog_covers_every_p0_replay_item() -> None:
    cases = load_case_catalog()
    case_ids = {case["id"] for case in cases}

    assert len(cases) >= 17
    assert {
        "p0-group-text-single",
        "p0-group-text-debounce",
        "p0-image-then-text",
        "p0-multi-astronomy-images",
        "p0-image-summary-reuse",
        "p0-meme-tool-chain",
        "p0-star-tool-chain",
        "p0-proactive-trigger",
        "p0-proactive-no-trigger",
        "p0-private-command-run",
        "p0-cancel-late-output",
        "p0-prompt-injection-block",
        "p0-output-audit-allow",
        "p0-output-audit-revise",
        "p0-output-audit-block",
        "p0-smart-segmentation",
    } <= case_ids


def test_functional_catalog_replays_with_structural_expectations() -> None:
    results = ReplayEngine().run_catalog()

    assert all(result.passed for result in results)
    cancellation = next(result for result in results if result.case_id == "p0-cancel-late-output")
    assert cancellation.summary["cancelled_turns"] == 1
    assert any(event["kind"] == "delivery.suppressed" for event in cancellation.trace)
    image_reuse = next(result for result in results if result.case_id == "p0-image-summary-reuse")
    assert image_reuse.summary["vlm_calls"] == 0
    assert image_reuse.summary["image_summary_reused"] == 1


def test_mismatched_baseline_fails_without_updating_fixture() -> None:
    case = dict(load_case_catalog()[0])
    case["expected"] = {**case["expected"], "deliveries": 2}

    result = ReplayEngine().run_case(case)

    assert result.passed is False
    assert result.validation_errors == ("deliveries: expected 2, got 1",)


def test_interactive_case_redacts_credentials_but_keeps_safe_input_visible() -> None:
    result = ReplayEngine().run_case(
        build_interactive_case(
            case_id="p0-ui-secret-redaction",
            text="普通内容 Authorization: token-that-must-not-be-visible",
        )
    )

    serialized = json.dumps(result.to_dict(), ensure_ascii=False)
    assert REDACTED_SECRET in serialized
    assert "token-that-must-not-be-visible" not in serialized
    assert "普通内容" in serialized


def test_replay_report_writes_only_redacted_artifacts(tmp_path: Path) -> None:
    result = ReplayEngine().run_case(
        build_interactive_case(
            case_id="p0-report-secret-redaction",
            text="Cookie=not-for-artifact",
        )
    )

    sandbox = RunSandbox.create(tmp_path, "report-safe-001")
    report = write_run_report(
        sandbox=sandbox,
        repository_root=tmp_path,
        profile=profile_definition("quick"),
        replay_results=(result,),
    )
    destination = report.root

    assert (destination / "summary.json").is_file()
    assert (destination / "summary.md").is_file()
    assert (destination / "junit.xml").is_file()
    assert (destination / "diff.json").is_file()
    assert "not-for-artifact" not in (destination / "summary.json").read_text(encoding="utf-8")
