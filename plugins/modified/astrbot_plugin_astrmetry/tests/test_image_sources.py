from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from main import (  # noqa: E402
    REQUEST_IMAGE_SOURCES_KEY,
    MyPlugin,
    _find_images,
    _materialize_image,
)


class _Event:
    def __init__(self, sources):
        self.extras = {REQUEST_IMAGE_SOURCES_KEY: sources}

    def get_messages(self):
        return []

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def set_extra(self, key, value):
        self.extras[key] = value

    def get_message_str(self):
        return "请看看这张图片"


class ImageSourceTests(unittest.TestCase):
    def test_request_image_sources_are_visible_and_deduplicated(self):
        event = _Event(["/tmp/current.jpg", "/tmp/current.jpg", "/tmp/second.jpg"])

        self.assertEqual(
            _find_images(event),
            ["/tmp/current.jpg", "/tmp/second.jpg"],
        )

    def test_local_request_image_can_be_materialized(self):
        async def run():
            with tempfile.TemporaryDirectory() as temporary_dir:
                source = Path(temporary_dir) / "source.jpg"
                destination = Path(temporary_dir) / "destination.jpg"
                source.write_bytes(b"current-request-image")

                await _materialize_image(str(source), destination, session=None)

                self.assertEqual(destination.read_bytes(), b"current-request-image")

        asyncio.run(run())

    def test_llm_hook_snapshots_provider_request_images_for_tool_call(self):
        async def run():
            event = _Event([])
            request = SimpleNamespace(
                image_urls=["/tmp/current.jpg", "/tmp/current.jpg"],
                extra_user_content_parts=[],
                system_prompt="",
            )
            plugin = object.__new__(MyPlugin)
            plugin.auto_analyze = True
            plugin.auto_hint = True
            plugin._inject_recent_analysis_context = lambda event, request: None
            plugin._get_cached_annotations = lambda event: []

            await plugin._add_star_tool_hint(event, request)

            self.assertEqual(
                event.get_extra(REQUEST_IMAGE_SOURCES_KEY),
                ["/tmp/current.jpg"],
            )
            self.assertEqual(_find_images(event), ["/tmp/current.jpg"])
            self.assertEqual(len(request.extra_user_content_parts), 1)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
