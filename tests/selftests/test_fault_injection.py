"""Deliberate defects that the P0 offline harness must detect as regressions."""

from __future__ import annotations

import hashlib
import socket
from pathlib import Path

import pytest

from tests.harness.compare import BaselineStore, canonical_observation
from tests.harness.network_guard import NetworkGuard, NetworkViolation
from tests.harness.replay import ReplayEngine, build_interactive_case
from tests.harness.sandbox import RunSandbox, SandboxViolation


def _case(case_id: str, *, simulation: dict[str, object], expected: dict[str, object]) -> dict[str, object]:
    case = build_interactive_case(case_id=case_id, text="合成故障注入文本")
    case["simulation"] = simulation
    case["expected"] = expected
    return case


def _assert_detected(case: dict[str, object], expected_key: str) -> None:
    result = ReplayEngine().run_case(case)
    assert not result.passed, "the intentional broken candidate was incorrectly accepted"
    assert any(error.startswith(f"{expected_key}:") for error in result.validation_errors), result.validation_errors


def test_detects_two_main_provider_requests() -> None:
    _assert_detected(
        _case(
            "p0-fault-double-main",
            simulation={"request_roles": ["main_reply", "main_reply"], "audit": "allow", "deliveries": 1},
            expected={"main_reply_requests": 1},
        ),
        "main_reply_requests",
    )


def test_detects_two_final_deliveries() -> None:
    _assert_detected(
        _case(
            "p0-fault-double-delivery",
            simulation={"request_roles": ["main_reply"], "audit": "allow", "deliveries": 2},
            expected={"deliveries": 1},
        ),
        "deliveries",
    )


def test_detects_duplicate_write_tool_attempt() -> None:
    _assert_detected(
        _case(
            "p0-fault-duplicate-write",
            simulation={
                "request_roles": ["main_reply", "tool_continuation"],
                "tools": [
                    {"name": "synthetic_write", "effect": "write", "idempotency_key": "same-effect"},
                    {"name": "synthetic_write", "effect": "write", "idempotency_key": "same-effect"},
                ],
                "audit": "allow",
                "deliveries": 1,
            },
            expected={"duplicate_write_tool_attempts": 0},
        ),
        "duplicate_write_tool_attempts",
    )


def test_detects_vlm_call_after_existing_summary() -> None:
    _assert_detected(
        _case(
            "p0-fault-vlm-cache",
            simulation={"request_roles": ["main_reply"], "vlm_calls": 1, "image_summary_reused": 1, "audit": "allow", "deliveries": 1},
            expected={"vlm_calls": 0},
        ),
        "vlm_calls",
    )


def test_detects_late_delivery_after_cancel() -> None:
    _assert_detected(
        _case(
            "p0-fault-late-delivery",
            simulation={"request_roles": ["main_reply"], "late_deliveries": 1, "audit": "allow", "deliveries": 1},
            expected={"late_delivery_count": 0},
        ),
        "late_delivery_count",
    )


def test_detects_context_order_regression() -> None:
    _assert_detected(
        _case(
            "p0-fault-context-order",
            simulation={
                "request_roles": ["main_reply"],
                "audit": "allow",
                "deliveries": 1,
                "fault_context_preserve_input_order": True,
                "context_sections": [
                    {"name": "current", "order": 20, "chars": 10},
                    {"name": "stable", "order": 10, "chars": 10},
                ],
            },
            expected={"context_order": ["stable", "current"]},
        ),
        "context_order",
    )


def test_detects_wrong_tool_result_call_id() -> None:
    _assert_detected(
        _case(
            "p0-fault-call-id",
            simulation={
                "request_roles": ["main_reply", "tool_continuation"],
                "tools": [{"name": "synthetic_read", "effect": "read", "result_call_id": "synthetic-wrong-call"}],
                "audit": "allow",
                "deliveries": 1,
            },
            expected={"invalid_tool_call_ids": 0},
        ),
        "invalid_tool_call_ids",
    )


def test_detects_output_audit_bypass() -> None:
    _assert_detected(
        _case(
            "p0-fault-audit-bypass",
            simulation={"request_roles": ["main_reply"], "audit": "allow", "audit_bypassed": True, "deliveries": 1},
            expected={"audit_before_delivery": True},
        ),
        "audit_before_delivery",
    )


def test_detects_secret_in_candidate_output_but_persists_redacted_value() -> None:
    case = _case(
        "p0-fault-secret-output",
        simulation={"request_roles": ["main_reply"], "audit": "allow", "deliveries": 1, "output": "api_key=unsafe-secret-value"},
        expected={"secret_leak_detected": 0, "security_safe": True},
    )
    result = ReplayEngine().run_case(case)
    assert not result.passed
    assert "unsafe-secret-value" not in str(result.to_dict())
    assert any(error.startswith("secret_leak_detected:") for error in result.validation_errors)


def test_blocks_external_network_before_dns_or_connection() -> None:
    with NetworkGuard(allow_loopback=True) as guard:
        with pytest.raises(NetworkViolation):
            socket.create_connection(("example.invalid", 443), timeout=0.01)
    assert guard.attempts and guard.attempts[0].operation == "socket.create_connection"


def test_blocks_sandbox_escape(tmp_path: Path) -> None:
    sandbox = RunSandbox.create(tmp_path, "selftest-sandbox")
    with pytest.raises(SandboxViolation):
        sandbox.write_text("../outside.txt", "must not escape")
    assert not (tmp_path / "outside.txt").exists()


def test_normal_replay_never_mutates_approved_baseline(tmp_path: Path) -> None:
    store = BaselineStore(tmp_path)
    seed_case = build_interactive_case(case_id="p0-fault-baseline-stability", text="合成基线")
    seed_result = ReplayEngine().run_case(seed_case)
    baseline_path = store.write_approved(
        case_id=seed_result.case_id,
        observation=canonical_observation(seed_result),
        reason="selftest seed only",
        source_ref="synthetic",
    )
    before = hashlib.sha256(baseline_path.read_bytes()).hexdigest()
    ReplayEngine().run_case(seed_case)
    after = hashlib.sha256(baseline_path.read_bytes()).hexdigest()
    assert after == before
