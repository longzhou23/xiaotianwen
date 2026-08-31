"""Strict parser for lightweight decision/proactive yes-no routes."""

from __future__ import annotations

from dataclasses import dataclass

from ..contracts.validation import ContractValidationError


@dataclass(frozen=True, slots=True)
class BinaryDecision:
    allowed: bool
    reason: str


def parse_binary_decision(value: str, *, max_reason_chars: int = 160) -> BinaryDecision:
    if not isinstance(value, str):
        raise ContractValidationError("decision output must be a string")
    lines = [line.strip() for line in value.strip().splitlines() if line.strip()]
    if not lines:
        raise ContractValidationError("decision output is empty")
    first, _, inline_reason = lines[0].partition(":")
    normalized = first.strip().lower()
    if normalized not in {"yes", "no"}:
        raise ContractValidationError("decision output must begin with yes or no")
    reason = inline_reason.strip() or (lines[1] if len(lines) > 1 else "")
    if len(reason) > max_reason_chars:
        reason = reason[:max_reason_chars]
    return BinaryDecision(normalized == "yes", reason)
