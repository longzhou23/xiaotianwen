from __future__ import annotations

import json

from astrbot_plugin_xiaotianwen_orchestrator.context import (
    ContextAssembler,
    ContextAssemblyPolicy,
    ContextAwareAdapter,
    ImageContextPoolAdapter,
    IrisMemoryAdapter,
    SharedContextAdapter,
)
from astrbot_plugin_xiaotianwen_orchestrator.contracts import ContextSection


def _section(source: str, priority: int, content: str, *, max_chars: int = 500) -> ContextSection:
    return ContextSection(
        source=source,
        priority=priority,
        content=content,
        max_chars=max_chars,
        cache_scope="request",
        version="test-v1",
        sensitive=source not in {"persona", "tool_rules"},
    )


def test_read_only_adapters_preserve_source_order_and_image_order() -> None:
    scene = ContextAwareAdapter().sections({"scene": "当前群聊正在讨论星图", "version": "ca-v1"})
    iris = IrisMemoryAdapter().sections(
        {
            "l2": "L2 命中",
            "l3": "L3 关系",
            "profile": "画像",
            "affection": "好感度",
            "version": "iris-v1",
        }
    )
    images = ImageContextPoolAdapter().sections(
        {
            "entries": [
                {"image_id": "img-b", "order": 1, "description": "第二张"},
                {"image_id": "img-a", "order": 0, "description": "第一张"},
            ]
        }
    )

    assembled = ContextAssembler().assemble(
        (
            _section("persona", 10, "PERSONA"),
            _section("tool_rules", 20, "TOOLS"),
            *scene,
            *iris,
            *images,
            _section("current_message", 100, "用户当前消息"),
        )
    )

    assert [section.source for section in assembled.sections] == [
        "persona",
        "tool_rules",
        "context_aware",
        "iris_l2",
        "iris_l3",
        "iris_profile",
        "iris_affection",
        "image_context_pool",
        "current_message",
    ]
    assert assembled.payload.startswith("PERSONA\n\nTOOLS\n\n")
    assert assembled.payload.index("img-a") < assembled.payload.index("img-b")


def test_shared_context_is_explicitly_disabled_by_default() -> None:
    snapshot = {"content": "跨会话资料"}

    assert SharedContextAdapter().sections(snapshot) == ()
    assert len(SharedContextAdapter(enabled=True).sections(snapshot)) == 1


def test_assembler_deduplicates_sources_and_applies_total_budget_after_prefix() -> None:
    policy = ContextAssemblyPolicy(total_budget_chars=18)
    result = ContextAssembler(policy).assemble(
        (
            _section("persona", 10, "PERSONA"),
            _section("tool_rules", 20, "TOOLS"),
            _section("context_aware", 40, "SCENE-EXTRA"),
            _section("context_aware", 41, "SHOULD-NOT-APPEAR"),
        )
    )

    assert [section.source for section in result.sections] == ["persona", "tool_rules", "context_aware"]
    assert "SHOULD-NOT-APPEAR" not in result.payload
    assert ("context_aware", "duplicate_source") in result.dropped
    assert result.payload.startswith("PERSONA\n\nTOOLS\n\n")
    assert result.overflow_chars == 0


def test_decision_route_excludes_main_reply_iris_and_tool_sections() -> None:
    result = ContextAssembler().assemble(
        (
            _section("persona", 10, "PERSONA"),
            _section("tool_rules", 20, "ALL TOOLS"),
            _section("context_aware", 40, "SCENE"),
            _section("iris_l2", 50, "L2"),
            _section("iris_profile", 70, "PROFILE"),
            _section("iris_affection", 80, "AFFECTION"),
            _section("current_message", 100, "MESSAGE"),
        ),
        route="decision",
    )

    assert [section.source for section in result.sections] == [
        "persona",
        "context_aware",
        "current_message",
    ]
    assert {source for source, reason in result.dropped if reason == "route_excluded"} == {
        "tool_rules",
        "iris_l2",
        "iris_profile",
        "iris_affection",
    }


def test_shadow_diff_is_content_redacted_and_adapters_do_not_call_models() -> None:
    calls = {"llm": 0, "embedding": 0, "vlm": 0}
    # The adapters only accept this finished mapping. The callable values are
    # intentionally not touched, demonstrating that a shadow assembly cannot
    # ask the source plugin to perform a second model operation.
    snapshot = {
        "content": "当前场景秘密内容",
        "run_llm": lambda: calls.__setitem__("llm", calls["llm"] + 1),
        "run_embedding": lambda: calls.__setitem__("embedding", calls["embedding"] + 1),
        "run_vlm": lambda: calls.__setitem__("vlm", calls["vlm"] + 1),
    }
    section = ContextAwareAdapter().sections(snapshot)
    result = ContextAssembler().assemble(section)
    diff = ContextAssembler.compare_legacy_payload("旧路径秘密内容", result)

    assert calls == {"llm": 0, "embedding": 0, "vlm": 0}
    rendered = json.dumps(diff.to_dict(), ensure_ascii=False)
    assert "旧路径秘密内容" not in rendered
    assert "当前场景秘密内容" not in rendered
