from __future__ import annotations

from astrbot_plugin_xiaotianwen_orchestrator.p2 import (
    CanaryPlan,
    PerformanceSample,
    compare_performance,
    default_long_run_templates,
    summarize_performance,
)


def test_performance_summary_requires_repeatable_sample_count_and_checks_p95_regression() -> None:
    baseline_samples = [PerformanceSample("chat", 100 + index, input_tokens=10, output_tokens=5) for index in range(20)]
    candidate_samples = [PerformanceSample("chat", 120 + index, input_tokens=10, output_tokens=5) for index in range(20)]
    baseline = summarize_performance(baseline_samples, route="chat")
    candidate = summarize_performance(candidate_samples, route="chat")
    assert baseline.status == "READY"
    assert candidate.status == "READY"
    assert compare_performance(baseline, candidate)["status"] == "BLOCKER"
    small = summarize_performance(baseline_samples[:2], route="chat")
    assert small.status == "INSUFFICIENT_DATA"
    assert compare_performance(small, small)["status"] == "INSUFFICIENT_DATA"


def test_canary_plan_is_100_turn_inventory_without_sending() -> None:
    plan = CanaryPlan.build()
    assert plan.count == 100
    assert plan.sends_enabled is False
    assert plan.slots[0].turn_id == "canary-001"
    assert plan.slots[-1].turn_id == "canary-100"
    assert all(slot.expected == "observe_only" for slot in plan.slots)


def test_long_run_templates_keep_24h_shadow_and_72h_snowluma_as_manual_targets() -> None:
    templates = default_long_run_templates()
    assert [(item.name, item.duration_hours) for item in templates] == [("orchestrator-shadow-24h", 24), ("snowluma-72h", 72)]
    assert all(item.to_dict()["status"] == "NOT_STARTED" for item in templates)
