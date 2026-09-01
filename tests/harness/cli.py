"""Unified, side-effect-contained command-line entry for the P0 test harness."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Iterable

from .compare import BaselineStore, canonical_observation, compare_observations
from .config import PROFILES, FrameworkConfigError, create_run_id, find_repository_root, profile_definition
from .hook_audit import build_manifest, compare_manifest, load_baseline, render_markdown
from .matrix import PluginTestResult, load_matrix, run_matrix
from .network_guard import NetworkGuard, NetworkViolation
from .p1_integration import run_p1_fake_suite
from .replay import ReplayEngine, build_interactive_case, load_case_catalog, load_injection_catalog
from .report import write_run_report
from .sandbox import RunSandbox, SandboxViolation


EXIT_PASS = 0
EXIT_FAILURE = 1
EXIT_CONFIGURATION = 2
EXIT_SECURITY = 3


def _unique_sandbox(repository_root: Path, requested_run_id: str | None, prefix: str) -> RunSandbox:
    seed = requested_run_id or create_run_id(prefix)
    candidate = seed
    suffix = 1
    while True:
        try:
            return RunSandbox.create(repository_root, candidate)
        except FileExistsError:
            suffix += 1
            candidate = f"{seed}-{suffix}"


def _injection_to_functional_case(raw: dict[str, Any]) -> dict[str, Any]:
    """Exercise a synthetic safety path without treating it as a real LLM proof."""

    case = build_interactive_case(
        case_id=str(raw["id"]),
        text=str(raw["input"]),
        route="chat",
        stream=False,
    )
    case["title"] = f"Synthetic injection fixture: {raw['attack_type']}"
    case["tags"] = ["security", "injection", str(raw["attack_type"])]
    case["simulation"] = {
        "request_roles": ["main_reply"],
        "audit": "block",
        "deliveries": 1,
        "security_safe": False,
    }
    case["expected"] = {
        "turns_ready": 1,
        "main_reply_requests": 1,
        "deliveries": 1,
        "audit": "block",
        "security_safe": True,
        "audit_before_delivery": True,
    }
    return case


def _select_cases(
    *,
    profile_name: str,
    case_id: str | None,
    tag: str | None,
) -> tuple[dict[str, Any], ...]:
    profile = profile_definition(profile_name)
    selected: list[dict[str, Any]] = []
    for catalog in profile.catalogs:
        if catalog == "p0_injection_cases":
            selected.extend(_injection_to_functional_case(case) for case in load_injection_catalog(catalog))
        else:
            selected.extend(load_case_catalog(catalog))
    if profile.selected_case_ids and not case_id and not tag:
        allowed = set(profile.selected_case_ids)
        selected = [case for case in selected if case["id"] in allowed]
    if case_id:
        selected = [case for case in selected if case["id"] == case_id]
        if not selected:
            raise FrameworkConfigError(f"No selected fixture case named {case_id!r}")
    if tag:
        selected = [case for case in selected if tag in case.get("tags", ())]
        if not selected:
            raise FrameworkConfigError(f"No selected fixture case contains tag {tag!r}")
    return tuple(selected)


def _comparisons(repository_root: Path, results: Iterable[Any], baseline_name: str | None) -> tuple[Any, ...]:
    if baseline_name is None:
        return ()
    if baseline_name != "approved":
        raise FrameworkConfigError("P0 only supports --baseline approved; Git-ref baselines are P1 work")
    store = BaselineStore(repository_root)
    return tuple(
        compare_observations(
            case_id=result.case_id,
            baseline=store.load(result.case_id),
            candidate=canonical_observation(result),
            baseline_name=baseline_name,
        )
        for result in results
    )


def _execute_run(args: argparse.Namespace, *, force_baseline: str | None = None) -> int:
    repository_root = find_repository_root(Path.cwd())
    profile = profile_definition(args.profile)
    if profile.name == "ui":
        return _execute_ui(args)
    sandbox = _unique_sandbox(repository_root, getattr(args, "run_id", None), profile.name)
    if profile.name == "integration":
        try:
            with NetworkGuard(allow_loopback=True):
                p1_report = run_p1_fake_suite(repository_root)
        except NetworkViolation as exc:
            report = write_run_report(
                sandbox=sandbox,
                repository_root=repository_root,
                profile=profile,
                replay_results=(),
                not_verified=("P1 fake integration was blocked by the network guard",),
                security_violations=(str(exc),),
            )
            print(f"{report.summary['release_gate']}: artifacts/test-runs/{sandbox.run_id}")
            return EXIT_SECURITY
        sandbox.write_json("observations/p1-integration.json", {"observations": list(p1_report.observations)})
        sandbox.write_json("logs/p1-integration.json", {"events": [item for item in p1_report.observations if item.get("kind") == "log.emitted"]})
        sandbox.write_json("p1-summary.json", p1_report.to_dict())
        report = write_run_report(
            sandbox=sandbox,
            repository_root=repository_root,
            profile=profile,
            replay_results=(),
            plugin_results=p1_report.checks,
            not_verified=(
                "real AstrBot disposable instance, plugin discovery/Hook order and Plugin Page remain NOT_VERIFIED",
                "real Provider/QQ/SnowLuma integration and long-run gates remain NOT_VERIFIED",
            ),
        )
        print(f"{report.summary['release_gate']}: artifacts/test-runs/{sandbox.run_id}")
        return EXIT_FAILURE if any(item.status == "FAILED" for item in p1_report.checks) else EXIT_PASS
    if profile.name == "audit":
        try:
            with NetworkGuard(allow_loopback=True):
                manifest = build_manifest(repository_root)
                drift = compare_manifest(manifest, load_baseline(repository_root))
        except NetworkViolation as exc:
            report = write_run_report(
                sandbox=sandbox,
                repository_root=repository_root,
                profile=profile,
                replay_results=(),
                not_verified=("static Hook audit was blocked by the network guard",),
                security_violations=(str(exc),),
            )
            print(f"{report.summary['release_gate']}: artifacts/test-runs/{sandbox.run_id}")
            return EXIT_SECURITY
        except (OSError, ValueError) as exc:
            report = write_run_report(
                sandbox=sandbox,
                repository_root=repository_root,
                profile=profile,
                replay_results=(),
                plugin_results=(
                    PluginTestResult(
                        identifier="p0-hook-audit",
                        status="FAILED",
                        duration_ms=0,
                        reason=str(exc),
                        returncode=1,
                        command=("static-ast-audit",),
                        output="configuration error",
                    ),
                ),
            )
            print(f"{report.summary['release_gate']}: artifacts/test-runs/{sandbox.run_id}")
            return EXIT_FAILURE
        audit_result = PluginTestResult(
            identifier="p0-hook-audit",
            status="PASSED" if not drift else "FAILED",
            duration_ms=0,
            reason="; ".join(drift) if drift else None,
            returncode=0 if not drift else 1,
            command=("static-ast-audit",),
            output=f"hooks={manifest['hook_count']} llm_calls={manifest['llm_call_count']}",
        )
        sandbox.write_json("hook-manifest.json", manifest)
        sandbox.write_text("hook-audit.md", render_markdown(repository_root))
        report = write_run_report(
            sandbox=sandbox,
            repository_root=repository_root,
            profile=profile,
            replay_results=(),
            plugin_results=(audit_result,),
        )
        print(f"{report.summary['release_gate']}: artifacts/test-runs/{sandbox.run_id}")
        return EXIT_FAILURE if drift else EXIT_PASS
    if profile.requires_docker:
        docker = shutil.which("docker")
        reason = "P1 integration harness is not implemented in P0."
        if docker is None:
            reason = "Docker is unavailable; integration was skipped and is NOT VERIFIED."
        report = write_run_report(
            sandbox=sandbox,
            repository_root=repository_root,
            profile=profile,
            replay_results=(),
            not_verified=[reason],
        )
        print(f"{report.summary['release_gate']}: artifacts/test-runs/{sandbox.run_id}")
        return EXIT_PASS
    cases = _select_cases(profile_name=profile.name, case_id=getattr(args, "case", None), tag=getattr(args, "tag", None))
    engine = ReplayEngine()
    security_violations: list[str] = []
    try:
        with NetworkGuard(allow_loopback=True):
            results = tuple(engine.run_case(case, run_id=f"{sandbox.run_id}-{case['id']}") for case in cases)
    except NetworkViolation as exc:
        security_violations.append(str(exc))
        results = ()
    comparisons = _comparisons(repository_root, results, force_baseline if force_baseline is not None else getattr(args, "baseline", None))
    plugin_results = run_matrix(repository_root, profile.name) if profile.run_plugin_matrix and not security_violations else ()
    report = write_run_report(
        sandbox=sandbox,
        repository_root=repository_root,
        profile=profile,
        replay_results=results,
        plugin_results=plugin_results,
        comparisons=comparisons,
        security_violations=security_violations,
    )
    print(f"{report.summary['release_gate']}: artifacts/test-runs/{sandbox.run_id}")
    if security_violations:
        return EXIT_SECURITY
    if report.summary["release_gate"] == "FAIL":
        return EXIT_FAILURE
    return EXIT_PASS


def _execute_compare(args: argparse.Namespace) -> int:
    return _execute_run(args, force_baseline=args.baseline)


def _execute_approve(args: argparse.Namespace) -> int:
    repository_root = find_repository_root(Path.cwd())
    cases = _select_cases(profile_name="refactor", case_id=args.case, tag=None)
    if len(cases) != 1:
        raise FrameworkConfigError("baseline approval requires exactly one case")
    result = ReplayEngine().run_case(cases[0], run_id=f"approval-preview-{args.case}")
    observation = canonical_observation(result)
    store = BaselineStore(repository_root)
    comparison = compare_observations(
        case_id=result.case_id,
        baseline=store.load(result.case_id),
        candidate=observation,
        baseline_name="approved",
    )
    print(json.dumps(comparison.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    if not args.yes:
        if not sys.stdin.isatty():
            raise FrameworkConfigError("noninteractive approval requires --yes after reviewing the diff")
        confirmation = input("Type APPROVE to write this single baseline: ").strip()
        if confirmation != "APPROVE":
            print("Baseline approval cancelled.")
            return EXIT_CONFIGURATION
    path = store.write_approved(
        case_id=result.case_id,
        observation=observation,
        reason=args.reason,
        source_ref=None,
    )
    print(f"Approved baseline written: {path}")
    return EXIT_PASS


def _execute_doctor(args: argparse.Namespace) -> int:
    repository_root = find_repository_root(Path.cwd())
    issues: list[str] = []
    checks: dict[str, Any] = {
        "repository_root": str(repository_root),
        "python": sys.version.split()[0],
        "pytest_available": importlib.util.find_spec("pytest") is not None,
        "docker_available": shutil.which("docker") is not None,
        "fixtures": {},
        "matrix_entries": 0,
    }
    if not checks["pytest_available"]:
        issues.append("pytest is missing; install the test-only dependency documented in tests/README.md")
    for catalog in ("p0_cases", "p0_injection_cases"):
        try:
            checks["fixtures"][catalog] = len(load_case_catalog(catalog)) if catalog == "p0_cases" else len(load_injection_catalog(catalog))
        except Exception as exc:  # configuration output, no test execution
            checks["fixtures"][catalog] = f"ERROR: {exc}"
            issues.append(f"fixture catalog {catalog}: {exc}")
    try:
        checks["matrix_entries"] = len(load_matrix(repository_root))
    except Exception as exc:
        issues.append(f"plugin matrix: {exc}")
    checks["issues"] = issues
    print(json.dumps(checks, ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_CONFIGURATION if issues else EXIT_PASS


def _execute_list(args: argparse.Namespace) -> int:
    repository_root = find_repository_root(Path.cwd())
    payload = {
        "profiles": {
            name: {"description": definition.description, "catalogs": definition.catalogs}
            for name, definition in sorted(PROFILES.items())
        },
        "functional_cases": [{"id": case["id"], "tags": case.get("tags", [])} for case in load_case_catalog()],
        "injection_cases": [{"id": case["id"], "attack_type": case["attack_type"]} for case in load_injection_catalog()],
        "matrix": [{"id": entry.identifier, "profiles": entry.profiles, "enabled": entry.enabled} for entry in load_matrix(repository_root)],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_PASS


def _execute_ui(args: argparse.Namespace) -> int:
    repository_root = find_repository_root(Path.cwd())
    from tests.ui.server.app import run_console

    return run_console(
        repository_root=repository_root,
        host=getattr(args, "host", "127.0.0.1"),
        port=getattr(args, "port", 0),
        open_browser=getattr(args, "open_browser", True),
        duration_seconds=getattr(args, "duration_seconds", None),
        astrbot_url=getattr(args, "astrbot_url", None),
        astrbot_data_dir=getattr(args, "astrbot_data_dir", None),
        live_astrbot=getattr(args, "live_astrbot", False),
        onebot_ws_url=getattr(args, "onebot_ws_url", None),
        onebot_token=getattr(args, "onebot_token", None),
        onebot_self_id=getattr(args, "onebot_self_id", "1000000001"),
        live_timeout_seconds=getattr(args, "live_timeout_seconds", 45.0),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="xiaotianwen-test", description="Offline Xiaotianwen regression test harness")
    subcommands = parser.add_subparsers(dest="command", required=True)

    run = subcommands.add_parser("run", help="run an offline profile, one case, or a tag selection")
    run.add_argument("--profile", default="quick", choices=("quick", "refactor", "full-offline", "integration", "audit", "ui"))
    run.add_argument("--case")
    run.add_argument("--tag")
    run.add_argument("--candidate", default="current", choices=("current",))
    run.add_argument("--baseline", choices=("approved",))
    run.add_argument("--run-id")
    run.set_defaults(handler=_execute_run)

    compare = subcommands.add_parser("compare", help="compare the current replay observations with an approved Golden")
    compare.add_argument("--profile", default="refactor", choices=("quick", "refactor", "full-offline"))
    compare.add_argument("--case")
    compare.add_argument("--tag")
    compare.add_argument("--candidate", default="current", choices=("current",))
    compare.add_argument("--baseline", default="approved", choices=("approved",))
    compare.add_argument("--run-id")
    compare.set_defaults(handler=_execute_compare)

    approve = subcommands.add_parser("approve-baseline", help="explicitly write one reviewed Golden baseline")
    approve.add_argument("--case", required=True)
    approve.add_argument("--reason", required=True)
    approve.add_argument("--yes", action="store_true", help="confirm a noninteractive approval after reviewing a diff")
    approve.set_defaults(handler=_execute_approve)

    doctor = subcommands.add_parser("doctor", help="validate local offline harness prerequisites")
    doctor.set_defaults(handler=_execute_doctor)

    listing = subcommands.add_parser("list", help="list profiles, fixtures and plugin matrix state")
    listing.set_defaults(handler=_execute_list)

    ui = subcommands.add_parser("ui", help="start the loopback-only Local Test Console")
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--port", default=0, type=int)
    ui.add_argument("--open", dest="open_browser", action="store_true", default=True)
    ui.add_argument("--no-open", dest="open_browser", action="store_false")
    ui.add_argument("--duration-seconds", type=float, default=None, help="test-only automatic shutdown; omitted means serve until Ctrl+C")
    ui.add_argument("--astrbot-url", help="loopback AstrBot Dashboard URL; defaults to the local xtw test instance")
    ui.add_argument("--astrbot-data-dir", help="AstrBot data directory for the read-only local observer")
    ui.add_argument("--live-astrbot", action="store_true", help="enable the explicit local OneBot bridge and route selected inputs into AstrBot")
    ui.add_argument("--onebot-ws-url", help="loopback AstrBot reverse WebSocket URL; otherwise read it from --astrbot-data-dir")
    ui.add_argument("--onebot-token", help="local test-only reverse WebSocket token; otherwise read it from --astrbot-data-dir")
    ui.add_argument("--onebot-self-id", default="1000000001", help="synthetic numeric OneBot self ID")
    ui.add_argument("--live-timeout-seconds", type=float, default=45.0, help="bounded wait for a local AstrBot action")
    ui.set_defaults(handler=_execute_ui)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (FrameworkConfigError, ValueError, FileNotFoundError) as exc:
        print(f"CONFIGURATION ERROR: {exc}", file=sys.stderr)
        return EXIT_CONFIGURATION
    except (NetworkViolation, SandboxViolation) as exc:
        print(f"SECURITY VIOLATION: {exc}", file=sys.stderr)
        return EXIT_SECURITY


if __name__ == "__main__":
    raise SystemExit(main())
