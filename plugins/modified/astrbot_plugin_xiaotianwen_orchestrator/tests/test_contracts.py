from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from astrbot_plugin_xiaotianwen_orchestrator.contracts import (
    ContextSection,
    ContractValidationError,
    MediaRef,
    ToolExecutionPolicy,
    TurnEnvelope,
)


def _media() -> MediaRef:
    return MediaRef(
        media_id="img-001",
        kind="image",
        message_id="m-001",
        sender_id="10001",
        order=0,
        description="一张测试星空图",
        local_path="/tmp/test-image.jpg",
        content_hash="abc123",
    )


def test_turn_envelope_is_json_serializable_without_platform_object() -> None:
    turn = TurnEnvelope(
        request_id="turn-001",
        session_id="group:42",
        route="chat",
        trigger="at_bot",
        sender_id="10001",
        text="看看这张",
        reply_to="m-000",
        media=(_media(),),
        received_at=100.0,
        batch_started_at=99.0,
        metadata={"event_source": "onebot", "flags": ["image", "reply"]},
    )

    serialized = turn.to_dict()

    assert json.loads(json.dumps(serialized, ensure_ascii=False))["media"][0]["media_id"] == "img-001"
    assert "看看这张" not in json.dumps(turn.structural_summary(), ensure_ascii=False)


def test_turn_rejects_unknown_route_and_raw_platform_metadata() -> None:
    class PlatformEvent:
        pass

    with pytest.raises(ContractValidationError, match="route"):
        TurnEnvelope(
            request_id="turn-001",
            session_id="group:42",
            route="unknown-route",
            trigger="message",
            sender_id="10001",
            text="hello",
            reply_to=None,
            media=(),
            received_at=1,
            batch_started_at=1,
        )
    with pytest.raises(ContractValidationError, match="platform"):
        TurnEnvelope(
            request_id="turn-001",
            session_id="group:42",
            route="chat",
            trigger="message",
            sender_id="10001",
            text="hello",
            reply_to=None,
            media=(),
            received_at=1,
            batch_started_at=1,
            metadata={"raw_event": PlatformEvent()},
        )


def test_media_preserves_order_and_hides_path_url_in_structural_summary() -> None:
    media = _media()
    summary = media.structural_summary()

    assert media.to_dict()["order"] == 0
    assert summary["has_local_path"] is True
    assert "/tmp/test-image.jpg" not in json.dumps(summary, ensure_ascii=False)
    with pytest.raises(ContractValidationError):
        MediaRef(
            media_id="img-002",
            kind="image",
            message_id="m-001",
            sender_id="10001",
            order=-1,
        )


def test_context_section_requires_nonempty_bounded_content() -> None:
    section = ContextSection(
        source="context_aware",
        priority=40,
        content="当前群聊场景",
        max_chars=4,
        cache_scope="request",
        version="v1",
        sensitive=True,
    )

    assert section.bounded_content() == "当前群聊"
    assert "当前群聊场景" not in json.dumps(section.structural_summary(), ensure_ascii=False)
    with pytest.raises(ContractValidationError, match="content"):
        ContextSection(
            source="context_aware",
            priority=40,
            content=" ",
            max_chars=4,
            cache_scope="request",
            version="v1",
        )


def test_unknown_tool_defaults_to_conservative_write_policy() -> None:
    policy = ToolExecutionPolicy.conservative_default()

    assert policy.effect == "write"
    assert policy.parallel_safe is False
    assert policy.retry_safe is False
    assert policy.is_cacheable is False
    with pytest.raises(ContractValidationError, match="write/send"):
        ToolExecutionPolicy("send", True, False, 0, 100)


def test_contract_modules_do_not_import_astrbot_runtime() -> None:
    contracts_dir = Path(__file__).resolve().parents[1] / "contracts"
    imported_modules: list[str] = []
    for path in contracts_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module)

    assert not [module for module in imported_modules if module.startswith("astrbot")]
    assert not [
        module
        for module in imported_modules
        if module.startswith("astrbot_plugin_group_chat_plus")
    ]
