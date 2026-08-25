from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
import types
import unittest
from pathlib import Path
from typing import Any


PLUGIN_PATH = Path(__file__).resolve().parents[1] / "main.py"


def _decorator(*args: Any, **kwargs: Any):
    def wrapper(func):
        return func

    return wrapper


def load_plugin_module():
    class Logger:
        def debug(self, *args: Any, **kwargs: Any) -> None:
            pass

        def info(self, *args: Any, **kwargs: Any) -> None:
            pass

        def warning(self, *args: Any, **kwargs: Any) -> None:
            pass

        def error(self, *args: Any, **kwargs: Any) -> None:
            pass

    astrbot = types.ModuleType("astrbot")
    astrbot.logger = Logger()
    api = types.ModuleType("astrbot.api")
    star = types.ModuleType("astrbot.api.star")
    event = types.ModuleType("astrbot.api.event")
    filter_mod = types.ModuleType("astrbot.api.event.filter")
    components = types.ModuleType("astrbot.api.message_components")
    provider = types.ModuleType("astrbot.api.provider")
    core = types.ModuleType("astrbot.core")
    core_message = types.ModuleType("astrbot.core.message")
    message_event_result = types.ModuleType("astrbot.core.message.message_event_result")

    class Star:
        def __init__(self, context):
            self.context = context

    class Context:
        def get_all_stars(self):
            return []

    class AstrMessageEvent:
        pass

    class Plain:
        def __init__(self, text: str):
            self.text = text

    class LLMResponse:
        pass

    class ProviderRequest:
        pass

    class MessageChain:
        def __init__(self, chain=None):
            self.chain = chain or []

    class EventMessageType:
        ALL = object()

    class PlatformAdapterType:
        AIOCQHTTP = object()

    filter_mod.platform_adapter_type = _decorator
    filter_mod.event_message_type = _decorator
    filter_mod.on_llm_request = _decorator
    filter_mod.on_llm_response = _decorator
    filter_mod.on_decorating_result = _decorator
    filter_mod.after_message_sent = _decorator
    filter_mod.on_astrbot_loaded = _decorator
    filter_mod.command = _decorator
    filter_mod.EventMessageType = EventMessageType
    filter_mod.PlatformAdapterType = PlatformAdapterType
    star.Star = Star
    star.Context = Context
    event.AstrMessageEvent = AstrMessageEvent
    event.filter = filter_mod
    components.Plain = Plain
    provider.LLMResponse = LLMResponse
    provider.ProviderRequest = ProviderRequest
    message_event_result.MessageChain = MessageChain

    for name, module in {
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.star": star,
        "astrbot.api.event": event,
        "astrbot.api.event.filter": filter_mod,
        "astrbot.api.message_components": components,
        "astrbot.api.provider": provider,
        "astrbot.core": core,
        "astrbot.core.message": core_message,
        "astrbot.core.message.message_event_result": message_event_result,
    }.items():
        sys.modules[name] = module

    spec = importlib.util.spec_from_file_location("recall_cancel_main", PLUGIN_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeContext:
    def get_all_stars(self):
        return []


class FakeMessageObj:
    def __init__(self, raw: dict[str, Any], message_id: str = "") -> None:
        self.raw_message = raw
        self.message_id = message_id


class FakeEvent:
    def __init__(
        self,
        raw: dict[str, Any],
        *,
        umo: str = "qq:group:100",
        message_id: str = "",
    ) -> None:
        self.message_obj = FakeMessageObj(raw, message_id)
        self.unified_msg_origin = umo
        self.stopped = False
        self.extras: dict[str, Any] = {}

    def stop_event(self) -> None:
        self.stopped = True

    def set_extra(self, key: str, value: Any) -> None:
        self.extras[key] = value

    def get_extra(self, key: str, default: Any = None) -> Any:
        return self.extras.get(key, default)

    def get_sender_id(self) -> str:
        return "sender-1"


class FakeLLMResponse:
    def __init__(self, completion_text: str = "reply") -> None:
        self.completion_text = completion_text


class FakeBotRecord:
    def __init__(
        self,
        msg_id: str,
        content: str,
        talking_to: str = "sender-1",
        timestamp: float | None = None,
    ) -> None:
        self.msg_id = msg_id
        self.content = content
        self.talking_to = talking_to
        self.is_bot = True
        self.timestamp = time.time() if timestamp is None else timestamp


class FakeContextAware:
    def __init__(self, *, remove_user: bool = True, remove_bot: bool = True) -> None:
        self.remove_user = remove_user
        self.remove_bot = remove_bot
        self.user_removed: list[tuple[str, str]] = []
        self.bot_removed: list[str] = []
        self.bot_records: dict[str, list[FakeBotRecord]] = {}

    async def remove_message(self, unified_msg_origin: str, message_id: str) -> bool:
        self.user_removed.append((unified_msg_origin, message_id))
        return self.remove_user

    async def remove_last_bot_response(self, unified_msg_origin: str) -> bool:
        self.bot_removed.append(unified_msg_origin)
        records = self.bot_records.get(unified_msg_origin, [])
        if records:
            records.pop()
        return self.remove_bot

    def add_bot_response(
        self,
        unified_msg_origin: str,
        msg_id: str,
        content: str = "reply",
        talking_to: str = "sender-1",
        timestamp: float | None = None,
    ) -> None:
        self.bot_records.setdefault(unified_msg_origin, []).append(
            FakeBotRecord(msg_id, content, talking_to, timestamp)
        )

    async def get_last_bot_response_marker(
        self,
        unified_msg_origin: str,
        response_preview: str | None = None,
        sender_id: str | None = None,
        min_timestamp: float | None = None,
    ) -> str | None:
        records = self.bot_records.get(unified_msg_origin, [])
        if not records:
            return None
        record = records[-1]
        if response_preview is not None and record.content != response_preview:
            return None
        if sender_id is not None and record.talking_to != sender_id:
            return None
        if min_timestamp is not None and record.timestamp < min_timestamp:
            return None
        return record.msg_id

    async def remove_last_bot_response_if_matches(
        self,
        unified_msg_origin: str,
        response_preview: str,
        sender_id: str,
        context_bot_msg_id: str,
    ) -> bool:
        marker = await self.get_last_bot_response_marker(
            unified_msg_origin,
            response_preview,
            sender_id,
        )
        if marker != context_bot_msg_id:
            return False
        return await self.remove_last_bot_response(unified_msg_origin)


def make_recall_event(message_id: str = "42") -> FakeEvent:
    return FakeEvent(
        {
            "post_type": "notice",
            "notice_type": "group_recall",
            "message_id": message_id,
            "operator_id": "10001",
        }
    )


class RecallCancelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.mod = load_plugin_module()

    def make_plugin(self, config: dict[str, Any] | None = None):
        plugin = self.mod.Main(FakeContext(), config)
        plugin._context_aware = FakeContextAware()
        return plugin

    async def test_recall_notice_continues_and_stops_matching_pending(self) -> None:
        plugin = self.make_plugin()
        original = FakeEvent({"message_id": "42"}, message_id="42")
        await plugin._state.add_pending_request(
            "42",
            original.unified_msg_origin,
            "sender-1",
            original,
        )

        recall = make_recall_event("42")
        await plugin.on_all_message(recall)

        self.assertFalse(recall.stopped)
        self.assertTrue(original.stopped)
        self.assertTrue(original.extras["agent_stop_requested"])
        self.assertEqual(plugin._context_aware.user_removed, [("qq:group:100", "42")])
        self.assertEqual(plugin._context_aware.bot_removed, [])

    async def test_safe_policy_does_not_remove_bot_response_for_old_recall(self) -> None:
        plugin = self.make_plugin({"remove_bot_response_policy": "safe"})

        await plugin.on_all_message(make_recall_event("42"))

        self.assertEqual(plugin._context_aware.user_removed, [("qq:group:100", "42")])
        self.assertEqual(plugin._context_aware.bot_removed, [])

    async def test_safe_policy_removes_bot_response_after_llm_response_seen(self) -> None:
        plugin = self.make_plugin({"remove_bot_response_policy": "safe"})
        original = FakeEvent({"message_id": "42"}, message_id="42")

        await plugin.on_llm_request(original, object())
        await plugin.on_llm_response(original, FakeLLMResponse())
        plugin._context_aware.add_bot_response(original.unified_msg_origin, "bot-42")
        await plugin.on_llm_response_recorded(original, FakeLLMResponse())
        await plugin.on_all_message(make_recall_event("42"))

        self.assertTrue(original.stopped)
        self.assertEqual(plugin._context_aware.bot_removed, ["qq:group:100"])
        self.assertEqual(plugin._context_aware.bot_records["qq:group:100"], [])

    async def test_safe_policy_does_not_remove_bot_response_before_recorded_marker(self) -> None:
        plugin = self.make_plugin({"remove_bot_response_policy": "safe"})
        original = FakeEvent({"message_id": "42"}, message_id="42")

        await plugin.on_llm_request(original, object())
        await plugin.on_llm_response(original, FakeLLMResponse())
        await plugin.on_all_message(make_recall_event("42"))

        self.assertTrue(original.stopped)
        self.assertEqual(plugin._context_aware.bot_removed, [])

    async def test_safe_policy_does_not_remove_newer_bot_response(self) -> None:
        plugin = self.make_plugin({"remove_bot_response_policy": "safe"})
        original = FakeEvent({"message_id": "42"}, message_id="42")

        await plugin.on_llm_request(original, object())
        await plugin.on_llm_response(original, FakeLLMResponse("first"))
        plugin._context_aware.add_bot_response(
            original.unified_msg_origin,
            "bot-first",
            "first",
        )
        await plugin.on_llm_response_recorded(original, FakeLLMResponse("first"))
        plugin._context_aware.add_bot_response(
            original.unified_msg_origin,
            "bot-newer",
            "newer",
        )

        await plugin.on_all_message(make_recall_event("42"))

        self.assertEqual(plugin._context_aware.bot_removed, [])
        self.assertEqual(
            [record.msg_id for record in plugin._context_aware.bot_records["qq:group:100"]],
            ["bot-first", "bot-newer"],
        )

    async def test_safe_policy_does_not_remove_newer_same_content_response(self) -> None:
        plugin = self.make_plugin({"remove_bot_response_policy": "safe"})
        first = FakeEvent({"message_id": "42"}, message_id="42")
        second = FakeEvent({"message_id": "43"}, message_id="43")

        await plugin.on_llm_request(first, object())
        await plugin.on_llm_response(first, FakeLLMResponse("same"))
        plugin._context_aware.add_bot_response(
            first.unified_msg_origin,
            "bot-first",
            "same",
        )
        await plugin.on_llm_response_recorded(first, FakeLLMResponse("same"))
        await plugin.on_llm_request(second, object())
        await plugin.on_llm_response(second, FakeLLMResponse("same"))
        plugin._context_aware.add_bot_response(
            second.unified_msg_origin,
            "bot-second",
            "same",
        )

        await plugin.on_all_message(make_recall_event("42"))

        self.assertEqual(plugin._context_aware.bot_removed, [])
        self.assertEqual(
            [record.msg_id for record in plugin._context_aware.bot_records["qq:group:100"]],
            ["bot-first", "bot-second"],
        )

    async def test_deferred_safe_cleanup_handles_recall_during_context_recording(self) -> None:
        plugin = self.make_plugin({"remove_bot_response_policy": "safe"})
        original = FakeEvent({"message_id": "42"}, message_id="42")

        await plugin.on_llm_request(original, object())
        await plugin.on_llm_response(original, FakeLLMResponse("late"))
        await plugin.on_all_message(make_recall_event("42"))
        plugin._context_aware.add_bot_response(
            original.unified_msg_origin,
            "bot-late",
            "late",
        )
        await asyncio.sleep(0.1)

        self.assertEqual(plugin._context_aware.bot_removed, ["qq:group:100"])
        self.assertEqual(plugin._context_aware.bot_records["qq:group:100"], [])

    async def test_safe_cleanup_handles_recall_after_context_record_before_marker(self) -> None:
        plugin = self.make_plugin({"remove_bot_response_policy": "safe"})
        original = FakeEvent({"message_id": "42"}, message_id="42")

        await plugin.on_llm_request(original, object())
        await plugin.on_llm_response(original, FakeLLMResponse("mid"))
        plugin._context_aware.add_bot_response(
            original.unified_msg_origin,
            "bot-mid",
            "mid",
        )
        await plugin.on_all_message(make_recall_event("42"))

        self.assertEqual(plugin._context_aware.bot_removed, ["qq:group:100"])
        self.assertEqual(plugin._context_aware.bot_records["qq:group:100"], [])

    async def test_deferred_safe_cleanup_does_not_remove_old_matching_response(self) -> None:
        plugin = self.make_plugin({"remove_bot_response_policy": "safe"})
        original = FakeEvent({"message_id": "42"}, message_id="42")
        plugin._context_aware.add_bot_response(
            original.unified_msg_origin,
            "bot-old",
            "same",
        )

        await plugin.on_llm_request(original, object())
        await plugin.on_llm_response(original, FakeLLMResponse("same"))
        await plugin.on_all_message(make_recall_event("42"))
        await asyncio.sleep(0.1)

        self.assertEqual(plugin._context_aware.bot_removed, [])
        self.assertEqual(
            [record.msg_id for record in plugin._context_aware.bot_records["qq:group:100"]],
            ["bot-old"],
        )

    async def test_request_after_recall_is_stopped(self) -> None:
        plugin = self.make_plugin()
        original = FakeEvent({"message_id": "42"}, message_id="42")

        await plugin.on_all_message(make_recall_event("42"))
        await plugin.on_llm_request(original, object())

        self.assertTrue(original.stopped)
        self.assertTrue(original.extras["agent_stop_requested"])

    async def test_decorating_result_blocks_recalled_response_without_sleep(self) -> None:
        plugin = self.make_plugin({"remove_bot_response_policy": "safe"})
        original = FakeEvent({"message_id": "42"}, message_id="42")

        await plugin.on_llm_request(original, object())
        await plugin.on_llm_response(original, FakeLLMResponse())
        plugin._context_aware.add_bot_response(original.unified_msg_origin, "bot-42")
        await plugin.on_llm_response_recorded(original, FakeLLMResponse())
        await plugin._state.add_recalled_message("42", original.unified_msg_origin, "")
        await plugin.on_decorating_result(original)

        self.assertTrue(original.stopped)
        self.assertEqual(plugin._stats.send_blocked, 1)
        self.assertEqual(plugin._context_aware.bot_removed, ["qq:group:100"])
        self.assertEqual(plugin._context_aware.bot_records["qq:group:100"], [])

    async def test_legacy_policy_keeps_old_bot_cleanup_behavior(self) -> None:
        plugin = self.make_plugin({"remove_bot_response_policy": "legacy_last"})

        await plugin.on_all_message(make_recall_event("42"))

        self.assertEqual(plugin._context_aware.bot_removed, ["qq:group:100"])

    async def test_invalid_config_falls_back_to_safe_defaults(self) -> None:
        cfg = self.mod.RecallCancelConfig.from_config(
            {
                "record_expire_seconds": 1,
                "cleanup_interval": "bad",
                "remove_bot_response_policy": "wat",
            }
        )

        self.assertEqual(cfg.record_expire_seconds, 300)
        self.assertEqual(cfg.cleanup_interval, 60)
        self.assertEqual(cfg.remove_bot_response_policy, "safe")


if __name__ == "__main__":
    unittest.main()
