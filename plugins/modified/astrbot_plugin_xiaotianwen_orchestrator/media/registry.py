"""In-memory registry that keeps media identity after files expire.

The registry owns metadata only.  It never downloads a URL, opens an image, or
stores base64 payloads.  ImageContextPool and Astrometry remain the owners of
their files and model calls.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Iterable

from ..contracts import ContractValidationError, MediaRef
from ..contracts.validation import require_identifier
from .resolver import resolve_media_reference


class MediaStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    METADATA_ONLY = "METADATA_ONLY"
    EXPIRED = "EXPIRED"
    MISSING = "MISSING"


@dataclass(frozen=True, slots=True)
class MediaResolution:
    """Tool-facing result with recoverable IDs and no raw path/URL logging."""

    status: MediaStatus
    media: MediaRef | None
    available_ids: tuple[str, ...]
    message: str

    @property
    def media_id(self) -> str | None:
        return self.media.media_id if self.media is not None else None

    def structural_summary(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "media_id": self.media_id,
            "available_ids": list(self.available_ids),
            "message": self.message,
        }


class MediaRegistry:
    """Request-independent metadata index with deterministic session order."""

    def __init__(self, *, max_items_per_session: int = 30) -> None:
        if type(max_items_per_session) is not int or max_items_per_session <= 0:
            raise ContractValidationError("max_items_per_session must be positive")
        self.max_items_per_session = max_items_per_session
        self._items: dict[str, MediaRef] = {}
        self._session_ids: dict[str, list[str]] = {}
        self._message_ids: dict[tuple[str, str], list[str]] = {}
        self._session_by_media: dict[str, str] = {}
        self._description_by_hash: dict[str, str] = {}
        self._parent_by_artifact: dict[str, str] = {}
        self._unavailable: set[str] = set()

    def register(self, session_id: str, media: MediaRef) -> MediaRef:
        session = require_identifier(session_id, "session_id")
        if not isinstance(media, MediaRef):
            raise ContractValidationError("registry accepts only MediaRef values")
        previous = self._items.get(media.media_id)
        if previous is not None:
            if (
                previous.message_id != media.message_id
                or previous.sender_id != media.sender_id
                or previous.kind != media.kind
            ):
                raise ContractValidationError("media_id already belongs to another media item")
            media = self._merge(previous, media)
        elif media.description is None and media.content_hash:
            cached_description = self._description_by_hash.get(media.content_hash)
            if cached_description:
                media = replace(media, description=cached_description)

        self._items[media.media_id] = media
        self._session_by_media[media.media_id] = session
        session_items = self._session_ids.setdefault(session, [])
        if media.media_id not in session_items:
            session_items.append(media.media_id)
        message_items = self._message_ids.setdefault((session, media.message_id), [])
        if media.media_id not in message_items:
            message_items.append(media.media_id)
            message_items.sort(key=lambda item_id: self._items[item_id].order)
        if media.description and media.content_hash:
            self._description_by_hash[media.content_hash] = media.description
        self._trim_session(session)
        return media

    @staticmethod
    def _merge(previous: MediaRef, current: MediaRef) -> MediaRef:
        return replace(
            current,
            description=current.description or previous.description,
            local_path=current.local_path or previous.local_path,
            source_url=current.source_url or previous.source_url,
            artifacts=tuple(dict.fromkeys((*previous.artifacts, *current.artifacts))),
            content_hash=current.content_hash or previous.content_hash,
            created_at=current.created_at if current.created_at is not None else previous.created_at,
            expires_at=current.expires_at if current.expires_at is not None else previous.expires_at,
        )

    def _trim_session(self, session_id: str) -> None:
        session_items = self._session_ids[session_id]
        while len(session_items) > self.max_items_per_session:
            removed_id = session_items.pop(0)
            removed = self._items.pop(removed_id, None)
            self._session_by_media.pop(removed_id, None)
            self._unavailable.discard(removed_id)
            if removed is not None:
                message_items = self._message_ids.get((session_id, removed.message_id), [])
                if removed_id in message_items:
                    message_items.remove(removed_id)
                if not message_items:
                    self._message_ids.pop((session_id, removed.message_id), None)

    def import_image_context_pool(
        self,
        session_id: str,
        entries: Iterable[dict[str, Any]],
        *,
        default_sender_id: str = "synthetic-unknown-sender",
    ) -> tuple[MediaRef, ...]:
        """Map a completed ImageContextPool snapshot without model or file I/O."""

        imported: list[MediaRef] = []
        for index, raw in enumerate(entries):
            if not isinstance(raw, dict):
                raise ContractValidationError("image context entries must be dictionaries")
            media_id = raw.get("image_id") or raw.get("media_id")
            message_id = raw.get("message_id") or raw.get("msg_id")
            if not media_id or not message_id:
                raise ContractValidationError("image context entry requires image_id and message_id")
            ref = MediaRef(
                media_id=str(media_id),
                kind=str(raw.get("kind") or "image"),
                message_id=str(message_id),
                sender_id=str(raw.get("sender_id") or default_sender_id),
                order=int(raw.get("order", index)),
                description=raw.get("description"),
                local_path=raw.get("local_path"),
                source_url=raw.get("source_url") or raw.get("url"),
                content_hash=raw.get("content_hash"),
                created_at=raw.get("created_at") or raw.get("timestamp"),
                expires_at=raw.get("expires_at"),
            )
            imported.append(self.register(session_id, ref))
        return tuple(imported)

    def link_artifact(self, parent_media_id: str, artifact: MediaRef) -> MediaRef:
        parent_id = require_identifier(parent_media_id, "parent_media_id")
        parent = self._items.get(parent_id)
        if parent is None:
            raise ContractValidationError("parent media_id is not registered")
        session_id = self._session_by_media[parent_id]
        registered = self.register(session_id, artifact)
        self._parent_by_artifact[registered.media_id] = parent_id
        updated_parent = replace(
            parent,
            artifacts=tuple(dict.fromkeys((*parent.artifacts, registered.media_id))),
        )
        self._items[parent_id] = updated_parent
        return registered

    def parent_id(self, artifact_media_id: str) -> str | None:
        return self._parent_by_artifact.get(artifact_media_id)

    def get(self, media_id: str) -> MediaRef | None:
        return self._items.get(media_id)

    def list_session(self, session_id: str) -> tuple[MediaRef, ...]:
        ids = self._session_ids.get(session_id, ())
        return tuple(self._items[item_id] for item_id in ids if item_id in self._items)

    def mark_file_unavailable(self, media_id: str) -> None:
        if media_id not in self._items:
            raise ContractValidationError("media_id is not registered")
        self._unavailable.add(media_id)
        self._items[media_id] = replace(self._items[media_id], local_path=None)

    def resolve(
        self,
        session_id: str,
        reference: str,
        *,
        now: float | None = None,
        require_file: bool = False,
    ) -> MediaResolution:
        session_items = self.list_session(session_id)
        media = resolve_media_reference(reference, session_items)
        available_ids = tuple(item.media_id for item in session_items)
        if media is None:
            return MediaResolution(
                MediaStatus.MISSING,
                None,
                available_ids,
                "未找到对应媒体；请使用可用 media ID 重试。",
            )
        if media.expires_at is not None and now is not None and now >= media.expires_at:
            return MediaResolution(
                MediaStatus.EXPIRED,
                media,
                available_ids,
                "媒体文件已过期；ID 和摘要仍可用，可重新上传后关联同一请求。",
            )
        if require_file and (media.media_id in self._unavailable or not media.local_path):
            return MediaResolution(
                MediaStatus.METADATA_ONLY,
                media,
                available_ids,
                "媒体原文件已清理；ID 和摘要仍保留，请重新上传，不能盲目重试旧路径。",
            )
        return MediaResolution(MediaStatus.AVAILABLE, media, available_ids, "媒体已解析。")

    def structural_summary(self, session_id: str) -> dict[str, object]:
        items = self.list_session(session_id)
        return {
            "session_id": session_id,
            "count": len(items),
            "media": [item.structural_summary() for item in items],
            "unavailable_ids": sorted(self._unavailable.intersection(item.media_id for item in items)),
        }
