"""Static P0 hook/LLM-call audit with no plugin import or runtime execution."""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


HOOK_DECORATORS = {
    "event_message_type",
    "on_astrbot_loaded",
    "on_decorating_result",
    "on_llm_request",
    "on_llm_response",
    "on_waiting_llm_request",
    "after_message_sent",
    "platform_adapter_type",
}
LLM_CALLS = {"text_chat", "text_chat_stream", "llm_generate", "request_llm"}
HOOK_MANIFEST_SCHEMA_VERSION = 2


@dataclass(frozen=True, slots=True)
class HookAuditRecord:
    path: str
    function: str
    decorator: str
    priority: str

    def to_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "function": self.function,
            "decorator": self.decorator,
            "priority": self.priority,
        }


@dataclass(frozen=True, slots=True)
class LlmCallAuditRecord:
    path: str
    function: str
    call: str
    line: int

    def to_dict(self) -> dict[str, str | int]:
        return {
            "path": self.path,
            "function": self.function,
            "call": self.call,
            "line": self.line,
        }


@dataclass(frozen=True, slots=True)
class HookEffectAuditRecord:
    """Structural field/side-effect inventory for one declared hook.

    Values are field names and event-extra keys only.  The audit never records
    request values, message text, persona content, memory or tool arguments.
    """

    path: str
    function: str
    decorator: str
    priority: str
    request_reads: tuple[str, ...]
    request_writes: tuple[str, ...]
    event_extra_reads: tuple[str, ...]
    event_extra_writes: tuple[str, ...]
    stops_event: bool
    sends_directly: bool
    replaces_result: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "function": self.function,
            "decorator": self.decorator,
            "priority": self.priority,
            "request_reads": list(self.request_reads),
            "request_writes": list(self.request_writes),
            "event_extra_reads": list(self.event_extra_reads),
            "event_extra_writes": list(self.event_extra_writes),
            "stops_event": self.stops_event,
            "sends_directly": self.sends_directly,
            "replaces_result": self.replaces_result,
        }


def _decorator_name(value: ast.expr) -> str | None:
    if isinstance(value, ast.Call):
        return _decorator_name(value.func)
    if isinstance(value, ast.Attribute):
        return value.attr
    if isinstance(value, ast.Name):
        return value.id
    return None


def _priority(value: ast.expr) -> str:
    if not isinstance(value, ast.Call):
        return "default"
    for keyword in value.keywords:
        if keyword.arg != "priority":
            continue
        try:
            literal = ast.literal_eval(keyword.value)
        except (ValueError, TypeError):
            literal = None
        if isinstance(literal, int) and not isinstance(literal, bool):
            return str(literal)
        return "dynamic"
    return "default"


def _root_attribute(value: ast.Attribute, names: set[str]) -> str | None:
    """Return the first field below a selected root name."""

    parts: list[str] = []
    current: ast.expr = value
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name) and current.id in names and parts:
        return parts[-1]
    return None


def _constant_string_arg(call: ast.Call, index: int = 0) -> str | None:
    if len(call.args) <= index:
        return None
    value = call.args[index]
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    return None


def _hook_effect(
    relative: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    decorator: ast.expr,
) -> HookEffectAuditRecord:
    argument_names = {argument.arg for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)}
    request_names = argument_names & {"req", "request"}
    event_names = argument_names & {"event"}
    request_reads: set[str] = set()
    request_writes: set[str] = set()
    extra_reads: set[str] = set()
    extra_writes: set[str] = set()
    stops_event = False
    sends_directly = False
    replaces_result = False

    for child in ast.walk(node):
        if isinstance(child, ast.AugAssign) and isinstance(child.target, ast.Attribute):
            field = _root_attribute(child.target, request_names)
            if field:
                request_reads.add(field)
                request_writes.add(field)
        if isinstance(child, ast.Attribute):
            field = _root_attribute(child, request_names)
            if field:
                if isinstance(child.ctx, ast.Store):
                    request_writes.add(field)
                elif isinstance(child.ctx, ast.Load):
                    request_reads.add(field)
        if not isinstance(child, ast.Call):
            continue
        if isinstance(child.func, ast.Name):
            if child.func.id == "_request_stop_event" and child.args and isinstance(child.args[0], ast.Name) and child.args[0].id in event_names:
                stops_event = True
            if child.func.id == "_replace_plain_text":
                replaces_result = True
            continue
        if not isinstance(child.func, ast.Attribute):
            continue
        if child.func.attr == "_replace_plain_text":
            replaces_result = True
        if (
            child.func.attr == "_request_stop_event"
            and child.args
            and isinstance(child.args[0], ast.Name)
            and child.args[0].id in event_names
        ):
            stops_event = True
        owner = child.func.value
        if isinstance(owner, ast.Name) and owner.id in event_names:
            if child.func.attr == "get_extra":
                key = _constant_string_arg(child)
                if key:
                    extra_reads.add(key)
            elif child.func.attr == "set_extra":
                key = _constant_string_arg(child)
                if key:
                    extra_writes.add(key)
            elif child.func.attr == "stop_event":
                stops_event = True
            elif child.func.attr == "send":
                sends_directly = True
            elif child.func.attr == "set_result":
                replaces_result = True

    return HookEffectAuditRecord(
        path=relative,
        function=node.name,
        decorator=_decorator_name(decorator) or "unknown",
        priority=_priority(decorator),
        request_reads=tuple(sorted(request_reads)),
        request_writes=tuple(sorted(request_writes)),
        event_extra_reads=tuple(sorted(extra_reads)),
        event_extra_writes=tuple(sorted(extra_writes)),
        stops_event=stops_event,
        sends_directly=sends_directly,
        replaces_result=replaces_result,
    )


def scan_plugins(repository_root: Path) -> tuple[tuple[HookAuditRecord, ...], tuple[LlmCallAuditRecord, ...]]:
    """Scan source syntax only; no plugin module is imported or executed."""

    plugin_root = repository_root / "plugins"
    hooks: list[HookAuditRecord] = []
    calls: list[LlmCallAuditRecord] = []
    for path in sorted(plugin_root.rglob("*.py")):
        if "tests" in path.parts or "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            continue
        relative = path.relative_to(repository_root).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    name = _decorator_name(decorator)
                    if name in HOOK_DECORATORS:
                        hooks.append(HookAuditRecord(relative, node.name, name, _priority(decorator)))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in LLM_CALLS:
                parent_name = "<module>"
                for candidate in ast.walk(tree):
                    if isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef)) and candidate.lineno <= node.lineno <= getattr(candidate, "end_lineno", candidate.lineno):
                        parent_name = candidate.name
                        break
                calls.append(LlmCallAuditRecord(relative, parent_name, node.func.attr, node.lineno))
    return tuple(hooks), tuple(calls)


def scan_hook_effects(repository_root: Path) -> tuple[HookEffectAuditRecord, ...]:
    """Scan hook request fields and event side effects without importing code."""

    plugin_root = repository_root / "plugins"
    effects: list[HookEffectAuditRecord] = []
    for path in sorted(plugin_root.rglob("*.py")):
        if "tests" in path.parts or "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            continue
        relative = path.relative_to(repository_root).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if _decorator_name(decorator) in HOOK_DECORATORS:
                    effects.append(_hook_effect(relative, node, decorator))
    return tuple(effects)


def build_manifest(repository_root: Path) -> dict[str, object]:
    """Build a JSON-safe static inventory and stable drift fingerprint."""

    hooks, calls = scan_plugins(repository_root)
    effects = scan_hook_effects(repository_root)
    hook_values = [item.to_dict() for item in hooks]
    call_values = [item.to_dict() for item in calls]
    effect_values = [item.to_dict() for item in effects]
    canonical = json.dumps(
        {"hooks": hook_values, "hook_effects": effect_values, "llm_calls": call_values},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "schema_version": HOOK_MANIFEST_SCHEMA_VERSION,
        "hook_count": len(hook_values),
        "hook_effect_count": len(effect_values),
        "llm_call_count": len(call_values),
        "fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "hooks": hook_values,
        "hook_effects": effect_values,
        "llm_calls": call_values,
    }


def load_baseline(repository_root: Path) -> dict[str, object]:
    """Read the checked-in static baseline without accepting arbitrary paths."""

    path = repository_root / "tests" / "fixtures" / "hook-audit-baseline.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != HOOK_MANIFEST_SCHEMA_VERSION:
        raise ValueError("hook audit baseline has an unsupported schema")
    if not isinstance(payload.get("fingerprint"), str):
        raise ValueError("hook audit baseline must contain a fingerprint")
    return payload


def compare_manifest(current: dict[str, object], baseline: dict[str, object]) -> tuple[str, ...]:
    """Return actionable drift descriptions; an empty tuple means no drift."""

    errors: list[str] = []
    if current.get("schema_version") != baseline.get("schema_version"):
        errors.append("hook audit manifest schema changed")
    if current.get("fingerprint") != baseline.get("fingerprint"):
        errors.append(
            "hook audit fingerprint changed: "
            f"expected {baseline.get('fingerprint')}, got {current.get('fingerprint')}"
        )
    for key in ("hook_count", "hook_effect_count", "llm_call_count"):
        if key in baseline and current.get(key) != baseline.get(key):
            errors.append(f"{key} changed: expected {baseline.get(key)}, got {current.get(key)}")
    return tuple(errors)


def render_markdown(repository_root: Path) -> str:
    hooks, calls = scan_plugins(repository_root)
    lines = [
        "# P0 Hook 与直接 LLM 调用静态审计",
        "",
        "> 自动生成于本地源码 AST；不导入、不启动任何插件，也不包含请求正文。",
        "> 此表只说明声明位置和 priority，不能替代 AstrBot 运行时实际加载顺序验证。",
        "",
        "## Hook 清单",
        "",
        "| 文件 | 函数 | 生命周期 | priority |",
        "|---|---|---|---|",
    ]
    for item in hooks:
        lines.append(f"| `{item.path}` | `{item.function}` | `{item.decorator}` | `{item.priority}` |")
    lines.extend(["", "## 直连 LLM 调用", "", "| 文件 | 函数 | 调用 | 行号 |", "|---|---|---|---:|"])
    for item in calls:
        lines.append(f"| `{item.path}` | `{item.function}` | `{item.call}` | {item.line} |")
    lines.extend(
        [
            "",
            "## P0 结论边界",
            "",
            "- 本审计不读取或输出请求正文、persona、记忆、工具参数、凭据或外部 URL。",
            "- priority 相同的 Hook 仍可能受加载顺序影响；P1/P2 必须以显式契约和运行时隔离回放继续验证。",
            "- `text_chat`/`llm_generate`/`request_llm` 命中不自动表示主回复；后续观察必须按 route/owner/request_id 分类。",
        ]
    )
    return "\n".join(lines) + "\n"
