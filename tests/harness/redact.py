"""Shared, conservative redaction and secret scanning for local harness data."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


REDACTED_SECRET = "[REDACTED_SECRET]"
_SENSITIVE_KEY = re.compile(
    r"(?:authorization|proxy-authorization|cookie|set-cookie|api[_-]?key|token|secret|password|auth[_-]?code|private[_-]?key)",
    re.IGNORECASE,
)
_ASSIGNMENT = re.compile(
    r"(?P<key>authorization|api[_-]?key|token|secret|password|auth[_-]?code|cookie)"
    r"\s*[:=]\s*(?P<value>[^\s,;]+)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
    re.DOTALL,
)
_URL_USERINFO = re.compile(r"(?P<scheme>https?://)(?:[^\s/@:]+):(?:[^\s/@]+)@", re.IGNORECASE)
_SENSITIVE_QUERY = re.compile(
    r"(?P<key>api[_-]?key|token|secret|password|auth[_-]?code|signature)=(?P<value>[^&#\s]+)",
    re.IGNORECASE,
)
_KNOWN_SECRET_PREFIX = re.compile(r"\b(?:sk|rk|ghp|github_pat|xoxb|xoxp)_[A-Za-z0-9_-]{12,}\b", re.IGNORECASE)
_HIGH_ENTROPY = re.compile(r"\b(?=[A-Za-z0-9+/_=-]{32,}\b)(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9+/_=-]{32,}\b")
_SENSITIVE_PATH = re.compile(
    r"(?:(?:[A-Za-z]:\\|/)(?:[^\s/\\]+[\\/])*(?:\.ssh|secrets|local-secrets|private)(?:[\\/][^\s]*)?)",
    re.IGNORECASE,
)
_NON_SECRET_METRIC_KEYS = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cached_tokens",
        "token_count",
        "tokens",
        "secret_leak_detected",
        "secret_leak_count",
    }
)
_CORRELATION_KEYS = frozenset(
    {
        "run_id",
        "session_id",
        "event_id",
        "turn_id",
        "request_id",
        "parent_request_id",
        "call_id",
        "delivery_id",
        "result_call_id",
        "idempotency_key",
        "case_id",
        "message_id",
        "message_ids",
        "media_id",
        "media_ids",
        # Platform/account identifiers are retained only as a stable local
        # correlation alias when they are not synthetic fixture identifiers.
        "sender_id",
        "user_id",
        "bot_id",
        "qq",
        "uin",
        "reply_to",
    }
)
_SAFE_CORRELATION_IDENTIFIER = re.compile(
    r"^(?:(?:turn|event|request|call|delivery)-[0-9a-f]{8,64}(?:-[a-z][a-z0-9-]{0,32})?|"
    r"local-ui-run-[0-9]{1,6}|"
    r"(?:ui|run|p0|synthetic|message|media|img|m|group|private|proactive)[A-Za-z0-9:.-]{0,128})$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class SecretHit:
    """A safe scanner result: it identifies a pattern, never the matched value."""

    category: str
    start: int
    end: int


def is_sensitive_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    # Token *counts* are observability metrics, not bearer tokens. Keeping them
    # visible is necessary for cache/latency regression comparisons.
    if key.lower() in _NON_SECRET_METRIC_KEYS:
        return False
    return bool(_SENSITIVE_KEY.search(key))


def is_correlation_key(key: object) -> bool:
    return isinstance(key, str) and key.lower() in _CORRELATION_KEYS


def redact_text(value: str) -> str:
    """Redact credentials while retaining harmless synthetic fixture text."""

    text = _PRIVATE_KEY_BLOCK.sub(REDACTED_SECRET, value)
    text = _BEARER.sub(f"Bearer {REDACTED_SECRET}", text)
    text = _URL_USERINFO.sub(r"\g<scheme>" + REDACTED_SECRET + "@", text)
    text = _SENSITIVE_QUERY.sub(lambda match: f"{match.group('key')}={REDACTED_SECRET}", text)
    text = _ASSIGNMENT.sub(lambda match: f"{match.group('key')}={REDACTED_SECRET}", text)
    text = _KNOWN_SECRET_PREFIX.sub(REDACTED_SECRET, text)
    return _HIGH_ENTROPY.sub(REDACTED_SECRET, text)


def find_secret_hits(value: str) -> tuple[SecretHit, ...]:
    """Find dangerous patterns without returning the potentially secret text."""

    patterns = (
        ("private_key", _PRIVATE_KEY_BLOCK),
        ("bearer", _BEARER),
        ("url_userinfo", _URL_USERINFO),
        ("sensitive_assignment", _ASSIGNMENT),
        ("sensitive_query", _SENSITIVE_QUERY),
        ("known_secret_prefix", _KNOWN_SECRET_PREFIX),
        ("high_entropy", _HIGH_ENTROPY),
        ("sensitive_path", _SENSITIVE_PATH),
    )
    hits: list[SecretHit] = []
    for category, pattern in patterns:
        hits.extend(SecretHit(category, match.start(), match.end()) for match in pattern.finditer(value))
    return tuple(sorted(hits, key=lambda hit: (hit.start, hit.end, hit.category)))


def redact_correlation_identifier(value: str) -> str:
    """Preserve safe synthetic IDs, otherwise replace them with a stable alias.

    Request/call/delivery IDs must remain correlatable in the local console.
    They are still never trusted as a place to store arbitrary user text or a
    credential: recognized harness IDs stay readable, while anything outside
    that narrow grammar becomes a deterministic non-reversible reference.
    """

    non_entropy_hits = tuple(hit for hit in find_secret_hits(value) if hit.category != "high_entropy")
    if not non_entropy_hits and _SAFE_CORRELATION_IDENTIFIER.fullmatch(value):
        return value
    digest = hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:16]
    return f"correlation-{digest}"


def redact_value(value: Any, *, key: object | None = None) -> Any:
    """Return a JSON-safe redacted copy without mutating the caller's value."""

    if is_sensitive_key(key):
        return REDACTED_SECRET
    if is_correlation_key(key) and isinstance(value, str):
        return redact_correlation_identifier(value)
    if isinstance(value, str):
        return redact_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Mapping):
        return {str(item_key): redact_value(item_value, key=item_key) for item_key, item_value in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        item_key = key if is_correlation_key(key) else None
        return [redact_value(item, key=item_key) for item in value]
    return redact_text(str(value))
