"""Local, model-free replies used whenever an unsafe result must be replaced."""

from __future__ import annotations


def choose_fallback(categories: tuple[str, ...] | list[str]) -> str:
    category_set = set(categories)
    if category_set & {"PROMPT_LEAK", "PRIVACY", "TOOL_DISCLOSURE"}:
        return "这里涉及不该公开的信息，我就不往外讲啦。"
    if "POLITICAL_SENSITIVE" in category_set:
        return "这类现实话题我就不展开啦，换个轻松一点的聊。"
    if category_set & {"SELF_HARM", "VIOLENCE", "ILLEGAL", "DANGEROUS_INSTRUCTION"}:
        return "这个我不能按刚才那个方向展开，不过可以换个安全一点的方式聊。"
    if category_set & {"HARASSMENT", "SEXUAL", "MANIPULATION"}:
        return "这句说出来可能不太合适，我换个不会伤到人的说法吧。"
    return "这句说出来可能不太合适，我换个安全一点的说法吧。"
