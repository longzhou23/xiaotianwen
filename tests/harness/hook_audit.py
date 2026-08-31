"""Static P0 hook/LLM-call audit with no plugin import or runtime execution."""

from __future__ import annotations

import ast
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


@dataclass(frozen=True, slots=True)
class HookAuditRecord:
    path: str
    function: str
    decorator: str
    priority: str


@dataclass(frozen=True, slots=True)
class LlmCallAuditRecord:
    path: str
    function: str
    call: str
    line: int


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
        if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, int):
            return str(keyword.value.value)
        return "dynamic"
    return "default"


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
