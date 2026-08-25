"""
AstrBot Recall Cancel Plugin v2.1.3

Cancels pending replies when the user recalls the original message.

Highlights in v2.1.3:
- Adapted to AstrBot 4.20.x plugin registry behavior.
- Stops running Agent generation with agent_stop_requested plus stop_event().
- Cleans pending state earlier and tightens UUID-like ID detection.
- Leaves recall notice propagation open for other recall-aware plugins.
- Makes context_aware bot-response cleanup safe by default and concurrency-aware.

Author: Muyouzhi
Version: 2.1.3
"""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from astrbot import logger
from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.core.message.message_event_result import MessageChain

if TYPE_CHECKING:
    pass


# ============================================================================
# Constants
# ============================================================================

# 撤回事件的 notice_type
NOTICE_GROUP_RECALL: Final = "group_recall"
NOTICE_FRIEND_RECALL: Final = "friend_recall"

# 记录过期时间（秒）- 消息ID在此时间后被清理
DEFAULT_RECORD_EXPIRE_SECONDS: Final = 300  # 5 分钟

# 清理间隔（秒）
DEFAULT_CLEANUP_INTERVAL: Final = 60

# context_aware Bot 回复清理策略
BOT_RESPONSE_POLICY_OFF: Final = "off"
BOT_RESPONSE_POLICY_SAFE: Final = "safe"
BOT_RESPONSE_POLICY_LEGACY_LAST: Final = "legacy_last"
BOT_RESPONSE_POLICIES: Final = frozenset(
    {
        BOT_RESPONSE_POLICY_OFF,
        BOT_RESPONSE_POLICY_SAFE,
        BOT_RESPONSE_POLICY_LEGACY_LAST,
    }
)

# 插件版本
PLUGIN_VERSION: Final = "2.1.3"


# ============================================================================
# Data Structures
# ============================================================================


@dataclass(slots=True)
class PendingRequest:
    """正在处理的 LLM 请求记录"""
    message_id: str  # 原始消息 ID
    unified_msg_origin: str  # 会话标识
    sender_id: str  # 发送者 ID
    timestamp: float  # monotonic 请求时间戳
    event: AstrMessageEvent | None = None  # 事件引用（用于 stop_event）
    llm_response_preview: str | None = None  # LLM 回复预览（对齐 context_aware）
    llm_response_started_at: float | None = None  # wall time, 对齐 context_aware
    llm_response_sequence: int = 0  # 本插件内的会话级 LLM 响应序号
    context_bot_msg_id: str | None = None  # context_aware 中对应 Bot 记录 ID


@dataclass(slots=True)
class RecalledMessage:
    """已撤回的消息记录"""
    message_id: str  # 被撤回消息的 ID
    unified_msg_origin: str  # 会话标识
    operator_id: str  # 撤回操作者 ID
    timestamp: float  # monotonic 撤回时间戳
    context_user_removed: bool = False  # 是否已从 context_aware 清理用户消息
    context_bot_removed: bool = False  # 是否已从 context_aware 清理 Bot 回复


@dataclass
class PluginStats:
    """插件统计信息"""
    recalls_detected: int = 0  # 检测到的撤回次数
    llm_requests_blocked: int = 0  # 阻止的 LLM 请求次数
    llm_responses_blocked: int = 0  # 阻止的 LLM 响应次数
    send_blocked: int = 0  # 阻止的发送次数
    context_aware_user_cleaned: int = 0  # 清理的 context_aware 用户消息次数
    context_aware_bot_cleaned: int = 0  # 清理的 context_aware Bot 回复次数


@dataclass(slots=True)
class RecallCancelConfig:
    """插件配置，来自 _conf_schema.json。"""

    record_expire_seconds: float = DEFAULT_RECORD_EXPIRE_SECONDS
    cleanup_interval: float = DEFAULT_CLEANUP_INTERVAL
    context_aware_cleanup: bool = True
    remove_bot_response_policy: str = BOT_RESPONSE_POLICY_SAFE

    @staticmethod
    def _get(config: Any, key: str, default: Any) -> Any:
        if config is None:
            return default
        getter = getattr(config, "get", None)
        if callable(getter):
            try:
                return getter(key, default)
            except Exception:
                return default
        if isinstance(config, dict):
            return config.get(key, default)
        return getattr(config, key, default)

    @staticmethod
    def _float(value: Any, default: float, minimum: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed >= minimum else default

    @staticmethod
    def _bool(value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "on", "开", "开启"}:
                return True
            if lowered in {"0", "false", "no", "off", "关", "关闭"}:
                return False
        return default

    @classmethod
    def from_config(cls, config: Any) -> "RecallCancelConfig":
        record_expire_seconds = cls._float(
            cls._get(config, "record_expire_seconds", DEFAULT_RECORD_EXPIRE_SECONDS),
            DEFAULT_RECORD_EXPIRE_SECONDS,
            minimum=10,
        )
        cleanup_interval = cls._float(
            cls._get(config, "cleanup_interval", DEFAULT_CLEANUP_INTERVAL),
            DEFAULT_CLEANUP_INTERVAL,
            minimum=5,
        )
        policy = str(
            cls._get(config, "remove_bot_response_policy", BOT_RESPONSE_POLICY_SAFE)
        ).strip()
        if policy not in BOT_RESPONSE_POLICIES:
            policy = BOT_RESPONSE_POLICY_SAFE
        return cls(
            record_expire_seconds=record_expire_seconds,
            cleanup_interval=cleanup_interval,
            context_aware_cleanup=cls._bool(
                cls._get(config, "context_aware_cleanup", True),
                True,
            ),
            remove_bot_response_policy=policy,
        )


# ============================================================================
# Recall State Manager
# ============================================================================


class RecallStateManager:
    """撤回状态管理器 - 线程安全的状态存储"""

    __slots__ = (
        "_pending_requests",
        "_recalled_messages",
        "_response_sequences",
        "_lock",
    )
    
    def __init__(self) -> None:
        self._pending_requests: dict[str, PendingRequest] = {}
        self._recalled_messages: dict[str, RecalledMessage] = {}
        self._response_sequences: dict[str, int] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _compose_key(unified_msg_origin: str, message_id: str) -> str:
        return f"{unified_msg_origin}::{message_id}"
    
    async def add_pending_request(
        self,
        message_id: str,
        unified_msg_origin: str,
        sender_id: str,
        event: AstrMessageEvent | None = None,
    ) -> None:
        """添加待处理的 LLM 请求"""
        key = self._compose_key(unified_msg_origin, message_id)
        async with self._lock:
            self._pending_requests[key] = PendingRequest(
                message_id=message_id,
                unified_msg_origin=unified_msg_origin,
                sender_id=sender_id,
                timestamp=time.monotonic(),
                event=event,
            )
    
    async def remove_pending_request(
        self, message_id: str, unified_msg_origin: str
    ) -> PendingRequest | None:
        """移除待处理的请求"""
        key = self._compose_key(unified_msg_origin, message_id)
        async with self._lock:
            return self._pending_requests.pop(key, None)
    
    async def get_pending_request(
        self, message_id: str, unified_msg_origin: str
    ) -> PendingRequest | None:
        """获取待处理的请求"""
        key = self._compose_key(unified_msg_origin, message_id)
        async with self._lock:
            return self._pending_requests.get(key)
    
    async def add_recalled_message(
        self,
        message_id: str,
        unified_msg_origin: str,
        operator_id: str,
    ) -> None:
        """添加已撤回的消息记录"""
        key = self._compose_key(unified_msg_origin, message_id)
        async with self._lock:
            self._recalled_messages[key] = RecalledMessage(
                message_id=message_id,
                unified_msg_origin=unified_msg_origin,
                operator_id=operator_id,
                timestamp=time.monotonic(),
            )
    
    async def is_recalled(self, message_id: str, unified_msg_origin: str) -> bool:
        """检查消息是否已被撤回"""
        key = self._compose_key(unified_msg_origin, message_id)
        async with self._lock:
            return key in self._recalled_messages
    
    async def get_recalled_message(
        self, message_id: str, unified_msg_origin: str
    ) -> RecalledMessage | None:
        """获取撤回记录"""
        key = self._compose_key(unified_msg_origin, message_id)
        async with self._lock:
            return self._recalled_messages.get(key)
    
    async def mark_pending_response_seen(
        self,
        message_id: str,
        unified_msg_origin: str,
        response_preview: str,
        context_bot_msg_id: str | None,
        response_started_at: float | None = None,
    ) -> None:
        """标记待处理请求已有对应的 context_aware Bot 记录。"""
        key = self._compose_key(unified_msg_origin, message_id)
        async with self._lock:
            if key in self._pending_requests:
                self._pending_requests[key].llm_response_preview = response_preview
                if response_started_at is not None:
                    self._pending_requests[key].llm_response_started_at = (
                        response_started_at
                    )
                    if self._pending_requests[key].llm_response_sequence <= 0:
                        sequence = self._response_sequences.get(unified_msg_origin, 0) + 1
                        self._response_sequences[unified_msg_origin] = sequence
                        self._pending_requests[key].llm_response_sequence = sequence
                self._pending_requests[key].context_bot_msg_id = context_bot_msg_id

    async def is_latest_response_sequence(
        self, unified_msg_origin: str, sequence: int
    ) -> bool:
        """检查给定响应序号是否仍是当前会话最新响应。"""
        async with self._lock:
            return self._response_sequences.get(unified_msg_origin, 0) == sequence

    async def mark_context_user_removed(
        self, message_id: str, unified_msg_origin: str
    ) -> None:
        """标记 context_aware 用户消息已清理。"""
        key = self._compose_key(unified_msg_origin, message_id)
        async with self._lock:
            if key in self._recalled_messages:
                self._recalled_messages[key].context_user_removed = True

    async def mark_context_bot_removed(
        self, message_id: str, unified_msg_origin: str
    ) -> None:
        """标记 context_aware Bot 回复已清理。"""
        key = self._compose_key(unified_msg_origin, message_id)
        async with self._lock:
            if key in self._recalled_messages:
                self._recalled_messages[key].context_bot_removed = True
    
    async def cleanup_expired(
        self, expire_seconds: float = DEFAULT_RECORD_EXPIRE_SECONDS
    ) -> int:
        """清理过期记录，返回清理数量"""
        now = time.monotonic()
        cleaned = 0
        async with self._lock:
            # 清理过期的待处理请求
            expired_pending = [
                k for k, v in self._pending_requests.items()
                if now - v.timestamp > expire_seconds
            ]
            for k in expired_pending:
                del self._pending_requests[k]
                cleaned += 1
            
            # 清理过期的撤回记录
            expired_recalled = [
                k for k, v in self._recalled_messages.items()
                if now - v.timestamp > expire_seconds
            ]
            for k in expired_recalled:
                del self._recalled_messages[k]
                cleaned += 1
        
        return cleaned
    
    async def get_stats(self) -> tuple[int, int]:
        """获取当前记录数量 (pending, recalled)"""
        async with self._lock:
            return len(self._pending_requests), len(self._recalled_messages)


# ============================================================================
# Context Aware Integration
# ============================================================================


class ContextAwareIntegration:
    """Integration layer for astrbot_plugin_context_aware."""
    
    __slots__ = ("_context", "_plugin_instance", "_plugin_module_path")
    
    def __init__(self, context: star.Context) -> None:
        self._context = context
        self._plugin_instance: Any = None
        self._plugin_module_path: str | None = None
    
    @staticmethod
    def _resolve_star_instance(star_entry: Any) -> Any:
        star_instance = getattr(star_entry, "star_cls", None)
        return star_instance if star_instance is not None else star_entry
    
    @staticmethod
    def _is_context_aware_plugin(plugin: Any) -> bool:
        if plugin is None:
            return False
        module_name = getattr(plugin.__class__, "__module__", "")
        plugin_name = str(getattr(plugin, "name", "") or "")
        return (
            (
                "context_aware" in module_name
                or plugin_name == "astrbot_plugin_context_aware"
            )
            and hasattr(plugin, "remove_message")
            and hasattr(plugin, "remove_last_bot_response")
        )
    
    def _get_plugin(self) -> Any:
        """Get the current context_aware plugin instance if available."""
        plugin = self._plugin_instance
        if self._is_context_aware_plugin(plugin):
            return plugin
        
        self._plugin_instance = None
        self._plugin_module_path = None
        try:
            for star_entry in self._context.get_all_stars():
                if getattr(star_entry, "activated", True) is False:
                    continue
                
                star_instance = self._resolve_star_instance(star_entry)
                if not self._is_context_aware_plugin(star_instance):
                    continue
                
                module_name = getattr(star_instance.__class__, "__module__", "")
                self._plugin_instance = star_instance
                if self._plugin_module_path != module_name:
                    logger.info(
                        "[RecallCancel] context_aware detected, sync cleanup enabled"
                    )
                self._plugin_module_path = module_name
                return self._plugin_instance
        except Exception as e:
            logger.debug(f"[RecallCancel] Failed to inspect context_aware plugin: {e}")
        
        return None
    
    async def _call_plugin_bool(self, method_name: str, *args: Any) -> bool:
        plugin = self._get_plugin()
        if plugin is None:
            return False
        
        method = getattr(plugin, method_name, None)
        if method is None:
            return False
        
        try:
            result = method(*args)
            if inspect.isawaitable(result):
                result = await result
            return bool(result)
        except Exception as e:
            logger.warning(f"[RecallCancel] context_aware.{method_name} failed: {e}")
            return False
    
    async def remove_message(self, unified_msg_origin: str, message_id: str) -> bool:
        """Remove one user message from context_aware history."""
        result = await self._call_plugin_bool(
            "remove_message",
            unified_msg_origin,
            message_id,
        )
        if result:
            logger.debug(
                f"[RecallCancel] Removed recalled message from context_aware "
                f"(msg_id={message_id})"
            )
        return result
    
    async def remove_last_bot_response(self, unified_msg_origin: str) -> bool:
        """Remove the last recorded bot response from context_aware history."""
        result = await self._call_plugin_bool(
            "remove_last_bot_response",
            unified_msg_origin,
        )
        if result:
            logger.debug("[RecallCancel] Removed last bot response from context_aware")
        return result

    async def get_last_bot_response_marker(
        self,
        unified_msg_origin: str,
        response_preview: str | None = None,
        sender_id: str | None = None,
        min_timestamp: float | None = None,
    ) -> str | None:
        """Return the last context_aware Bot record ID if it matches the response."""
        plugin = self._get_plugin()
        if plugin is None:
            return None

        sessions = getattr(plugin, "_sessions", None)
        get_messages_list = getattr(sessions, "get_messages_list", None)
        if not callable(get_messages_list):
            return None

        try:
            messages = get_messages_list(unified_msg_origin)
        except Exception as e:
            logger.debug(f"[RecallCancel] Failed to inspect context_aware history: {e}")
            return None

        for message in reversed(messages):
            if not getattr(message, "is_bot", False):
                continue
            if response_preview is not None and getattr(message, "content", None) != response_preview:
                return None
            if sender_id is not None and getattr(message, "talking_to", None) != sender_id:
                return None
            if min_timestamp is not None:
                timestamp = getattr(message, "timestamp", None)
                try:
                    timestamp_value = float(timestamp)
                except (TypeError, ValueError):
                    return None
                if timestamp_value < min_timestamp:
                    return None
            marker = getattr(message, "msg_id", None)
            return str(marker) if marker else None
        return None

    async def remove_last_bot_response_if_matches(
        self,
        unified_msg_origin: str,
        response_preview: str,
        sender_id: str,
        context_bot_msg_id: str,
    ) -> bool:
        """Remove the last Bot response only if it is the expected record."""
        marker = await self.get_last_bot_response_marker(
            unified_msg_origin,
            response_preview,
            sender_id,
        )
        if marker != context_bot_msg_id:
            return False
        return await self.remove_last_bot_response(unified_msg_origin)


# ============================================================================
# Main Plugin
# ============================================================================


class Main(star.Star):
    """
    撤回取消回复插件
    
    当用户撤回消息时，自动取消正在处理的 LLM 回复。
    支持与 context_aware 插件联动，同步清理上下文记录。
    """
    
    def __init__(self, context: star.Context, config: Any | None = None) -> None:
        super().__init__(context)
        
        self._config = RecallCancelConfig.from_config(config)
        self._state = RecallStateManager()
        self._stats = PluginStats()
        self._context_aware = ContextAwareIntegration(context)
        self._cleanup_task: asyncio.Task | None = None
        
        logger.info(
            f"[RecallCancel] 插件 v{PLUGIN_VERSION} 已加载 | "
            f"bot_response_policy={self._config.remove_bot_response_policy}"
        )
    
    # -------------------------------------------------------------------------
    # 消息 ID 提取
    # -------------------------------------------------------------------------
    
    @staticmethod
    def _raw_get(raw: Any, key: str, default: Any = None) -> Any:
        if raw is None:
            return default
        if isinstance(raw, dict):
            return raw.get(key, default)
        
        value = getattr(raw, key, default)
        if value is not default:
            return value
        
        getter = getattr(raw, "get", None)
        if callable(getter):
            try:
                return getter(key, default)
            except TypeError:
                try:
                    return getter(key)
                except Exception:
                    return default
            except Exception:
                return default
        return default
    
    @staticmethod
    def _normalize_message_id(value: Any) -> str | None:
        if value is None:
            return None
        value = str(value).strip()
        return value or None
    
    @staticmethod
    def _is_uuid_like(value: str) -> bool:
        compact = value.replace("-", "")
        return len(compact) == 32 and all(
            ch in "0123456789abcdefABCDEF" for ch in compact
        )
    
    def _get_message_id(self, event: AstrMessageEvent) -> str | None:
        """Extract the original message ID from a regular or recall event."""
        try:
            raw = getattr(event.message_obj, "raw_message", None)
            raw_msg_id = self._normalize_message_id(self._raw_get(raw, "message_id"))
            if raw_msg_id:
                return raw_msg_id
            
            msg_id = self._normalize_message_id(
                getattr(event.message_obj, "message_id", None)
            )
            if msg_id and not self._is_uuid_like(msg_id):
                return msg_id
        except Exception as e:
            logger.debug(f"[RecallCancel] Failed to extract message_id: {e}")
        
        return None
    
    def _is_recall_event(self, event: AstrMessageEvent) -> tuple[bool, str | None, str | None]:
        """Return whether the event is a recall notice and its target message ID."""
        try:
            raw = getattr(event.message_obj, "raw_message", None)
            if not raw:
                return False, None, None
            
            post_type = self._raw_get(raw, "post_type")
            if post_type not in (None, "notice"):
                return False, None, None
            
            notice_type = self._raw_get(raw, "notice_type")
            if notice_type not in (NOTICE_GROUP_RECALL, NOTICE_FRIEND_RECALL):
                return False, None, None
            
            recalled_msg_id = self._normalize_message_id(
                self._raw_get(raw, "message_id")
            )
            operator_id = self._normalize_message_id(
                self._raw_get(raw, "operator_id") or self._raw_get(raw, "user_id")
            )
            if recalled_msg_id:
                return True, recalled_msg_id, operator_id
        except Exception as e:
            logger.debug(f"[RecallCancel] Failed to inspect recall event: {e}")
        
        return False, None, None
    
    @staticmethod
    def _request_stop_event(event: AstrMessageEvent | None) -> None:
        if event is None:
            return
        try:
            event.set_extra("agent_stop_requested", True)
        except Exception:
            pass
        event.stop_event()

    @staticmethod
    def _get_response_preview(resp: LLMResponse) -> str | None:
        text = getattr(resp, "completion_text", None)
        if text is None:
            return None
        text = str(text)
        return text[:200] if text else None
    
    async def _stop_pending_request(self, pending: PendingRequest | None) -> bool:
        if pending is None:
            return False
        
        self._request_stop_event(pending.event)
        await self._state.remove_pending_request(
            pending.message_id,
            pending.unified_msg_origin,
        )
        return True

    async def _remove_context_user_message(self, umo: str, msg_id: str) -> bool:
        if not self._config.context_aware_cleanup:
            return False
        recalled = await self._state.get_recalled_message(msg_id, umo)
        if recalled and recalled.context_user_removed:
            return False
        if await self._context_aware.remove_message(umo, msg_id):
            self._stats.context_aware_user_cleaned += 1
            await self._state.mark_context_user_removed(msg_id, umo)
            return True
        return False

    async def _remove_context_bot_response(
        self,
        umo: str,
        msg_id: str,
        pending: PendingRequest | None = None,
    ) -> bool:
        if not self._config.context_aware_cleanup:
            return False
        policy = self._config.remove_bot_response_policy
        if policy == BOT_RESPONSE_POLICY_OFF:
            return False
        recalled = await self._state.get_recalled_message(msg_id, umo)
        if recalled and recalled.context_bot_removed:
            return False
        if policy == BOT_RESPONSE_POLICY_SAFE:
            if (
                pending is None
                or not pending.llm_response_preview
                or pending.llm_response_sequence <= 0
            ):
                return False
            if not await self._state.is_latest_response_sequence(
                umo,
                pending.llm_response_sequence,
            ):
                return False
            context_bot_msg_id = pending.context_bot_msg_id
            if context_bot_msg_id is None:
                context_bot_msg_id = await self._context_aware.get_last_bot_response_marker(
                    umo,
                    pending.llm_response_preview,
                    pending.sender_id,
                    min_timestamp=pending.llm_response_started_at,
                )
            if context_bot_msg_id is None:
                return False
            removed = await self._context_aware.remove_last_bot_response_if_matches(
                umo,
                pending.llm_response_preview,
                pending.sender_id,
                context_bot_msg_id,
            )
        else:
            removed = await self._context_aware.remove_last_bot_response(umo)
        if removed:
            self._stats.context_aware_bot_cleaned += 1
            await self._state.mark_context_bot_removed(msg_id, umo)
            return True
        return False

    async def _deferred_safe_context_bot_cleanup(
        self,
        umo: str,
        msg_id: str,
        response_preview: str,
        sender_id: str,
        response_started_at: float | None,
        response_sequence: int,
    ) -> None:
        """Handle recalls that stop the LLM hook before the low-priority marker runs."""
        for delay in (0, 0.05, 0.2):
            await asyncio.sleep(delay)
            recalled = await self._state.get_recalled_message(msg_id, umo)
            if not recalled or recalled.context_bot_removed:
                return
            if not await self._state.is_latest_response_sequence(
                umo,
                response_sequence,
            ):
                return

            marker = await self._context_aware.get_last_bot_response_marker(
                umo,
                response_preview,
                sender_id,
                min_timestamp=response_started_at,
            )
            if marker is None:
                continue

            if await self._context_aware.remove_last_bot_response_if_matches(
                umo,
                response_preview,
                sender_id,
                marker,
            ):
                self._stats.context_aware_bot_cleaned += 1
                await self._state.mark_context_bot_removed(msg_id, umo)
            return

    async def _schedule_deferred_safe_context_bot_cleanup(
        self,
        umo: str,
        msg_id: str,
        pending: PendingRequest | None,
    ) -> None:
        if (
            self._config.remove_bot_response_policy != BOT_RESPONSE_POLICY_SAFE
            or not self._config.context_aware_cleanup
            or pending is None
            or not pending.llm_response_preview
            or pending.llm_response_sequence <= 0
            or pending.context_bot_msg_id
        ):
            return

        asyncio.create_task(
            self._deferred_safe_context_bot_cleanup(
                umo,
                msg_id,
                pending.llm_response_preview,
                pending.sender_id,
                pending.llm_response_started_at,
                pending.llm_response_sequence,
            )
        )
    
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.ALL, priority=100)
    async def on_all_message(self, event: AstrMessageEvent) -> None:
        """Listen to all aiocqhttp events and intercept recall notices."""
        is_recall, recalled_msg_id, operator_id = self._is_recall_event(event)
        if not is_recall or not recalled_msg_id:
            return
        
        self._stats.recalls_detected += 1
        umo = event.unified_msg_origin
        logger.info(
            f"[RecallCancel] Recall detected | msg_id={recalled_msg_id} | "
            f"operator={operator_id} | umo={umo}"
        )
        
        await self._state.add_recalled_message(
            message_id=recalled_msg_id,
            unified_msg_origin=umo,
            operator_id=operator_id or "",
        )
        
        pending = await self._state.get_pending_request(recalled_msg_id, umo)
        if pending:
            logger.info(
                f"[RecallCancel] Pending LLM request found, stopping it | "
                f"msg_id={recalled_msg_id}"
            )
            await self._stop_pending_request(pending)
            self._stats.llm_requests_blocked += 1
        
        await self._remove_context_user_message(umo, recalled_msg_id)

        policy = self._config.remove_bot_response_policy
        if policy == BOT_RESPONSE_POLICY_LEGACY_LAST:
            await self._remove_context_bot_response(umo, recalled_msg_id)
        elif policy == BOT_RESPONSE_POLICY_SAFE:
            removed = await self._remove_context_bot_response(
                umo,
                recalled_msg_id,
                pending,
            )
            if not removed:
                await self._schedule_deferred_safe_context_bot_cleanup(
                    umo,
                    recalled_msg_id,
                    pending,
                )

        logger.debug("[RecallCancel] Recall notice propagation left open")
    
    @filter.on_llm_request(priority=100)  # run early
    async def on_llm_request(
        self, event: AstrMessageEvent, req: ProviderRequest
    ) -> None:
        """Block the LLM request if the source message was already recalled."""
        msg_id = self._get_message_id(event)
        if not msg_id:
            return
        
        umo = event.unified_msg_origin
        sender_id = event.get_sender_id()
        await self._state.add_pending_request(
            message_id=msg_id,
            unified_msg_origin=umo,
            sender_id=sender_id,
            event=event,
        )
        
        if await self._state.is_recalled(msg_id, umo):
            logger.info(
                f"[RecallCancel] Blocked LLM request for recalled message | "
                f"msg_id={msg_id}"
            )
            self._request_stop_event(event)
            await self._state.remove_pending_request(msg_id, umo)
            self._stats.llm_requests_blocked += 1
            return
        
        logger.debug(f"[RecallCancel] Tracked LLM request | msg_id={msg_id}")
    
    @filter.on_llm_response(priority=100)  # run early
    async def on_llm_response(
        self, event: AstrMessageEvent, resp: LLMResponse
    ) -> None:
        """Block the final LLM response early if the message was recalled."""
        msg_id = self._get_message_id(event)
        if not msg_id:
            return
        umo = event.unified_msg_origin
        
        if await self._state.is_recalled(msg_id, umo):
            logger.info(
                f"[RecallCancel] Blocked LLM response for recalled message | "
                f"msg_id={msg_id}"
            )
            self._request_stop_event(event)
            await self._state.remove_pending_request(msg_id, umo)
            self._stats.llm_responses_blocked += 1
            if self._config.remove_bot_response_policy == BOT_RESPONSE_POLICY_LEGACY_LAST:
                await self._remove_context_bot_response(umo, msg_id)
            return

        response_preview = self._get_response_preview(resp)
        if response_preview:
            await self._state.mark_pending_response_seen(
                msg_id,
                umo,
                response_preview,
                context_bot_msg_id=None,
                response_started_at=time.time(),
            )

    @filter.on_llm_response(priority=-100)  # run after context_aware default handlers
    async def on_llm_response_recorded(
        self, event: AstrMessageEvent, resp: LLMResponse
    ) -> None:
        """Mark that context_aware has had a chance to record the Bot response."""
        msg_id = self._get_message_id(event)
        if not msg_id:
            return
        umo = event.unified_msg_origin
        if await self._state.is_recalled(msg_id, umo):
            return
        response_preview = self._get_response_preview(resp)
        if not response_preview:
            return
        sender_id = event.get_sender_id()
        pending = await self._state.get_pending_request(msg_id, umo)
        if pending is None:
            return
        marker = await self._context_aware.get_last_bot_response_marker(
            umo,
            response_preview,
            sender_id,
            min_timestamp=pending.llm_response_started_at,
        )
        if marker is None:
            return
        await self._state.mark_pending_response_seen(
            msg_id,
            umo,
            response_preview,
            context_bot_msg_id=marker,
        )

    @filter.on_decorating_result(priority=100)  # run early
    async def on_decorating_result(self, event: AstrMessageEvent) -> None:
        """Do one last check right before the message is sent."""
        msg_id = self._get_message_id(event)
        if not msg_id:
            return
        umo = event.unified_msg_origin
        
        if await self._state.is_recalled(msg_id, umo):
            pending = await self._state.get_pending_request(msg_id, umo)
            logger.info(
                f"[RecallCancel] Blocked send stage for recalled message | "
                f"msg_id={msg_id}"
            )
            self._request_stop_event(event)
            await self._state.remove_pending_request(msg_id, umo)
            self._stats.send_blocked += 1

            policy = self._config.remove_bot_response_policy
            if policy == BOT_RESPONSE_POLICY_LEGACY_LAST:
                await self._remove_context_bot_response(umo, msg_id)
            elif policy == BOT_RESPONSE_POLICY_SAFE:
                await self._remove_context_bot_response(umo, msg_id, pending)
    
    @filter.after_message_sent(priority=100)
    async def after_message_sent(self, event: AstrMessageEvent) -> None:
        """消息发送后清理待处理记录"""
        msg_id = self._get_message_id(event)
        if not msg_id:
            return
        
        # 移除待处理记录
        await self._state.remove_pending_request(msg_id, event.unified_msg_origin)
        logger.debug(f"[RecallCancel] 消息已发送，清理记录 | 消息ID: {msg_id}")
    
    # -------------------------------------------------------------------------
    # Background Cleanup
    # -------------------------------------------------------------------------
    
    @filter.on_astrbot_loaded()
    async def on_loaded(self, *args: Any, **kwargs: Any) -> None:
        """AstrBot 加载完成后启动清理任务"""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            logger.debug("[RecallCancel] 后台清理任务已启动")
    
    async def _cleanup_loop(self) -> None:
        """定期清理过期记录"""
        while True:
            try:
                await asyncio.sleep(self._config.cleanup_interval)
                cleaned = await self._state.cleanup_expired(
                    self._config.record_expire_seconds
                )
                if cleaned > 0:
                    pending, recalled = await self._state.get_stats()
                    logger.debug(
                        f"[RecallCancel] 已清理 {cleaned} 条过期记录 | "
                        f"当前: 待处理 {pending}, 已撤回 {recalled}"
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[RecallCancel] 清理任务出错: {e}")
    
    # -------------------------------------------------------------------------
    # Stats Command
    # -------------------------------------------------------------------------
    
    @filter.command("recall_stats")
    async def stats_command(self, event: AstrMessageEvent) -> None:
        """显示撤回取消插件统计信息"""
        pending, recalled = await self._state.get_stats()
        
        stats_text = (
            "📊 撤回取消插件统计\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"检测撤回: {self._stats.recalls_detected} 次\n"
            f"阻止请求: {self._stats.llm_requests_blocked} 次\n"
            f"阻止响应: {self._stats.llm_responses_blocked} 次\n"
            f"阻止发送: {self._stats.send_blocked} 次\n"
            f"清理用户上下文: {self._stats.context_aware_user_cleaned} 次\n"
            f"清理Bot上下文: {self._stats.context_aware_bot_cleaned} 次\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Bot回复清理策略: {self._config.remove_bot_response_policy}\n"
            f"上下文联动: {'开启' if self._config.context_aware_cleanup else '关闭'}\n"
            f"当前待处理: {pending} 条\n"
            f"当前撤回记录: {recalled} 条"
        )
        
        await event.send(MessageChain([Plain(stats_text)]))
    
    # -------------------------------------------------------------------------
    # Cleanup
    # -------------------------------------------------------------------------
    
    async def terminate(self) -> None:
        """清理资源"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        
        logger.info(
            f"[RecallCancel] 插件已终止 | "
            f"统计: 检测撤回 {self._stats.recalls_detected}, "
            f"阻止请求 {self._stats.llm_requests_blocked}, "
            f"阻止响应 {self._stats.llm_responses_blocked}, "
            f"阻止发送 {self._stats.send_blocked}, "
            f"清理用户上下文 {self._stats.context_aware_user_cleaned}, "
            f"清理Bot上下文 {self._stats.context_aware_bot_cleaned}"
        )
