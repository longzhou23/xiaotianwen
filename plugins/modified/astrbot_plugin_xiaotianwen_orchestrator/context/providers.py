"""Adapters from already-produced plugin snapshots to ContextSection values.

These classes accept plain mappings rather than plugin instances. Therefore a
shadow pass can never call Iris retrieval, VLM, embeddings, shared-context I/O
or mutate a ProviderRequest by accident.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..contracts import ContextSection
from ..contracts.validation import ContractValidationError
from .budgets import DEFAULT_SOURCE_PRIORITIES


def _require_snapshot(value: object, adapter_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractValidationError(f"{adapter_name} accepts a read-only snapshot mapping")
    return value


def _first_text(snapshot: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = snapshot.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _version(snapshot: Mapping[str, Any], fallback: str) -> str:
    value = snapshot.get("version")
    return value.strip() if isinstance(value, str) and value.strip() else fallback


def _positive_int(snapshot: Mapping[str, Any], key: str, default: int) -> int:
    value = snapshot.get(key, default)
    if type(value) is not int or value <= 0:
        raise ContractValidationError(f"snapshot {key} must be a positive integer")
    return value


class ContextAwareAdapter:
    """Map an existing current-scene render into a single request-scoped section."""

    source = "context_aware"

    def sections(self, snapshot: Mapping[str, Any]) -> tuple[ContextSection, ...]:
        snapshot = _require_snapshot(snapshot, type(self).__name__)
        content = _first_text(snapshot, "content", "scene", "rendered_scene")
        if content is None:
            return ()
        return (
            ContextSection(
                source=self.source,
                priority=DEFAULT_SOURCE_PRIORITIES[self.source],
                content=content,
                max_chars=_positive_int(snapshot, "max_chars", 4_000),
                cache_scope="request",
                version=_version(snapshot, "context-aware-snapshot-v1"),
                sensitive=True,
            ),
        )


class IrisMemoryAdapter:
    """Map completed Iris retrieval results; it never invokes Iris itself."""

    _FIELDS = (
        ("iris_l2", ("l2", "l2_context", "l2_memory"), 50),
        ("iris_l3", ("l3", "l3_context", "l3_memory"), 60),
        ("iris_profile", ("profile", "user_profile"), 70),
        ("iris_affection", ("affection", "affection_context"), 80),
    )

    def sections(self, snapshot: Mapping[str, Any]) -> tuple[ContextSection, ...]:
        snapshot = _require_snapshot(snapshot, type(self).__name__)
        result: list[ContextSection] = []
        version = _version(snapshot, "iris-snapshot-v1")
        raw_max_chars = _positive_int(snapshot, "max_chars", 3_000)
        for source, keys, priority in self._FIELDS:
            content = _first_text(snapshot, *keys)
            if content is None:
                continue
            result.append(
                ContextSection(
                    source=source,
                    priority=priority,
                    content=content,
                    max_chars=raw_max_chars,
                    cache_scope="request",
                    version=version,
                    sensitive=True,
                )
            )
        return tuple(result)


class ImageContextPoolAdapter:
    """Turn persisted image IDs/descriptions into one bounded text section."""

    source = "image_context_pool"

    def sections(self, snapshot: Mapping[str, Any]) -> tuple[ContextSection, ...]:
        snapshot = _require_snapshot(snapshot, type(self).__name__)
        entries = snapshot.get("entries", snapshot.get("images", ()))
        if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes, bytearray)):
            raise ContractValidationError("image context snapshot entries must be a list")
        ordered: list[tuple[int, int, Mapping[str, Any]]] = []
        for fallback_order, entry in enumerate(entries):
            if not isinstance(entry, Mapping):
                raise ContractValidationError("image context entries must be mappings")
            raw_order = entry.get("order", fallback_order)
            if type(raw_order) is not int or raw_order < 0:
                raise ContractValidationError("image context entry order must be a non-negative integer")
            ordered.append((raw_order, fallback_order, entry))
        lines: list[str] = []
        for _, _, entry in sorted(ordered, key=lambda item: (item[0], item[1])):
            image_id = entry.get("image_id") or entry.get("media_id")
            if not isinstance(image_id, str) or not image_id.strip():
                raise ContractValidationError("image context entries must include image_id")
            kind = entry.get("kind", "image")
            description = entry.get("description")
            rendered_description = (
                description.strip()
                if isinstance(description, str) and description.strip()
                else "尚未记录首次视觉描述"
            )
            line = f"[图片: {image_id.strip()}；类型: {str(kind).strip() or 'image'}；摘要: {rendered_description}]"
            artifacts = entry.get("artifacts", ())
            if isinstance(artifacts, Sequence) and not isinstance(artifacts, (str, bytes, bytearray)):
                usable = [str(item).strip() for item in artifacts if str(item).strip()]
                if usable:
                    line += f"；可用关联资源: {', '.join(usable)}"
            lines.append(line)
        if not lines:
            return ()
        raw_max_chars = _positive_int(snapshot, "max_chars", 3_000)
        return (
            ContextSection(
                source=self.source,
                priority=DEFAULT_SOURCE_PRIORITIES[self.source],
                content="\n".join(lines),
                max_chars=raw_max_chars,
                cache_scope="session",
                version=_version(snapshot, "image-context-pool-snapshot-v1"),
                sensitive=True,
            ),
        )


class SharedContextAdapter:
    """Expose a supplied shared-context snapshot only when explicitly enabled."""

    source = "shared_context"

    def __init__(self, *, enabled: bool = False) -> None:
        self.enabled = bool(enabled)

    def sections(self, snapshot: Mapping[str, Any]) -> tuple[ContextSection, ...]:
        if not self.enabled:
            return ()
        snapshot = _require_snapshot(snapshot, type(self).__name__)
        content = _first_text(snapshot, "content", "shared_context")
        if content is None:
            return ()
        raw_max_chars = _positive_int(snapshot, "max_chars", 2_000)
        return (
            ContextSection(
                source=self.source,
                priority=DEFAULT_SOURCE_PRIORITIES[self.source],
                content=content,
                max_chars=raw_max_chars,
                cache_scope="shared",
                version=_version(snapshot, "shared-context-snapshot-v1"),
                sensitive=True,
            ),
        )
