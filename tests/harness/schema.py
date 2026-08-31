"""Strict, JSON-only schema checks for offline replay fixtures.

The P0 fixture format intentionally contains data, never executable callbacks.
It is small enough to validate without an additional schema package and rejects
the most common accidental references to an instance or a private workstation.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Iterable

from .redact import find_secret_hits


class FixtureValidationError(ValueError):
    """A fixture is malformed or violates the P0 isolation rules."""


@dataclass(frozen=True, slots=True)
class FixtureIssue:
    path: str
    message: str


_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{2,119}$")
_SYNTHETIC = re.compile(r"(?:synthetic|test|fixture|fake|p0)[a-z0-9_-]*", re.IGNORECASE)
_URL = re.compile(r"(?:https?|wss?)://[^\s)\]}]+", re.IGNORECASE)
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:\\")
_UNSAFE_PATH_PARTS = frozenset({"private", "secrets", "local-secrets", ".ssh", "recovery"})
_ROUTES = frozenset({"chat", "agent", "decision", "proactive", "vision", "background"})
_TIMELINE_OPERATIONS = frozenset({"event", "flush", "mark_stage"})


def _walk(value: Any, path: str = "$") -> Iterable[tuple[str, Any]]:
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]")


def _synthetic_identifier(value: object) -> bool:
    return isinstance(value, str) and bool(_SYNTHETIC.fullmatch(value))


def _looks_real_ip(value: str) -> bool:
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError:
        return False
    return not parsed.is_loopback and not parsed.is_unspecified


def _validate_string_safety(value: str, path: str, issues: list[FixtureIssue]) -> None:
    hits = find_secret_hits(value)
    if hits:
        categories = ", ".join(sorted({hit.category for hit in hits}))
        issues.append(FixtureIssue(path, f"contains a secret-like pattern: {categories}"))
    if _WINDOWS_ABSOLUTE.match(value) or value.startswith("/home/") or value.startswith("/Users/"):
        issues.append(FixtureIssue(path, "contains a workstation absolute path"))
    if _looks_real_ip(value):
        issues.append(FixtureIssue(path, "contains a non-loopback IP address"))
    urls = _URL.findall(value)
    for url in urls:
        if not url.startswith("http://127.0.0.1") and not url.startswith("http://localhost"):
            issues.append(FixtureIssue(path, "contains an external URL"))
            break


def validate_functional_case(case: dict[str, Any], *, inherited_compare: dict[str, Any] | None = None) -> tuple[FixtureIssue, ...]:
    """Validate one replay case and return all actionable errors."""

    issues: list[FixtureIssue] = []
    case_id = case.get("id")
    if not isinstance(case_id, str) or not _ID.fullmatch(case_id):
        issues.append(FixtureIssue("$.id", "must be a lowercase synthetic identifier"))
    route = case.get("route", "chat")
    if route not in _ROUTES:
        issues.append(FixtureIssue("$.route", f"must be one of {sorted(_ROUTES)}"))
    timeline = case.get("timeline")
    if not isinstance(timeline, list) or not timeline:
        issues.append(FixtureIssue("$.timeline", "must be a non-empty list"))
    else:
        previous_at = float("-inf")
        for index, item in enumerate(timeline):
            item_path = f"$.timeline[{index}]"
            if not isinstance(item, dict):
                issues.append(FixtureIssue(item_path, "must be an object"))
                continue
            if item.get("op") not in _TIMELINE_OPERATIONS:
                issues.append(FixtureIssue(f"{item_path}.op", "uses an unsupported operation"))
            at = item.get("at")
            if not isinstance(at, (int, float)) or isinstance(at, bool):
                issues.append(FixtureIssue(f"{item_path}.at", "must be numeric virtual seconds"))
            elif float(at) < previous_at:
                issues.append(FixtureIssue(f"{item_path}.at", "must not move virtual time backwards"))
            else:
                previous_at = float(at)
            if item.get("op") == "event":
                event = item.get("event")
                if not isinstance(event, dict):
                    issues.append(FixtureIssue(f"{item_path}.event", "must be an object"))
                    continue
                for key in ("message_id", "user_id"):
                    if key in event and not _synthetic_identifier(event[key]):
                        issues.append(FixtureIssue(f"{item_path}.event.{key}", "must be an obvious synthetic identifier"))
                if "group_id" in event and not _synthetic_identifier(event["group_id"]):
                    issues.append(FixtureIssue(f"{item_path}.event.group_id", "must be an obvious synthetic identifier"))
    if not isinstance(case.get("simulation", {}), dict):
        issues.append(FixtureIssue("$.simulation", "must be an object"))
    if not isinstance(case.get("expected"), dict):
        issues.append(FixtureIssue("$.expected", "must be an object"))
    compare = case.get("compare", inherited_compare)
    if not isinstance(compare, dict) or not isinstance(compare.get("text"), str):
        issues.append(FixtureIssue("$.compare", "must declare a structural text comparison strategy"))
    for value_path, value in _walk(case):
        if isinstance(value, str):
            _validate_string_safety(value, value_path, issues)
    return tuple(issues)


def validate_functional_catalog(payload: dict[str, Any]) -> tuple[FixtureIssue, ...]:
    issues: list[FixtureIssue] = []
    if payload.get("schema_version") != 1:
        issues.append(FixtureIssue("$.schema_version", "must equal 1"))
    cases = payload.get("cases")
    if not isinstance(cases, list):
        return tuple([*issues, FixtureIssue("$.cases", "must be a list")])
    seen_ids: set[str] = set()
    inherited_compare = payload.get("default_compare")
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            issues.append(FixtureIssue(f"$.cases[{index}]", "must be an object"))
            continue
        case_id = case.get("id")
        if isinstance(case_id, str):
            if case_id in seen_ids:
                issues.append(FixtureIssue(f"$.cases[{index}].id", "is duplicated"))
            seen_ids.add(case_id)
        issues.extend(validate_functional_case(case, inherited_compare=inherited_compare if isinstance(inherited_compare, dict) else None))
    return tuple(issues)


def validate_injection_catalog(payload: dict[str, Any]) -> tuple[FixtureIssue, ...]:
    issues: list[FixtureIssue] = []
    if payload.get("schema_version") != 1:
        issues.append(FixtureIssue("$.schema_version", "must equal 1"))
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        return tuple([*issues, FixtureIssue("$.cases", "must be a non-empty list")])
    for index, case in enumerate(cases):
        path = f"$.cases[{index}]"
        if not isinstance(case, dict):
            issues.append(FixtureIssue(path, "must be an object"))
            continue
        if not _synthetic_identifier(case.get("id")):
            issues.append(FixtureIssue(f"{path}.id", "must be an obvious synthetic identifier"))
        if not isinstance(case.get("attack_type"), str) or not isinstance(case.get("expected"), str):
            issues.append(FixtureIssue(path, "must declare attack_type and expected outcome"))
        input_text = case.get("input")
        if not isinstance(input_text, str):
            issues.append(FixtureIssue(f"{path}.input", "must be a synthetic string"))
        elif find_secret_hits(input_text):
            issues.append(FixtureIssue(f"{path}.input", "contains a secret-like pattern"))
    return tuple(issues)


def ensure_valid(issues: tuple[FixtureIssue, ...], *, label: str) -> None:
    if issues:
        rendered = "; ".join(f"{issue.path}: {issue.message}" for issue in issues[:8])
        suffix = "" if len(issues) <= 8 else f" (+{len(issues) - 8} more)"
        raise FixtureValidationError(f"Invalid {label}: {rendered}{suffix}")


def safe_asset_reference(value: str) -> bool:
    """Accept a virtual synthetic URI or a relative fixture asset path only."""

    if value.startswith("/synthetic/") or value.startswith("synthetic://"):
        return True
    path = PurePosixPath(value.replace("\\", "/"))
    return not path.is_absolute() and ".." not in path.parts and not (set(path.parts) & _UNSAFE_PATH_PARTS)
