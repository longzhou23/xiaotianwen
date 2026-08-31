"""Declarative Hook ownership inventory used during P2 migration.

This is an auditable contract, not a claim about runtime activation.  The
``legacy`` entries describe current source behavior; the registry/assembler
entry describes the target isolated path.  Real Hook order still requires the
P1 isolated AstrBot gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..contracts.validation import ContractValidationError, require_non_empty_string, require_source_name


_ROLES = frozenset({
    "context_provider",
    "context_assembler",
    "input_security",
    "request_guard",
    "artifact_provider",
    "output_cleaner",
    "final_outbound_gate",
    "delivery_owner",
    "compatibility",
    "lifecycle",
})
_EFFECTS = frozenset({"read", "context", "request_mutation", "model_call", "artifact_provider", "tool", "send", "write", "security", "delivery"})


@dataclass(frozen=True, slots=True)
class HookContract:
    plugin: str
    hook: str
    priority: int | str
    role: str
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    effects: tuple[str, ...] = ()
    enabled: bool = True
    scope: str = "legacy"

    def __post_init__(self) -> None:
        object.__setattr__(self, "plugin", require_source_name(self.plugin, "plugin"))
        object.__setattr__(self, "hook", require_source_name(self.hook, "hook"))
        if type(self.priority) is not int and not (isinstance(self.priority, str) and self.priority.strip()):
            raise ContractValidationError("hook priority must be an integer or non-empty string")
        if self.role not in _ROLES:
            raise ContractValidationError(f"unsupported Hook role: {self.role}")
        object.__setattr__(self, "reads", tuple(require_non_empty_string(item, "read field") for item in self.reads))
        object.__setattr__(self, "writes", tuple(require_non_empty_string(item, "write field") for item in self.writes))
        normalized_effects = tuple(require_source_name(item, "effect").lower() for item in self.effects)
        if any(item not in _EFFECTS for item in normalized_effects):
            raise ContractValidationError("Hook effects contain an unsupported value")
        object.__setattr__(self, "effects", normalized_effects)
        if type(self.enabled) is not bool:
            raise ContractValidationError("Hook enabled must be boolean")
        object.__setattr__(self, "scope", require_source_name(self.scope, "scope"))

    def to_dict(self) -> dict[str, object]:
        return {
            "plugin": self.plugin,
            "hook": self.hook,
            "priority": self.priority,
            "role": self.role,
            "reads": list(self.reads),
            "writes": list(self.writes),
            "effects": list(self.effects),
            "enabled": self.enabled,
            "scope": self.scope,
        }


def default_hook_contracts() -> tuple[HookContract, ...]:
    """Return the checked-in inventory for the currently relevant plugins."""

    return (
        HookContract(
            "xiaotianwen_orchestrator", "context_assembly", 0, "context_assembler",
            reads=("TurnEnvelope", "registered_snapshots"),
            writes=("ContextAssemblyResult",), effects=("context",), scope="isolated",
        ),
        HookContract(
            "astrbot_plugin_context_aware", "on_llm_request", -10, "context_provider",
            reads=("ProviderRequest.contexts", "ProviderRequest.prompt", "event extras"),
            writes=("ProviderRequest.contexts", "ProviderRequest.extra_user_content_parts", "ProviderRequest.image_urls"),
            effects=("context", "model_call"),
        ),
        HookContract(
            "astrbot_plugin_image_context_pool", "on_llm_request", -20, "context_provider",
            reads=("ProviderRequest.image_urls", "session image cache"),
            writes=("ProviderRequest.contexts", "ProviderRequest.image_urls"),
            effects=("context", "model_call"),
        ),
        HookContract(
            "astrbot_plugin_iris_memory", "on_llm_request", "default", "context_provider",
            reads=("session", "Iris L1/L2/L3/profile"),
            writes=("ProviderRequest.contexts", "ProviderRequest.extra_user_content_parts"),
            effects=("context", "model_call", "write"),
        ),
        HookContract(
            "astrbot_plugin_shared_context", "on_llm_request", "default", "context_provider",
            reads=("shared context store", "ProviderRequest.contexts"),
            writes=("ProviderRequest.extra_user_content_parts",), effects=("context", "write"),
        ),
        HookContract(
            "astrbot_plugin_astrmetry", "on_llm_request", -1000, "artifact_provider",
            reads=("ProviderRequest.image_urls", "event extras"),
            writes=("artifact metadata", "event extras"), effects=("artifact_provider",),
        ),
        HookContract(
            "astrbot_plugin_tool_use_cleaner", "on_llm_request", "default", "output_cleaner",
            reads=("ProviderRequest.contexts",), writes=("ProviderRequest.contexts",), effects=("request_mutation",),
        ),
        HookContract(
            "antipromptinjector", "intercept_llm_request", -1000, "input_security",
            reads=("ProviderRequest.prompt", "ProviderRequest.system_prompt"),
            writes=("security decision",), effects=("security", "model_call"),
        ),
        HookContract(
            "antipromptinjector", "finalize_llm_request", 999, "request_guard",
            reads=("ProviderRequest prompt/context fingerprint",), writes=("security decision",), effects=("security",),
        ),
        HookContract(
            "astrbot_plugin_output_audit", "disable_streaming_for_audit", 90, "request_guard",
            reads=("ProviderRequest.stream" ,), writes=("ProviderRequest.stream",), effects=("security",),
        ),
        HookContract(
            "astrbot_plugin_output_audit", "on_decorating_result", -90, "final_outbound_gate",
            reads=("candidate output",), writes=("audit decision",), effects=("security", "delivery"),
        ),
        HookContract(
            "astrbot_plugin_smart_segmentation", "on_llm_response", "default", "output_cleaner",
            reads=("LLMResponse",), writes=("segmentation state",), effects=("delivery",),
        ),
        HookContract(
            "astrbot_plugin_group_chat_plus", "on_llm_request", -100000, "compatibility",
            reads=("legacy ProviderRequest fields",), writes=("legacy ProviderRequest fields",),
            effects=("context", "request_mutation", "model_call"), enabled=False, scope="migration",
        ),
        HookContract(
            "astrbot_plugin_group_chat_plus", "after_message_sent", "default", "delivery_owner",
            reads=("delivery result",), writes=("history state",), effects=("delivery", "write"),
        ),
        HookContract(
            "astrbot_plugin_affection", "on_llm_request", "default", "context_provider",
            reads=("bot/user relationship snapshot",), writes=("structured affection section",), effects=("context", "model_call"),
        ),
        HookContract(
            "astrbot_plugin_stealer", "on_llm_request", "default", "context_provider",
            reads=("emotion/artifact snapshot",), writes=("structured emotion section",), effects=("context", "model_call"),
        ),
    )


def validate_hook_contracts(contracts: Iterable[HookContract]) -> tuple[str, ...]:
    values = tuple(contracts)
    errors: list[str] = []
    if len([item for item in values if item.role == "context_assembler"]) != 1:
        errors.append("exactly one context_assembler owner is required")
    if not any(item.role == "input_security" for item in values):
        errors.append("an input_security owner is required")
    if not any(item.role == "final_outbound_gate" for item in values):
        errors.append("a final_outbound_gate owner is required")
    for item in values:
        if item.role == "compatibility" and item.enabled:
            errors.append(f"legacy compatibility Hook must be disabled: {item.plugin}.{item.hook}")
        if "monkey_patch" in item.effects:
            errors.append(f"runtime monkey-patch is forbidden: {item.plugin}.{item.hook}")
    return tuple(errors)
