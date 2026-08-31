"""Positive unit coverage for core harness primitives and redaction."""

from __future__ import annotations

import json

import pytest

from tests.harness.clock import VirtualClock
from tests.harness.compare import DifferenceLevel, compare_observations
from tests.harness.ids import DeterministicIdFactory
from tests.harness.path_policy import PathAccessViolation, RepositoryPathPolicy
from tests.harness.redact import REDACTED_SECRET, find_secret_hits, redact_text, redact_value
from tests.harness.report import write_run_report
from tests.harness.replay import ReplayEngine, build_interactive_case
from tests.harness.sandbox import RunSandbox
from tests.harness.config import profile_definition
from tests.harness.spies import DeliverySpy, ToolSpy, VLMSpy


def test_virtual_clock_is_monotonic() -> None:
    clock = VirtualClock(10)
    assert clock.advance(0.5) == 10.5
    assert clock.set(11) == 11


def test_deterministic_ids_repeat_for_fresh_factories() -> None:
    assert DeterministicIdFactory().next("request") == DeterministicIdFactory().next("request")


def test_redaction_and_scanner_do_not_return_a_secret() -> None:
    value = "Authorization: Bearer sk_abcdefghijklmnopqrstuvwxyz0123456789"
    assert REDACTED_SECRET in redact_text(value)
    assert find_secret_hits(value)


def test_redaction_keeps_safe_correlation_ids_and_token_metrics() -> None:
    payload = redact_value(
        {
            "request_id": "turn-0123456789abcdef0123456789abcdef",
            "call_id": "call-fedcba9876543210fedcba9876543210",
            "message_ids": ["synthetic-ui-message-01", "synthetic-ui-message-02"],
            "input_tokens": 123,
            "output_tokens": 45,
            "tokens": 168,
            "secret_leak_detected": 0,
            "sender_id": "2663176223",
            "token": "must-stay-secret",
        }
    )

    assert payload["request_id"].startswith("turn-")
    assert payload["call_id"].startswith("call-")
    assert payload["message_ids"] == ["synthetic-ui-message-01", "synthetic-ui-message-02"]
    assert payload["input_tokens"] == 123
    assert payload["output_tokens"] == 45
    assert payload["tokens"] == 168
    assert payload["secret_leak_detected"] == 0
    assert payload["sender_id"].startswith("correlation-")
    assert payload["token"] == REDACTED_SECRET


def test_redaction_aliases_untrusted_identifier_text_without_leaking_it() -> None:
    raw_identifier = "user-controlled-" + "a" * 48
    redacted = redact_value({"request_id": raw_identifier})

    assert redacted["request_id"].startswith("correlation-")
    assert raw_identifier not in redacted["request_id"]


def test_tool_and_delivery_spies_detect_duplicate_effects() -> None:
    tool = ToolSpy()
    first = tool.execute(name="synthetic-write", effect="write", idempotency_key="same")
    second = tool.execute(name="synthetic-write", effect="write", idempotency_key="same")
    delivery = DeliverySpy()
    sent = delivery.deliver(turn_id="synthetic-turn", payload_type="text", text="合成输出")
    duplicate = delivery.deliver(turn_id="synthetic-turn", payload_type="text", text="合成输出")
    assert first["status"] == "suppressed"
    assert second["status"] == "duplicate"
    assert sent["status"] == "completed"
    assert duplicate["status"] == "duplicate"


def test_vlm_spy_skips_call_on_existing_summary() -> None:
    spy = VLMSpy()
    cached = spy.analyze("synthetic-image", existing_summary=True)
    assert cached["status"] == "cache_hit"
    assert not any(record.kind == "vlm.request" for record in spy.records)


def test_comparator_marks_main_reply_count_as_blocker() -> None:
    comparison = compare_observations(
        case_id="p0-selftest-compare",
        baseline={"summary": {"main_reply_requests": 1}},
        candidate={"summary": {"main_reply_requests": 2}},
    )
    assert comparison.has_blocker
    assert comparison.differences[0].level is DifferenceLevel.BLOCKER


def test_path_policy_rejects_private_instance_data(tmp_path) -> None:
    protected = tmp_path / "private" / "instance.json"
    protected.parent.mkdir()
    protected.write_text("not for P0", encoding="utf-8")
    with pytest.raises(PathAccessViolation):
        RepositoryPathPolicy(tmp_path).assert_safe_read(protected)


def test_report_has_all_required_files_and_redacts_candidate_secret(tmp_path) -> None:
    sandbox = RunSandbox.create(tmp_path, "selftest-report")
    case = build_interactive_case(case_id="p0-selftest-report", text="合成报告输入")
    case["simulation"] = {"request_roles": ["main_reply"], "audit": "allow", "deliveries": 1, "output": "token=unsafe-report-secret"}
    case["expected"] = {"secret_leak_detected": 0}
    result = ReplayEngine().run_case(case)
    report = write_run_report(
        sandbox=sandbox,
        repository_root=tmp_path,
        profile=profile_definition("quick"),
        replay_results=(result,),
    )
    for relative in ("summary.md", "summary.json", "junit.xml", "diff.json", "environment.json", "observations/p0-selftest-report.json", "logs/p0-selftest-report.json"):
        assert (report.root / relative).is_file()
    combined = "\n".join(path.read_text(encoding="utf-8") for path in report.root.rglob("*") if path.is_file())
    assert "unsafe-report-secret" not in combined
    assert str(tmp_path) not in combined
    environment = json.loads((report.root / "environment.json").read_text(encoding="utf-8"))
    assert environment["repository_root"] == "<repository-root>"
