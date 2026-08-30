"""AstrBot 图片上下文缓存池插件。"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import mimetypes
import os
import re
import shutil
import time
import urllib.parse
import urllib.request
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astrbot import logger
from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Reply
from astrbot.api.provider import ProviderRequest
from astrbot.core.agent.message import TextPart

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path
except Exception:
    def get_astrbot_plugin_data_path() -> str:
        return os.path.join(os.getcwd(), "plugin_data")


@dataclass(slots=True)
class CachedImage:
    image_id: str
    msg_id: str
    local_path: str
    original_ref: str
    sender_name: str
    timestamp: float
    description: str = ""


class Main(star.Star):
    """只负责图片缓存和后续文字请求的图片回放。"""

    _KV_POOL_KEY = "image_context_pool_entries"

    def __init__(self, context: star.Context, config: Any | None = None) -> None:
        super().__init__(context)
        self._context = context
        self._config = config
        self._enabled = self._cfg_bool("enable", True)
        self._pool_size = max(1, self._cfg_int("pool_size", 30))
        self._pool_ttl = max(60, self._cfg_int("pool_ttl", 1800))
        self._description_first = self._cfg_bool("description_first", True)
        self._max_bytes = max(1024 * 1024, self._cfg_int("max_bytes", 50 * 1024 * 1024))
        self._download_timeout = max(5, min(120, self._cfg_int("download_timeout", 15)))
        default_dir = os.path.join(
            get_astrbot_plugin_data_path(),
            "astrbot_plugin_image_context_pool",
            "cached_images",
        )
        self._cache_dir = os.path.expanduser(
            str(self._cfg("cache_dir", default_dir) or default_dir)
        )
        try:
            os.makedirs(self._cache_dir, exist_ok=True)
        except Exception as exc:
            logger.error(f"[ImageContextPool] 无法创建缓存目录: {exc}")
            self._cache_dir = ""
        self._pool: dict[str, deque[CachedImage]] = {}
        self._download_locks: dict[str, asyncio.Lock] = {}
        self._persist_lock = asyncio.Lock()
        self._reference_pattern = re.compile(
            r"(这个|这张|那张|上面|刚刚|图片|照片|原图|解析|标注|解算|看看|看下|看一下|星空|图|表情包|贴纸|反应图|meme|emoji|astrometry|annotat)",
            re.IGNORECASE,
        )
        self._original_request_pattern = re.compile(
            r"(重新|再发|原图|看清|细节|放大|像素|识别|测量|定位|解析|标注|解算|astrometry|annotat)",
            re.IGNORECASE,
        )
        self._all_images_pattern = re.compile(
            r"(全部|所有|都标|都解析|逐张|每张|这些|这几张|前面几张|最近几张|all)",
            re.IGNORECASE,
        )
        logger.info(
            f"[ImageContextPool] 已加载 | 启用: {self._enabled} | "
            f"每会话 {self._pool_size} 张 | TTL {self._pool_ttl}s"
        )

    async def initialize(self) -> None:
        """恢复尚未过期的图片索引，让重启后仍可引用最近图片。

        图片本身仍以文件形式保存在 plugin_data 中；KV 只保存图片 ID、描述
        和稳定副本路径。文件被清理后会在加载时自动丢弃对应索引。
        """
        try:
            data = await self.get_kv_data(self._KV_POOL_KEY, {})
            if isinstance(data, dict):
                for session_id, raw_entries in data.items():
                    if not isinstance(session_id, str) or not isinstance(raw_entries, list):
                        continue
                    restored: deque[CachedImage] = deque(maxlen=self._pool_size)
                    for raw in raw_entries:
                        if not isinstance(raw, dict):
                            continue
                        try:
                            entry = CachedImage(
                                image_id=str(raw.get("image_id", "")),
                                msg_id=str(raw.get("message_id", "")),
                                local_path=str(raw.get("local_path", "")),
                                original_ref=str(raw.get("original_ref", "")),
                                sender_name=str(raw.get("sender_name", "")),
                                timestamp=float(raw.get("timestamp", 0)),
                                description=str(raw.get("description", "")),
                            )
                        except (TypeError, ValueError):
                            continue
                        if entry.image_id and entry.local_path:
                            restored.append(entry)
                    if restored:
                        self._pool[session_id] = restored
            before = sum(len(entries) for entries in self._pool.values())
            self._prune()
            after = sum(len(entries) for entries in self._pool.values())
            if before != after:
                await self._persist()
            logger.info(
                f"[ImageContextPool] 已恢复 {after} 张持久化图片索引"
            )
        except Exception as exc:
            logger.warning(f"[ImageContextPool] 恢复持久化索引失败: {exc}")

    async def _persist(self) -> None:
        """持久化图片索引，不写入图片二进制内容。"""
        data = {
            session_id: [
                {
                    "image_id": entry.image_id,
                    "message_id": entry.msg_id,
                    "local_path": entry.local_path,
                    "original_ref": entry.original_ref,
                    "sender_name": entry.sender_name,
                    "timestamp": entry.timestamp,
                    "description": entry.description,
                }
                for entry in entries
            ]
            for session_id, entries in self._pool.items()
            if entries
        }
        async with self._persist_lock:
            await self.put_kv_data(self._KV_POOL_KEY, data)

    def _cfg(self, key: str, default: Any = None) -> Any:
        if self._config is None:
            return default
        try:
            return self._config.get(key, default)
        except Exception:
            return default

    def _cfg_bool(self, key: str, default: bool) -> bool:
        value = self._cfg(key, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on", "是"}

    def _cfg_int(self, key: str, default: int) -> int:
        try:
            return int(self._cfg(key, default))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _iter_images(components: Any):
        for component in components or []:
            if isinstance(component, Image):
                yield component
            elif isinstance(component, Reply):
                yield from Main._iter_images(getattr(component, "chain", None) or [])

    @staticmethod
    def _set_cached_image_ref(component: Image, local_path: str) -> None:
        path = str(Path(local_path).resolve(strict=False))
        replacements = {"file": Path(path).as_uri(), "url": "", "path": path}
        for attr, value in replacements.items():
            if not hasattr(component, attr):
                continue
            try:
                setattr(component, attr, value)
            except Exception:
                pass

    def _lookup_cached_ref(self, session_id: str, ref: str) -> str | None:
        """引用图片的临时路径失效时，按原始引用找回稳定副本。"""
        pool = self._pool.get(session_id)
        if not pool or not ref:
            return None
        ref_name = os.path.basename(urllib.parse.unquote(ref.removeprefix("file://")))
        for entry in reversed(pool):
            if not os.path.isfile(entry.local_path):
                continue
            original = entry.original_ref
            original_name = os.path.basename(urllib.parse.unquote(original.removeprefix("file://")))
            if original == ref or (ref_name and original_name and ref_name == original_name):
                return entry.local_path
        return None

    @staticmethod
    def _image_ref(component: Image) -> str:
        for attr in ("url", "file", "path"):
            try:
                value = getattr(component, attr, None)
            except Exception:
                value = None
            if value:
                return str(value)
        return ""

    @staticmethod
    def _extension(ref: str, content_type: str = "") -> str:
        path = urllib.parse.urlparse(ref).path.lower()
        ext = os.path.splitext(path)[1]
        if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"}:
            return ext
        guessed = mimetypes.guess_extension(content_type.split(";", 1)[0].strip())
        return guessed or ".jpg"

    def _write_bytes(self, raw: bytes, ref: str, content_type: str = "") -> str | None:
        if not self._cache_dir or not raw or len(raw) > self._max_bytes:
            return None
        digest = hashlib.sha256(ref.encode("utf-8", "ignore") + b"\0" + raw[:64]).hexdigest()[:32]
        target = Path(self._cache_dir) / f"image-pool-{digest}{self._extension(ref, content_type)}"
        if target.is_file() and target.stat().st_size <= self._max_bytes:
            return str(target)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
        try:
            temporary.write_bytes(raw)
            os.replace(temporary, target)
            return str(target)
        except Exception as exc:
            try:
                temporary.unlink(missing_ok=True)
            except Exception:
                pass
            logger.warning(f"[ImageContextPool] 写入图片缓存失败: {exc}")
            return None

    def _download_sync(self, ref: str) -> str | None:
        if not ref:
            return None
        if ref.startswith("data:"):
            try:
                header, payload = ref.split(",", 1)
                raw = base64.b64decode(payload, validate=False)
                return self._write_bytes(raw, ref, header.removeprefix("data:"))
            except Exception:
                return None
        if ref.startswith("base64://"):
            try:
                raw = base64.b64decode(ref.removeprefix("base64://"), validate=False)
                return self._write_bytes(raw, ref)
            except Exception:
                return None
        local_ref = ref
        if ref.startswith("file://"):
            local_ref = urllib.parse.unquote(ref.removeprefix("file://"))
        if not ref.startswith(("http://", "https://")):
            if not self._cache_dir or not os.path.isfile(local_ref):
                return None
            try:
                if os.path.getsize(local_ref) > self._max_bytes:
                    return None
                source = Path(local_ref)
                target = Path(self._cache_dir) / f"image-pool-{hashlib.sha256(str(source).encode()).hexdigest()[:32]}{source.suffix or '.jpg'}"
                if not target.exists():
                    shutil.copy2(source, target)
                return str(target)
            except Exception:
                return None
        temporary_ref = f"{ref}\0{self._max_bytes}"
        request = urllib.request.Request(ref, headers={"User-Agent": "AstrBot-ImageContextPool/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=self._download_timeout) as response:
                content_type = str(response.headers.get("Content-Type", ""))
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > self._max_bytes:
                        return None
                    chunks.append(chunk)
            return self._write_bytes(b"".join(chunks), temporary_ref, content_type)
        except Exception as exc:
            logger.debug(f"[ImageContextPool] 图片下载失败: {exc}")
            return None

    async def _cache_image(self, ref: str) -> str | None:
        if not ref:
            return None
        lock = self._download_locks.setdefault(ref, asyncio.Lock())
        async with lock:
            try:
                result = await asyncio.to_thread(self._download_sync, ref)
            finally:
                self._download_locks.pop(ref, None)
            return result

    def _prune(self, session_id: str | None = None) -> None:
        now = time.time()
        session_ids = [session_id] if session_id else list(self._pool)
        for sid in session_ids:
            pool = self._pool.get(sid)
            if not pool:
                continue
            kept = deque(
                (entry for entry in pool if now - entry.timestamp <= self._pool_ttl and os.path.isfile(entry.local_path)),
                maxlen=self._pool_size,
            )
            if kept:
                self._pool[sid] = kept
            else:
                self._pool.pop(sid, None)

    def _capture_descriptions(self, session_id: str, req: ProviderRequest) -> int:
        """从 ContextAware 已生成的场景块提取首次 VLM 描述并绑定图片 ID。"""
        pool = self._pool.get(session_id)
        if not pool:
            return 0
        blocks: list[tuple[str, str]] = []
        for part in getattr(req, "extra_user_content_parts", None) or []:
            text = getattr(part, "text", None)
            if not isinstance(text, str):
                continue
            for match in re.finditer(r"<image\b([^>]*)>(.*?)</image>", text, re.IGNORECASE | re.DOTALL):
                attrs, body = match.groups()
                sender_match = re.search(r"sender=\"([^\"]*)\"", attrs)
                sender = html.unescape(sender_match.group(1)) if sender_match else ""
                clean_body = html.unescape(re.sub(r"<[^>]+>", "", body)).strip()
                # 一条消息可能包含多张图片。SceneGenerator 会把它们压进同一个
                # `<image count="N">` 节点，因此必须按 `[图片: ...]` 标记拆开，
                # 不能把整段内容错误地绑定到第一张图。
                captions = [
                    item.strip()
                    for item in re.findall(
                        r"\[(?:图片|表情包)\s*[:：]\s*(.+?)\]",
                        clean_body,
                    )
                    if item.strip()
                ]
                if captions:
                    blocks.extend((sender, caption[:160]) for caption in captions)
                    continue
                if clean_body and clean_body not in {"[图片]", "图片"}:
                    blocks.append((sender, clean_body[:160]))
        bound = 0
        for sender, caption in blocks:
            candidates = [entry for entry in pool if not entry.description]
            if sender:
                same_sender = [entry for entry in candidates if entry.sender_name == sender]
                candidates = same_sender or candidates
            if candidates:
                candidates[0].description = caption
                bound += 1
        if blocks:
            logger.debug(f"[ImageContextPool] 已记录 {len(blocks)} 条首次 VLM 描述")
        return bound

    def _append_index(self, req: ProviderRequest, entries: list[CachedImage]) -> None:
        existing_parts = getattr(req, "extra_user_content_parts", None) or []
        if any("<image_cache_index>" in str(getattr(part, "text", "")) for part in existing_parts):
            return
        lines = ["<image_cache_index>"]
        for entry in reversed(entries):
            description = entry.description or "尚未记录首次 VLM 描述"
            lines.append(
                f'  <image id="{entry.image_id}" sender="{html.escape(entry.sender_name)}">'
                f"首次视觉描述：{html.escape(description)}</image>"
            )
        lines.append(
            "  <instruction>引用图片时优先使用 image id 和首次视觉描述；只有明确要求原图或需要像素级细节时才重新查看原图。</instruction>"
        )
        lines.append("</image_cache_index>")
        try:
            parts = getattr(req, "extra_user_content_parts", None)
            if not isinstance(parts, list):
                return
            parts.append(TextPart(text="\n".join(lines)).mark_as_temp())
        except Exception as exc:
            logger.debug(f"[ImageContextPool] 图片索引注入失败: {exc}")

    def _needs_original(self, text: str) -> bool:
        return bool(self._original_request_pattern.search(text))

    def _repair_request_image_refs(self, session_id: str, req: ProviderRequest) -> int:
        """把 req 中已经失效的临时图片路径替换为缓存池稳定副本。"""
        repaired = 0

        def repair_ref(value: Any) -> str | None:
            if not isinstance(value, str) or not value:
                return None
            if value.startswith(("http://", "https://", "data:", "base64://")):
                return None
            local = urllib.parse.unquote(value.removeprefix("file://"))
            if os.path.isfile(local):
                return None
            cached = self._lookup_cached_ref(session_id, value)
            return cached if cached and cached != value else None

        image_urls = getattr(req, "image_urls", None)
        if isinstance(image_urls, list):
            for index, value in enumerate(image_urls):
                cached = repair_ref(value)
                if cached:
                    image_urls[index] = cached
                    repaired += 1

        contexts = getattr(req, "contexts", None)
        if isinstance(contexts, list):
            for context in contexts:
                if not isinstance(context, dict):
                    continue
                content = context.get("content")
                if not isinstance(content, list):
                    continue
                for part in content:
                    if not isinstance(part, dict) or part.get("type") != "image_url":
                        continue
                    image_part = part.get("image_url")
                    if isinstance(image_part, dict):
                        value = image_part.get("url")
                        cached = repair_ref(value)
                        if cached:
                            image_part["url"] = cached
                            repaired += 1
                    elif isinstance(image_part, str):
                        cached = repair_ref(image_part)
                        if cached:
                            part["image_url"] = cached
                            repaired += 1
        if repaired:
            logger.info(f"[ImageContextPool] 已修复 {repaired} 个失效图片引用")
        return repaired

    @staticmethod
    def _is_reset(text: str) -> bool:
        return bool(re.match(r"^/(?:reset|new)(?:\s|$)", text.strip(), re.IGNORECASE))

    @filter.platform_adapter_type(filter.PlatformAdapterType.ALL)
    async def on_message(self, event: AstrMessageEvent, *args: Any, **kwargs: Any) -> None:
        if not self._enabled:
            return
        try:
            text = str(event.get_message_str() or "")
            session_id = event.unified_msg_origin
            if self._is_reset(text):
                self._pool.pop(session_id, None)
                await self._persist()
                return
            components = event.get_messages()
            images = list(self._iter_images(components))
            if not images:
                return
            self._prune(session_id)
            pool = self._pool.setdefault(session_id, deque(maxlen=self._pool_size))
            sender_name = str(event.get_sender_name() or event.get_sender_id() or "")
            msg_id = str(getattr(getattr(event, "message_obj", None), "message_id", ""))
            for component in images:
                ref = self._image_ref(component)
                local_path = self._lookup_cached_ref(session_id, ref)
                if not local_path:
                    local_path = await self._cache_image(ref)
                if not local_path:
                    continue
                # 让后续命令（包括 /解析 和引用消息）使用稳定副本，
                # 不再回查已被 AstrBot 清理的 data/temp/media_image 路径。
                self._set_cached_image_ref(component, local_path)
                duplicate = next((entry for entry in pool if entry.local_path == local_path), None)
                if duplicate:
                    duplicate.timestamp = time.time()
                    continue
                image_id = f"img-{int(time.time() * 1000):x}-{uuid.uuid4().hex[:6]}"
                pool.append(
                    CachedImage(
                        image_id, msg_id, local_path, ref, sender_name, time.time()
                    )
                )
            self._prune(session_id)
            await self._persist()
            count = len(self._pool.get(session_id, ()))
            if count:
                logger.info(f"[ImageContextPool] 图片缓存池写入: {count} 张")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"[ImageContextPool] 记录图片失败: {exc}")

    @filter.on_llm_request(priority=-20)
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        if not self._enabled:
            return
        try:
            components = event.get_messages()
            session_id = event.unified_msg_origin
            self._prune(session_id)
            self._repair_request_image_refs(session_id, req)
            is_image_event = any(isinstance(component, Image) for component in self._iter_images(components))
            text = str(event.get_message_str() or "").strip()
            is_reference = bool(text and self._reference_pattern.search(text))
            if not is_image_event and not is_reference:
                return
            pool = self._pool.get(session_id)
            logger.info(
                f"[ImageContextPool] 请求检查 | 当前图片={'是' if is_image_event else '否'} | "
                f"指代={'是' if is_reference else '否'} | "
                f"缓存={len(pool or ())} | "
                f"已有描述={sum(1 for entry in (pool or ()) if entry.description)}"
            )
            if not pool:
                return

            # ContextAware 的 on_llm_request(priority=-10) 已经先完成首次 VLM 描述；
            # 本插件在 -20 读取场景块，为图片缓存条目绑定描述和 ID。
            if self._capture_descriptions(session_id, req):
                await self._persist()
            entries = list(pool)
            self._append_index(req, entries)

            if is_image_event or not is_reference:
                return
            if list(getattr(req, "image_urls", None) or []):
                return

            newest = entries[-1]
            if (
                self._description_first
                and newest.description
                and not self._needs_original(text)
            ):
                try:
                    event.set_extra("image_context_pool_description_replay", newest.image_id)
                except Exception:
                    pass
                logger.info(
                    f"[ImageContextPool] 使用图片 ID {newest.image_id} 的首次描述回放，未重复发送原图"
                )
                return

            refs = [entry.local_path for entry in reversed(entries) if os.path.isfile(entry.local_path)]
            if not refs:
                return
            # 纯文字“标注一下/解析一下”默认指向最近一张图，避免把整个
            # 30 张缓存窗口都提交给 Astrometry；明确说“全部/这几张”时
            # 才回放多张。
            replay_limit = self._pool_size if self._all_images_pattern.search(text) else 1
            req.image_urls = refs[:replay_limit]
            try:
                event.set_extra("image_context_pool_replayed_images", len(req.image_urls))
            except Exception:
                pass
            logger.info(f"[ImageContextPool] 历史图片原图回放: {len(req.image_urls)} 张")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"[ImageContextPool] 回放图片失败: {exc}")

    async def terminate(self) -> None:
        self._pool.clear()
        self._download_locks.clear()
        logger.info("[ImageContextPool] 已停止")
