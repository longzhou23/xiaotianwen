"""OneBot/AstrBot event normalization without importing either runtime."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..contracts import MediaRef, TurnEnvelope
from ..contracts.validation import (
    ContractValidationError,
    require_finite_timestamp,
    structural_fingerprint,
)


def _value(value: object, *names: str, default: Any = None) -> Any:
    """Read a plain field from a mapping or a benign event attribute.

    Values are normalized immediately and never retained as platform objects.
    Only explicitly named zero-argument AstrBot convenience methods are called.
    """

    if isinstance(value, Mapping):
        for name in names:
            if name in value and value[name] is not None:
                return value[name]
        return default
    for name in names:
        try:
            candidate = getattr(value, name)
        except Exception:
            continue
        if candidate is None:
            continue
        if callable(candidate) and name in {"get_message_str", "get_sender_id", "get_sender_name"}:
            try:
                candidate = candidate()
            except Exception:
                continue
        if not callable(candidate):
            return candidate
    return default


def _nested_value(value: object, container_name: str, *names: str) -> Any:
    container = _value(value, container_name)
    return _value(container, *names) if container is not None else None


def _text_from_segments(segments: object) -> str:
    if not isinstance(segments, Sequence) or isinstance(segments, (str, bytes, bytearray)):
        return ""
    pieces: list[str] = []
    for segment in segments:
        if not isinstance(segment, Mapping):
            continue
        segment_type = str(segment.get("type", "")).lower()
        if segment_type not in {"text", "plain"}:
            continue
        data = segment.get("data", {})
        raw = data.get("text") if isinstance(data, Mapping) else None
        if raw is not None:
            pieces.append(str(raw))
    return "".join(pieces)


def _safe_identifier_fragment(value: object) -> str:
    return hashlib.sha256(str(value).encode("utf-8", "replace")).hexdigest()[:20]


def _turn_request_id(
    *,
    session_id: str,
    message_id: str,
    sender_id: str,
    route: str,
    text: str,
    media: Sequence[MediaRef],
) -> str:
    """Create a stable, non-secret turn ID from normalized ingress facts.

    The ID is not an authorization credential.  Determinism makes shadow
    replay comparable across runs and lets a duplicate OneBot event converge on
    the same request-local identity before any downstream work is considered.
    A platform message ID is preferred; a stable text/media fallback is used
    only when that field is absent.
    """

    if message_id and message_id != "event-unknown":
        material = ("message", session_id, message_id, sender_id, route)
    else:
        material = (
            "fallback",
            session_id,
            sender_id,
            route,
            hashlib.sha256(text.encode("utf-8", "replace")).hexdigest(),
            tuple((item.media_id, item.kind, item.order, item.content_hash) for item in media),
        )
    return f"turn-{hashlib.sha256(repr(material).encode('utf-8', 'replace')).hexdigest()[:32]}"


def _message_id(event: object) -> str:
    raw = _value(event, "message_id", "id")
    if raw is None:
        raw = _nested_value(event, "message_obj", "message_id", "id")
    return str(raw or "event-unknown")


def _sender_id(event: object) -> str:
    raw = _value(event, "sender_id", "user_id")
    if raw is None:
        raw = _nested_value(event, "sender", "user_id", "id")
    return str(raw or "unknown-sender")


def _session_id(event: object, sender_id: str) -> str:
    raw = _value(event, "unified_msg_origin", "session_id", "session")
    if raw:
        return str(raw)
    group_id = _value(event, "group_id")
    if group_id is not None:
        return f"group:{group_id}"
    user_id = _value(event, "user_id", "sender_id") or sender_id
    return f"private:{user_id}"


def _reply_to(event: object) -> str | None:
    raw = _value(event, "reply_to", "reply_to_message_id")
    if raw is None:
        raw = _nested_value(event, "reply", "message_id", "id")
    if raw is None or not str(raw).strip():
        return None
    return str(raw)


def _segment_media(
    *,
    session_id: str,
    message_id: str,
    sender_id: str,
    segments: object,
) -> list[MediaRef]:
    if not isinstance(segments, Sequence) or isinstance(segments, (str, bytes, bytearray)):
        return []
    media: list[MediaRef] = []
    for source_index, segment in enumerate(segments):
        if not isinstance(segment, Mapping):
            continue
        segment_type = str(segment.get("type", "")).lower()
        if segment_type not in {"image", "mface", "face", "sticker", "file", "video", "record"}:
            continue
        data = segment.get("data", {})
        data = data if isinstance(data, Mapping) else {}
        kind = {
            "mface": "meme",
            "face": "meme",
            "sticker": "sticker",
            "record": "audio",
        }.get(segment_type, segment_type)
        raw_ref = data.get("url") or data.get("file") or data.get("path") or ""
        raw_ref_text = str(raw_ref)
        raw_id = data.get("image_id") or data.get("media_id")
        media_id = str(raw_id or f"media-{_safe_identifier_fragment((session_id, message_id, source_index, raw_ref_text))}")
        source_url = raw_ref_text if raw_ref_text.startswith(("http://", "https://")) else None
        # Never retain a data URI/base64 payload in the media contract. It is
        # not a stable recovery path and makes a shadow turn unexpectedly huge.
        is_inline_payload = raw_ref_text.startswith(("data:", "base64://"))
        local_path = raw_ref_text if raw_ref_text and source_url is None and not is_inline_payload else None
        content_hash = data.get("content_hash") or data.get("md5")
        if not content_hash and is_inline_payload:
            content_hash = hashlib.sha256(raw_ref_text.encode("utf-8")).hexdigest()
        media.append(
            MediaRef(
                media_id=media_id,
                kind=kind,
                message_id=message_id,
                sender_id=sender_id,
                order=len(media),
                local_path=local_path,
                source_url=source_url,
                content_hash=str(content_hash) if content_hash else None,
            )
        )
    return media


def _declared_media(
    *,
    session_id: str,
    message_id: str,
    sender_id: str,
    value: object,
) -> list[MediaRef]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    result: list[MediaRef] = []
    for order, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise ContractValidationError("event media entries must be mappings")
        payload = dict(raw)
        payload.setdefault("message_id", message_id)
        payload.setdefault("sender_id", sender_id)
        payload.setdefault("order", order)
        payload.setdefault(
            "media_id",
            f"media-{_safe_identifier_fragment((session_id, message_id, order, payload.get('source_url')))}",
        )
        result.append(MediaRef.from_dict(payload))
    return result


def event_to_envelope(
    event: object,
    *,
    received_at: float | None = None,
    route: str = "chat",
    trigger: str | None = None,
) -> TurnEnvelope:
    """Build a safe TurnEnvelope from an OneBot dict or AstrBot-like event.

    It intentionally reads a small documented field set. Any unrecognized
    field, platform event object, listener or callback is discarded at this
    ingress boundary.
    """

    now = time.time() if received_at is None else require_finite_timestamp(received_at, "received_at")
    sender_id = _sender_id(event)
    message_id = _message_id(event)
    session_id = _session_id(event, sender_id)
    raw_text = _value(event, "text", "raw_message", "message_str", "get_message_str")
    text = str(raw_text) if raw_text is not None else ""
    segments = _value(event, "message", "messages", "components")
    if not text:
        text = _text_from_segments(segments)
    declared_media = _value(event, "media")
    media = _declared_media(
        session_id=session_id,
        message_id=message_id,
        sender_id=sender_id,
        value=declared_media,
    )
    if not media:
        media = _segment_media(
            session_id=session_id,
            message_id=message_id,
            sender_id=sender_id,
            segments=segments,
        )
    raw_route = _value(event, "route") or route
    raw_trigger = _value(event, "trigger") or trigger or "message"
    platform = str(_value(event, "platform", "platform_name") or "onebot")
    metadata = {
        "event_source": platform,
        "message_id": message_id,
        "is_group": _value(event, "group_id") is not None,
        "media_count": len(media),
    }
    return TurnEnvelope(
        request_id=_turn_request_id(
            session_id=session_id,
            message_id=message_id,
            sender_id=sender_id,
            route=str(raw_route),
            text=text,
            media=media,
        ),
        session_id=session_id,
        route=str(raw_route),
        trigger=str(raw_trigger),
        sender_id=sender_id,
        text=text,
        reply_to=_reply_to(event),
        media=tuple(media),
        received_at=now,
        batch_started_at=now,
        metadata=metadata,
    )


def event_fingerprint(event: object, *, received_at: float | None = None) -> str:
    """Fingerprint duplicate ingress events without retaining their text body."""

    envelope = event_to_envelope(event, received_at=received_at)
    message_id = str(envelope.metadata.get("message_id", ""))
    # A platform message id is the most precise duplicate key. The short TTL
    # store bounds any risk from a platform that later reuses ids.
    if message_id and message_id != "event-unknown":
        material = f"id\0{envelope.session_id}\0{message_id}"
    else:
        # Keep this fallback independent from TurnEnvelope.request_id. Only
        # stable ingress facts participate when a platform omitted message_id.
        material = "fallback\0" + structural_fingerprint(
            {
                "session_id": envelope.session_id,
                "sender_id": envelope.sender_id,
                "route": envelope.route,
                "trigger": envelope.trigger,
                "reply_to": envelope.reply_to,
                "text_hash": envelope.text_hash,
                "media": [
                    {
                        "media_id": item.media_id,
                        "kind": item.kind,
                        "order": item.order,
                        "content_hash": item.content_hash,
                    }
                    for item in envelope.media
                ],
            }
        )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class EventDeduplicator:
    """In-memory short-TTL fingerprint store with no background task."""

    ttl_seconds: float = 30.0
    _seen: dict[str, float] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if type(self.ttl_seconds) not in (int, float) or self.ttl_seconds <= 0:
            raise ContractValidationError("ttl_seconds must be a positive number")
        self.ttl_seconds = float(self.ttl_seconds)

    def _prune(self, now: float) -> None:
        expired = [key for key, expires_at in self._seen.items() if expires_at <= now]
        for key in expired:
            self._seen.pop(key, None)

    def seen_or_add(self, fingerprint: str, *, now: float | None = None) -> bool:
        current = time.time() if now is None else require_finite_timestamp(now, "now")
        self._prune(current)
        if fingerprint in self._seen:
            return True
        self._seen[fingerprint] = current + self.ttl_seconds
        return False

    @property
    def size(self) -> int:
        return len(self._seen)

    def clear(self) -> None:
        self._seen.clear()
