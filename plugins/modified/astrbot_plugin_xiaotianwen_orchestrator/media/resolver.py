"""Deterministic Chinese/ID media reference resolution."""

from __future__ import annotations

import re
from typing import Iterable

from ..contracts import MediaRef


_ORDINALS = {
    "第一张": 0,
    "第1张": 0,
    "第二张": 1,
    "第2张": 1,
    "第三张": 2,
    "第3张": 2,
    "第四张": 3,
    "第4张": 3,
}


def resolve_media_reference(reference: str, media: Iterable[MediaRef]) -> MediaRef | None:
    """Resolve explicit IDs first, then recent-message ordinals and pronouns."""

    items = tuple(media)
    if not items or not isinstance(reference, str):
        return None
    normalized = reference.strip()
    for item in items:
        if normalized == item.media_id or re.search(rf"(?<![\w-]){re.escape(item.media_id)}(?![\w-])", normalized):
            return item
    if "标注" in normalized or "解算图" in normalized:
        for item in reversed(items):
            if item.kind in {"annotated", "astrometry_annotation", "star_annotated"}:
                return item
    latest_message_id = items[-1].message_id
    latest_group = tuple(item for item in items if item.message_id == latest_message_id)
    ordered_group = tuple(sorted(latest_group, key=lambda item: item.order))
    for label, index in _ORDINALS.items():
        if label in normalized:
            return ordered_group[index] if index < len(ordered_group) else None
    if any(token in normalized for token in ("这张", "那张", "刚才", "上面", "最近")):
        return items[-1]
    return None
