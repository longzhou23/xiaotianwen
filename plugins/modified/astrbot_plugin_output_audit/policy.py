"""Strict, deterministic policy types for the Output Audit plugin."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


DECISIONS = frozenset({"allow", "revise", "block"})
RISK_LEVELS = frozenset({"none", "low", "medium", "high", "critical"})
CATEGORIES = frozenset(
    {
        "PROMPT_LEAK",
        "PRIVACY",
        "HARASSMENT",
        "SEXUAL",
        "SELF_HARM",
        "VIOLENCE",
        "ILLEGAL",
        "DANGEROUS_INSTRUCTION",
        "IMPERSONATION",
        "MANIPULATION",
        "TOOL_DISCLOSURE",
        "MISLEADING_HIGH_STAKES",
        "CONTEXT_INSTRUCTION",
        "POLITICAL_SENSITIVE",
        "OTHER",
    }
)


class VerdictValidationError(ValueError):
    """The reviewer did not honour the fixed JSON contract."""


@dataclass(frozen=True)
class ReviewVerdict:
    decision: str
    risk_level: str
    categories: tuple[str, ...] = field(default_factory=tuple)
    reason_code: str = "OK"
    rewrite_instruction: str = ""
    confidence: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "risk_level": self.risk_level,
            "categories": list(self.categories),
            "reason_code": self.reason_code,
            "rewrite_instruction": self.rewrite_instruction,
            "confidence": self.confidence,
        }


def _parse_json_object(raw: str) -> dict[str, Any]:
    """Parse a JSON object, accepting only a harmless Markdown fence wrapper."""
    text = (raw or "").strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline < 0 or not text.endswith("```"):
            raise VerdictValidationError("REVIEW_JSON_INVALID")
        text = text[first_newline + 1 : -3].strip()
    try:
        data = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise VerdictValidationError("REVIEW_JSON_INVALID") from exc
    if not isinstance(data, dict):
        raise VerdictValidationError("REVIEW_JSON_NOT_OBJECT")
    return data


def parse_verdict(raw: str) -> ReviewVerdict:
    """Validate every field and the decision/risk consistency matrix."""
    data = _parse_json_object(raw)
    required = {
        "decision",
        "risk_level",
        "categories",
        "reason_code",
        "rewrite_instruction",
        "confidence",
    }
    if set(data) != required:
        raise VerdictValidationError("REVIEW_SCHEMA_FIELDS")

    decision = data["decision"]
    level = data["risk_level"]
    categories = data["categories"]
    reason = data["reason_code"]
    rewrite = data["rewrite_instruction"]
    confidence = data["confidence"]

    if not isinstance(decision, str) or decision not in DECISIONS:
        raise VerdictValidationError("REVIEW_DECISION_INVALID")
    if not isinstance(level, str) or level not in RISK_LEVELS:
        raise VerdictValidationError("REVIEW_RISK_INVALID")
    if not isinstance(categories, list) or len(categories) > 4:
        raise VerdictValidationError("REVIEW_CATEGORIES_INVALID")
    if any(not isinstance(item, str) or item not in CATEGORIES for item in categories):
        raise VerdictValidationError("REVIEW_CATEGORY_INVALID")
    if len(set(categories)) != len(categories):
        raise VerdictValidationError("REVIEW_CATEGORY_DUPLICATE")
    if not isinstance(reason, str) or not reason or len(reason) > 64:
        raise VerdictValidationError("REVIEW_REASON_INVALID")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", reason):
        raise VerdictValidationError("REVIEW_REASON_FORMAT")
    if not isinstance(rewrite, str) or len(rewrite) > 200:
        raise VerdictValidationError("REVIEW_REWRITE_INVALID")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise VerdictValidationError("REVIEW_CONFIDENCE_INVALID")
    confidence = float(confidence)
    if not 0.0 <= confidence <= 1.0:
        raise VerdictValidationError("REVIEW_CONFIDENCE_RANGE")

    allowed = {
        "allow": {"none", "low"},
        "revise": {"medium"},
        "block": {"high", "critical"},
    }
    if level not in allowed[decision]:
        raise VerdictValidationError("REVIEW_DECISION_RISK_MISMATCH")
    if decision == "revise" and not rewrite.strip():
        raise VerdictValidationError("REVIEW_REWRITE_REQUIRED")
    if decision != "revise" and rewrite:
        raise VerdictValidationError("REVIEW_REWRITE_FORBIDDEN")

    return ReviewVerdict(
        decision=decision,
        risk_level=level,
        categories=tuple(categories),
        reason_code=reason,
        rewrite_instruction=rewrite.strip(),
        confidence=confidence,
    )


def action_for_verdict(verdict: ReviewVerdict, mode: str) -> str:
    """Translate a valid reviewer verdict into the configured rollout action."""
    if mode in {"shadow", "warn"}:
        return "allow"
    return verdict.decision
