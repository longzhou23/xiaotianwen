"""Redacted JSON, Markdown and JUnit reports for a local test run."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence
from xml.etree import ElementTree as ET

from .compare import ComparisonResult, DifferenceLevel, canonical_observation
from .config import FRAMEWORK_VERSION, git_environment, profile_as_dict
from .redact import redact_text, redact_value
from .sandbox import RunSandbox


@dataclass(frozen=True, slots=True)
class ReportResult:
    root: Path
    summary: dict[str, Any]


def _plugin_status(item: Any) -> str:
    return str(getattr(item, "status", item.get("status") if isinstance(item, dict) else "UNKNOWN"))


def _as_dict(item: Any) -> dict[str, Any]:
    if hasattr(item, "to_dict"):
        return dict(item.to_dict())
    return dict(item)


def _write_junit(sandbox: RunSandbox, replay_results: Sequence[Any], plugin_results: Sequence[Any]) -> None:
    suite = ET.Element("testsuite", name="xiaotianwen-local-harness")
    all_items: list[tuple[str, str, bool, str | None, bool]] = []
    for result in replay_results:
        all_items.append(("replay", result.case_id, result.passed, "; ".join(result.validation_errors), False))
    for item in plugin_results:
        status = _plugin_status(item)
        all_items.append(("plugin", getattr(item, "identifier", "unknown"), status == "PASSED", getattr(item, "reason", None), status in {"NOT_RUN", "MISSING_DEPENDENCY", "SKIPPED"}))
    suite.set("tests", str(len(all_items)))
    suite.set("failures", str(sum(1 for _, _, passed, _, skipped in all_items if not passed and not skipped)))
    suite.set("skipped", str(sum(1 for _, _, _, _, skipped in all_items if skipped)))
    for group, name, passed, reason, skipped in all_items:
        case = ET.SubElement(suite, "testcase", classname=group, name=name)
        if skipped:
            ET.SubElement(case, "skipped", message=reason or "not verified")
        elif not passed:
            failure = ET.SubElement(case, "failure", message=reason or "failed")
            failure.text = redact_text(reason or "failed")
    xml = ET.tostring(suite, encoding="unicode")
    sandbox.write_text("junit.xml", "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n" + xml + "\n")


def write_run_report(
    *,
    sandbox: RunSandbox,
    repository_root: Path,
    profile: Any,
    replay_results: Sequence[Any],
    plugin_results: Sequence[Any] = (),
    comparisons: Sequence[ComparisonResult] = (),
    not_verified: Sequence[str] = (),
    security_violations: Sequence[str] = (),
) -> ReportResult:
    """Write every P0 report file under a verified run-owned sandbox."""

    result_dicts = [_as_dict(item) for item in replay_results]
    plugin_dicts = [_as_dict(item) for item in plugin_results]
    comparison_dicts = [item.to_dict() for item in comparisons]
    diff_counts = Counter(
        difference["level"]
        for comparison in comparison_dicts
        for difference in comparison.get("differences", [])
    )
    replay_failed = [item.case_id for item in replay_results if not item.passed]
    plugin_failed = [item["identifier"] for item in plugin_dicts if item.get("status") in {"FAILED", "TIMEOUT", "COLLECTION_ERROR", "ENVIRONMENT_ERROR"}]
    not_verified_items = list(not_verified) + [
        f"plugin:{item['identifier']}:{item['status']}"
        for item in plugin_dicts
        if item.get("status") in {"NOT_RUN", "MISSING_DEPENDENCY", "SKIPPED"}
    ] + [
        f"baseline:{item['case_id']}:NOT_VERIFIED"
        for item in comparison_dicts
        if item.get("status") == "NOT_VERIFIED"
    ]
    has_blocker = bool(replay_failed or plugin_failed or security_violations or diff_counts[DifferenceLevel.BLOCKER.value])
    gate = "FAIL" if has_blocker else ("NOT_VERIFIED" if not_verified_items else "PASS")
    summary = redact_value(
        {
            "schema_version": 1,
            "framework_version": FRAMEWORK_VERSION,
            "run_id": sandbox.run_id,
            "profile": profile_as_dict(profile),
            "release_gate": gate,
            "replay": {"total": len(replay_results), "passed": len(replay_results) - len(replay_failed), "failed_cases": replay_failed},
            "plugins": {"total": len(plugin_dicts), "failed": plugin_failed, "statuses": dict(Counter(item.get("status", "UNKNOWN") for item in plugin_dicts))},
            "differences": {level.value: diff_counts[level.value] for level in DifferenceLevel},
            "security_violations": list(security_violations),
            "not_verified": not_verified_items,
            "baseline": "approved" if comparisons else "not requested",
            "candidate": "current",
            "next_step": (
                "Fix reported blocker(s) before enabling a shadow or production change."
                if has_blocker
                else "Review NOT VERIFIED layers before treating this as an integration or production gate."
                if not_verified_items
                else "P0 offline structural gate passed; remaining P1/P2 layers are intentionally not claimed."
            ),
        }
    )
    sandbox.write_json("summary.json", summary)
    sandbox.write_json("diff.json", {"schema_version": 1, "comparisons": comparison_dicts})
    sandbox.write_json("environment.json", git_environment(repository_root))
    for result in replay_results:
        sandbox.write_json(f"observations/{result.case_id}.json", canonical_observation(result))
        sandbox.write_json(f"logs/{result.case_id}.json", {"schema_version": 1, "logs": list(result.logs)})
    for plugin in plugin_dicts:
        identifier = str(plugin.get("identifier", "unknown")).replace("/", "-")
        sandbox.write_json(f"logs/plugin-{identifier}.json", plugin)
    _write_junit(sandbox, replay_results, plugin_results)
    lines = [
        "# Xiaotianwen local test run",
        "",
        f"- Run: `{sandbox.run_id}`",
        f"- Profile: `{getattr(profile, 'name', 'unknown')}`",
        f"- Release gate: **{gate}**",
        f"- Replay: {summary['replay']['passed']}/{summary['replay']['total']} passed",
        f"- Plugin failures: {', '.join(plugin_failed) if plugin_failed else 'none'}",
        f"- Differences: blocker={summary['differences']['BLOCKER']}, review={summary['differences']['REVIEW_REQUIRED']}, performance={summary['differences']['PERFORMANCE']}, info={summary['differences']['INFO']}",
        f"- Network/write guard violations: {', '.join(security_violations) if security_violations else 'none observed'}",
        "",
        "## Not verified",
        "",
    ]
    lines.extend([f"- {item}" for item in not_verified_items] or ["- none"])
    lines.extend(["", "## Next step", "", summary["next_step"], ""])
    sandbox.write_text("summary.md", "\n".join(lines))
    return ReportResult(root=sandbox.root, summary=summary)
