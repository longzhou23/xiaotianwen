from __future__ import annotations

from tests.harness.hook_audit import render_markdown, scan_plugins
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
