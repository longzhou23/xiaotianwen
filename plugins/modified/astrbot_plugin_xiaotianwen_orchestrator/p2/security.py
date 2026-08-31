"""Minimum fixed security boundary for input, tools and final output."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..contracts.validation import ContractValidationError, require_identifier, require_non_empty_string, sha256_text


_INPUT_PATTERNS = (
    ("instruction_override", re.compile(r"(?:ignore|disregard)\s+(?:all\s+)?previous|忽略(?:之前|上面|以上)的指令", re.IGNORECASE)),
    ("internal_context_request", re.compile(r"(?:system\s+prompt|internal\s+instructions|输出全部上下文|输出系统提示|完整记忆)", re.IGNORECASE)),
    ("credential_request", re.compile(r"(?:api[_-]?key|access[_-]?token|private\s+key|密码|令牌)", re.IGNORECASE)),
)
_OUTPUT_PATTERNS = (
    ("credential_leak", re.compile(r"(?:api[_-]?key|access[_-]?token|private\s+key|密码|令牌)\s*[:=]", re.IGNORECASE)),
    ("internal_context_leak", re.compile(r"(?:system\s+prompt|internal\s+instructions|内部提示|完整上下文|他人记忆)", re.IGNORECASE)),
)
_UNTRUSTED_SOURCES = frozenset({"ocr", "vlm", "image", "merged_forward", "quoted_message", "tool_result"})
_WRITE_EFFECTS = frozenset({"send", "write", "status", "steal"})


@dataclass(frozen=True, slots=True)
class InputAssessment:
    allowed: bool
    labels: tuple[str, ...]
    source: str
    chars: int
    text_hash: str

    def to_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "labels": list(self.labels),
            "source": self.source,
            "chars": self.chars,
            "text_hash": self.text_hash,
        }


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    allowed: bool
    effect: str
    reason: str
    actor_hash: str
    target_session_id: str

    def to_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "effect": self.effect,
            "reason": self.reason,
            "actor_hash": self.actor_hash,
            "target_session_id": self.target_session_id,
        }


@dataclass(frozen=True, slots=True)
class GatedOutput:
    action: str
    text: str
    labels: tuple[str, ...]
    candidate_chars: int
    candidate_hash: str
    reviewer_payload: dict[str, object]

    @property
    def allowed_for_delivery(self) -> bool:
        return self.action in {"allow", "revise"}


class SecurityBoundary:
    """All paths use the same fixed boundary, including proactive/tool paths."""

    refusal_text = "抱歉，我不能提供这类内容。"

    def assess_input(self, text: str, *, source: str = "user") -> InputAssessment:
        if not isinstance(text, str):
            raise ContractValidationError("security input must be a string")
        normalized_source = require_non_empty_string(source, "source").lower()
        labels = [label for label, pattern in _INPUT_PATTERNS if pattern.search(text)]
        if normalized_source in _UNTRUSTED_SOURCES:
            labels.append("untrusted_material")
        labels = list(dict.fromkeys(labels))
        blocked = any(label in {"instruction_override", "internal_context_request", "credential_request"} for label in labels)
        return InputAssessment(not blocked, tuple(labels), normalized_source, len(text), sha256_text(text))

    def authorize_tool(
        self,
        *,
        actor_id: str,
        session_id: str,
        effect: str,
        target_session_id: str | None = None,
        is_admin: bool = False,
    ) -> PermissionDecision:
        actor = require_identifier(actor_id, "actor_id")
        session = require_identifier(session_id, "session_id")
        target = require_identifier(target_session_id or session, "target_session_id")
        normalized_effect = require_non_empty_string(effect, "effect").lower()
        if target != session:
            return PermissionDecision(False, normalized_effect, "cross-session operation requires explicit scope", sha256_text(actor)[:16], target)
        if normalized_effect in _WRITE_EFFECTS and not is_admin:
            return PermissionDecision(False, normalized_effect, "write-like operation requires permission", sha256_text(actor)[:16], target)
        return PermissionDecision(True, normalized_effect, "permission accepted", sha256_text(actor)[:16], target)

    def gate_output(self, candidate: str, *, input_assessment: InputAssessment | None = None, route: str = "chat") -> GatedOutput:
        if not isinstance(candidate, str):
            raise ContractValidationError("candidate output must be a string")
        labels = [label for label, pattern in _OUTPUT_PATTERNS if pattern.search(candidate)]
        if input_assessment is not None and not input_assessment.allowed:
            labels.extend(input_assessment.labels)
        labels = list(dict.fromkeys(labels))
        action = "block" if any(label in {"credential_leak", "internal_context_leak", "instruction_override", "internal_context_request", "credential_request"} for label in labels) else "allow"
        rendered = self.refusal_text if action == "block" else candidate
        reviewer_payload = {
            "route": require_non_empty_string(route, "route"),
            "action": action,
            "labels": labels,
            "candidate_chars": len(candidate),
            "candidate_hash": sha256_text(candidate),
        }
        return GatedOutput(action, rendered, tuple(labels), len(candidate), sha256_text(candidate), reviewer_payload)
