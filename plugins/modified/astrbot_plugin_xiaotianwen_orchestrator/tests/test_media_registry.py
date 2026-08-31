from __future__ import annotations

import json

from astrbot_plugin_xiaotianwen_orchestrator.contracts import MediaRef
from astrbot_plugin_xiaotianwen_orchestrator.media import (
    MediaRegistry,
    MediaStatus,
    VlmCacheKey,
    VlmDescriptionCache,
)


def _media(media_id: str, message_id: str, order: int, **overrides: object) -> MediaRef:
    values = {
        "media_id": media_id,
        "kind": "image",
        "message_id": message_id,
        "sender_id": "synthetic-user",
        "order": order,
        "description": None,
        "local_path": f"/synthetic/{media_id}.jpg",
        "content_hash": f"hash-{media_id}",
    }
    values.update(overrides)
    return MediaRef(**values)  # type: ignore[arg-type]


def test_image_context_mapping_preserves_ids_order_and_reuses_description() -> None:
    registry = MediaRegistry()
    imported = registry.import_image_context_pool(
        "group:synthetic",
        (
            {
                "image_id": "img-original",
                "message_id": "message-1",
                "sender_id": "synthetic-user",
                "order": 1,
                "description": "星空摘要",
                "content_hash": "same-content",
            },
            {
                "image_id": "img-first",
                "message_id": "message-1",
                "sender_id": "synthetic-user",
                "order": 0,
                "content_hash": "different-content",
            },
        ),
    )
    repeated = registry.register(
        "group:synthetic",
        _media(
            "img-new-message",
            "message-2",
            0,
            content_hash="same-content",
        ),
    )

    assert [item.media_id for item in imported] == ["img-original", "img-first"]
    assert [item.media_id for item in registry.list_session("group:synthetic")] == [
        "img-original",
        "img-first",
        "img-new-message",
    ]
    assert repeated.description == "星空摘要"


def test_reference_resolution_uses_latest_message_order_and_annotated_artifact() -> None:
    registry = MediaRegistry()
    registry.register("group:synthetic", _media("img-old", "message-old", 0))
    registry.register("group:synthetic", _media("img-a", "message-latest", 0))
    registry.register("group:synthetic", _media("img-b", "message-latest", 1))
    registry.link_artifact(
        "img-b",
        _media(
            "img-b-annotated",
            "message-latest",
            2,
            kind="annotated",
            local_path="/synthetic/img-b-annotated.png",
        ),
    )

    assert registry.resolve("group:synthetic", "第一张").media_id == "img-a"
    assert registry.resolve("group:synthetic", "第二张").media_id == "img-b"
    assert registry.resolve("group:synthetic", "刚才的标注图").media_id == "img-b-annotated"
    assert registry.parent_id("img-b-annotated") == "img-b"


def test_cleaned_file_returns_recoverable_metadata_without_stale_url_or_path() -> None:
    registry = MediaRegistry()
    registry.register(
        "group:synthetic",
        _media(
            "img-cleaned",
            "message-1",
            0,
            description="仍保留的摘要",
            source_url="https://temporary.invalid/image.jpg",
        ),
    )
    registry.mark_file_unavailable("img-cleaned")

    resolution = registry.resolve(
        "group:synthetic",
        "img-cleaned",
        require_file=True,
    )
    rendered = json.dumps(resolution.structural_summary(), ensure_ascii=False)

    assert resolution.status is MediaStatus.METADATA_ONLY
    assert resolution.media is not None
    assert resolution.media.description == "仍保留的摘要"
    assert "/synthetic/" not in rendered
    assert "temporary.invalid" not in rendered
    assert resolution.available_ids == ("img-cleaned",)


def test_vlm_cache_key_includes_content_provider_and_prompt_version() -> None:
    cache = VlmDescriptionCache(max_entries=2)
    key = VlmCacheKey("content-hash", "provider-a", "prompt-v1")
    cache.put(key, "首次描述", now=10, ttl_seconds=5)

    assert cache.get(key, now=14.9) == "首次描述"
    assert cache.get(VlmCacheKey("content-hash", "provider-b", "prompt-v1"), now=11) is None
    assert cache.get(VlmCacheKey("content-hash", "provider-a", "prompt-v2"), now=11) is None
    assert cache.get(key, now=15) is None
    assert "首次描述" not in json.dumps(cache.structural_summary(), ensure_ascii=False)
