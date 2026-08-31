from __future__ import annotations

import json

from astrbot_plugin_xiaotianwen_orchestrator.context import ContextAssembler
from astrbot_plugin_xiaotianwen_orchestrator.contracts import ContextSection, TurnEnvelope
from astrbot_plugin_xiaotianwen_orchestrator.ingress import event_to_envelope
from astrbot_plugin_xiaotianwen_orchestrator.p2 import (
    ContextProviderRegistry,
    ProviderRegistration,
    default_hook_contracts,
    validate_hook_contracts,
)


def _turn() -> TurnEnvelope:
    return event_to_envelope(
        {"message_id": "m-101", "user_id": "u-101", "group_id": "g-101", "raw_message": "测试"},
        received_at=1.0,
    )


def _section(source: str, content: str, priority: int) -> ContextSection:
    return ContextSection(source, priority, content, 1_000, "request", "test-v1", sensitive=True)


def test_registry_has_one_assembler_and_deduplicates_identical_provider_content() -> None:
    registry = ContextProviderRegistry()
    registry.register(ProviderRegistration("scene", "context_aware", lambda turn, snapshots: _section("context_aware", "同一摘要", 40)))
    registry.register(ProviderRegistration("images", "image_context_pool", lambda turn, snapshots: _section("image_context_pool", "同一摘要", 90)))
    collection, assembled = registry.assemble(_turn())

    assert [section.source for section in collection.sections] == ["context_aware"]
    assert collection.dropped == (("image_context_pool", "duplicate_content"),)
    assert assembled.sections[0].source == "context_aware"
    assert registry.ASSEMBLER_OWNER == "xiaotianwen_context_assembler"


def test_provider_cannot_return_a_different_source_or_mutate_a_request() -> None:
    registry = ContextProviderRegistry(assembler=ContextAssembler())
    registry.register(ProviderRegistration("scene", "context_aware", lambda turn, snapshots: _section("wrong", "不应进入", 40)))
    collection = registry.collect(_turn())
    assert collection.sections == ()
    assert collection.failures[0].error_class == "ContractValidationError"


def test_hook_contract_is_serializable_and_legacy_group_compatibility_is_off() -> None:
    contracts = default_hook_contracts()
    assert validate_hook_contracts(contracts) == ()
    assert sum(item.role == "context_assembler" for item in contracts) == 1
    compatibility = [item for item in contracts if item.role == "compatibility"]
    assert compatibility and all(not item.enabled for item in compatibility)
    rendered = json.dumps([item.to_dict() for item in contracts], ensure_ascii=False)
    assert "ProviderRequest.prompt" in rendered
    assert "api_key" not in rendered.lower()
