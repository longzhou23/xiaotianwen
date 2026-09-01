from __future__ import annotations

from tests.harness.hook_audit import (
    build_manifest,
    compare_manifest,
    load_baseline,
    render_markdown,
    scan_hook_effects,
    scan_plugins,
)
from tests.harness.replay import REPOSITORY_ROOT


def test_static_hook_audit_finds_known_hooks_without_importing_plugins() -> None:
    hooks, calls = scan_plugins(REPOSITORY_ROOT)

    assert any(item.decorator == "on_llm_request" for item in hooks)
    assert any("astrbot_plugin_group_chat_plus/main.py" in item.path for item in hooks)
    assert any(item.call in {"text_chat", "text_chat_stream"} for item in calls)


def test_hook_audit_markdown_is_structural_and_has_no_request_body() -> None:
    content = render_markdown(REPOSITORY_ROOT)

    assert "# P0 Hook 与直接 LLM 调用静态审计" in content
    assert "priority" in content
    assert "不导入、不启动任何插件" in content
    assert "raw_message" not in content


def test_hook_audit_manifest_detects_upgrade_drift_against_checked_in_baseline() -> None:
    current = build_manifest(REPOSITORY_ROOT)
    baseline = load_baseline(REPOSITORY_ROOT)

    assert compare_manifest(current, baseline) == ()
    changed = dict(current)
    changed["fingerprint"] = "0" * 64
    assert compare_manifest(changed, baseline)


def test_critical_hook_field_and_security_boundaries_are_explicit() -> None:
    effects = scan_hook_effects(REPOSITORY_ROOT)

    def find(path_suffix: str, function: str, decorator: str):
        matches = [
            item
            for item in effects
            if item.path.endswith(path_suffix)
            and item.function == function
            and item.decorator == decorator
        ]
        assert len(matches) == 1
        return matches[0]

    debounce = find("astrbot_plugin_debounce/main.py", "on_llm_request", "on_llm_request")
    recall = find("astrbot_plugin_recall_cancel/main.py", "on_llm_request", "on_llm_request")
    assert debounce.priority == recall.priority == "100"
    assert debounce.stops_event
    assert recall.stops_event
    assert not debounce.request_writes
    assert not recall.request_writes

    convergence = find("astrbot_plugin_group_chat_plus/main.py", "on_llm_request", "on_llm_request")
    assert convergence.priority == "-100000"
    assert {
        "audio_urls",
        "contexts",
        "extra_user_content_parts",
        "func_tool",
        "image_urls",
        "prompt",
        "system_prompt",
    }.issubset(convergence.request_writes)
    assert not convergence.sends_directly

    input_gate = find("antipromptinjector/main.py", "intercept_llm_request", "on_llm_request")
    signature_gate = find("antipromptinjector/main.py", "finalize_llm_request", "on_llm_request")
    assert input_gate.priority == "-1000"
    assert signature_gate.priority == "999"
    assert input_gate.stops_event and input_gate.sends_directly
    assert signature_gate.stops_event and not signature_gate.sends_directly

    streaming_gate = find("astrbot_plugin_output_audit/main.py", "disable_streaming_for_audit", "on_llm_request")
    output_gate = find("astrbot_plugin_output_audit/main.py", "audit_final_result", "on_decorating_result")
    assert streaming_gate.priority == "90"
    assert streaming_gate.event_extra_writes == ("enable_streaming",)
    assert output_gate.priority == "-90"
    assert output_gate.replaces_result
    assert not output_gate.sends_directly
