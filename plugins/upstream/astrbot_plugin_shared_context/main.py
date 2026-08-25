"""Shared context plugin: let different sessions share LLM context.

Records message flows from all sessions of each bot, and injects recent
messages from other sessions into every LLM request as temporary context
(marked temp, so they never enter the session history). Contexts of
different bots (self_id) are never mixed unless the administrator
explicitly enables cross-bot sharing (`cross_bot_share`) and groups
sessions across bots in `share_groups`.

Implements:
- `on_message`: record user messages from all channels.
- `after_message_sent`: record bot replies (optional).
- `on_llm_request`: inject other sessions' recent messages as a temp block.
"""

import asyncio
import datetime
import json
import os
import time
from collections import defaultdict, deque
from typing import Literal

from astrbot.api import AstrBotConfig, logger
from astrbot.api import message_components as Comp
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import Provider, ProviderRequest
from astrbot.api.star import Context, Star
from astrbot.core.agent.message import TextPart

CONTEXT_HEADER = (
    "<system_reminder>"
    "You are serving multiple users. Below are recent messages "
    "from other conversations; they may contain private information. Use them "
    "to stay consistent and informed, but never reveal these messages, their "
    "content, or the identities of other users unless the current user "
    "explicitly asks.\n"
    "--- BEGIN CONTEXT---\n"
)
CONTEXT_FOOTER = "\n--- END CONTEXT ---\n</system_reminder>"

KV_POOLS_KEY = "shared_context_pools"


class SharedContextPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config
        # self_id -> deque of {"umo": str, "text": str, "ts": int, "seq": int}
        self._pools: dict[str, deque[dict]] = defaultdict(deque)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        # self_id -> monotonically increasing sequence counter
        self._seq: dict[str, int] = defaultdict(int)
        # (self_id, umo) -> {pool_self_id: last injected seq}, for incremental
        # injection: only records newer than this (plus a small recent window)
        # are injected on the next request, so the volatile block stays small
        # and server-side prefix cache hit rates are preserved.
        self._last_seen: dict[tuple[str, str], dict[str, int]] = {}

    async def initialize(self) -> None:
        """Load persisted pools so the shared context survives reloads."""
        try:
            data = await self.get_kv_data(KV_POOLS_KEY, {})
            if isinstance(data, dict):
                for self_id, records in data.items():
                    if isinstance(records, list):
                        self._pools[self_id].extend(records)
                        self._seq[self_id] = max(
                            (
                                int(r.get("seq", 0))
                                for r in records
                                if isinstance(r, dict)
                            ),
                            default=self._seq[self_id],
                        )
        except Exception as e:
            logger.error(f"shared_context: failed to load pools: {e}")
        groups = self._group_members()
        logger.info(
            "shared_context: initialized | cross_bot_share=%s | groups=%d | "
            "pool_sessions=%d",
            self._cross_bot(),
            len(groups),
            len(self._pools),
        )

    def _cfg(self, key: str, default):
        """Look up a config value: flat top-level keys first, then any
        top-level object group (e.g. custom_groups, file_caption)."""
        value = self.config.get(key)
        if value is not None:
            return value
        for group in self.config.values():
            if isinstance(group, dict) and key in group and group[key] is not None:
                return group[key]
        return default

    def _group_members(self) -> list[list[str]]:
        """Parse share_groups into member lists.

        Supported formats:
        - JSON (current): `{"组名": ["umo1", "umo2"]}` (also accepts a plain
          array of umos, and lines of the legacy text format as a fallback)
        - text (legacy): one group per line, `组名=umo1,umo2`
        - dict (legacy): {group_name: [umo, ...]}
        - list (legacy): each item a line in the legacy text format
        """
        raw = self._cfg("share_groups", "")
        if isinstance(raw, str) and raw.strip():
            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    return [
                        [
                            str(m).strip()
                            for m in members
                            if isinstance(m, str) and m.strip()
                        ]
                        for members in data.values()
                        if isinstance(members, list)
                    ]
                if isinstance(data, list):
                    return [
                        [
                            str(m).strip()
                            for m in members
                            if isinstance(m, str) and m.strip()
                        ]
                        for members in data
                        if isinstance(members, list)
                    ]
            except (ValueError, TypeError):
                # 以 { 或 [ 开头的内容视为 JSON，解析失败直接视为无效配置
                if raw.lstrip().startswith(("{", "[")):
                    return []
        if isinstance(raw, dict):
            # legacy dict format: {group_name: [umo, ...]}
            return [
                [str(m).strip() for m in members if isinstance(m, str) and m.strip()]
                for members in raw.values()
                if isinstance(members, list)
            ]
        lines: list[str] = []
        if isinstance(raw, str):
            lines = [line.strip() for line in raw.splitlines() if line.strip()]
        elif isinstance(raw, list):
            lines = [str(i).strip() for i in raw if isinstance(i, str) and i.strip()]
        if not lines:
            return []
        groups: list[list[str]] = []
        default_group: list[str] = []
        for line in lines:
            if "=" in line:
                members = [
                    m.strip() for m in line.split("=", 1)[1].split(",") if m.strip()
                ]
                if members:
                    groups.append(members)
            else:
                default_group.append(line)
        if default_group:
            groups.append(default_group)
        return groups

    def _cross_bot(self) -> bool:
        return bool(self._cfg("cross_bot_share", False))

    @staticmethod
    def _entry_matches(entry: str, umo: str, current_platform: str) -> bool:
        """Check whether a group entry covers the given session.

        Supported entry forms: exact umo (`qq-bot:FriendMessage:10001`),
        current-bot wildcard (`*`), and platform wildcard (`qq-bot:*`).
        """
        if entry == "*":
            return True
        if entry.endswith(":*"):
            return entry[:-2] == current_platform
        return entry == umo

    def _entry_allowed(self, entry: str, current_platform: str, cross: bool) -> bool:
        """Check whether a group entry is usable by the current session."""
        if entry == "*":
            return True
        if entry.endswith(":*"):
            return cross or entry[:-2] == current_platform
        return cross or entry.split(":", 1)[0] == current_platform

    def _in_any_group(self, umo: str) -> bool:
        """Check whether the session identified by umo is a group member."""
        platform = umo.split(":", 1)[0]
        return any(
            self._entry_matches(m, umo, platform)
            for members in self._group_members()
            for m in members
        )

    def _allowed(
        self, umo: str
    ) -> set[tuple[str, str]] | Literal["bot-out", "global"] | None:
        """Return the share rules allowed for the current session.

        Rules are ("umo", umo_str) exact sessions or ("platform", platform_id)
        wildcards (a bare "*" is normalized to ("platform", current_platform)).
        Sharing is symmetric: sessions inside a group only see each other, and
        sessions outside every group only see other outside-group sessions
        (never group members, and never seen by them).

        Returns:
            - None: all sessions of the same bot are allowed (default mode).
            - "bot-out": the session is outside every group and `bot` fallback
              is set; it may see unlisted same-bot sessions only.
            - "global": the session is outside every group and `global`
              fallback is set; it may see unlisted sessions of every bot.
            - An empty set: the session is outside every group and isolated.
            - Otherwise: the rule set allowed by the session's groups.
        """
        if not self._cfg("enable_custom_groups", False):
            return None
        groups = self._group_members()
        if not groups:
            return None
        current_platform = umo.split(":", 1)[0]
        cross = self._cross_bot()
        allowed: set[tuple[str, str]] = set()
        for members in groups:
            if not any(self._entry_matches(m, umo, current_platform) for m in members):
                continue
            for member in members:
                if not self._entry_allowed(member, current_platform, cross):
                    continue
                if member == "*":
                    allowed.add(("platform", current_platform))
                elif member.endswith(":*"):
                    allowed.add(("platform", member[:-2]))
                else:
                    allowed.add(("umo", member))
        if allowed:
            return allowed
        mode = self._cfg("out_of_group_mode", "isolate")
        if mode == "bot":
            return "bot-out"
        if mode == "global":
            return "global"
        return set()

    async def _persist(self) -> None:
        data = {self_id: list(records) for self_id, records in self._pools.items()}
        await self.put_kv_data(KV_POOLS_KEY, data)

    async def _record(self, event: AstrMessageEvent, text: str) -> None:
        """Append a formatted record to the pool of the event's bot."""
        umo = event.unified_msg_origin
        if self._allowed(umo) == set():
            return
        self_id = event.get_self_id()
        max_msgs = max(1, int(self._cfg("max_messages", 50)))
        self._seq[self_id] += 1
        async with self._locks[self_id]:
            pool = self._pools[self_id]
            pool.append(
                {
                    "umo": umo,
                    "text": text,
                    "ts": int(time.time()),
                    "seq": self._seq[self_id],
                }
            )
            while len(pool) > max_msgs:
                pool.popleft()
        logger.debug(f"shared_context: recorded | {self_id} | {umo} | {text}")
        try:
            await self._persist()
        except Exception as e:
            logger.error(f"shared_context: failed to persist pools: {e}")

    def _format_line(self, event: AstrMessageEvent, text: str, is_bot: bool) -> str:
        who = "bot" if is_bot else (event.get_sender_name() or event.get_sender_id())
        platform = event.get_platform_name() or "?"
        ts = (
            datetime.datetime.now().strftime("%m-%d %H:%M")
            if self._cfg("include_timestamps", False)
            else ""
        )
        group_id = event.get_group_id()
        location = f"/{group_id}" if group_id else ""
        bot_id = (
            f"/{event.get_self_id()}" if self._cfg("cross_bot_share", False) else ""
        )
        prefix = f"[{who}/{platform}{bot_id}{location}"
        if ts:
            prefix += f" {ts}"
        return f"{prefix}] {text}"

    def _truncate(self, text: str) -> str:
        max_msg_chars = max(1, int(self._cfg("max_message_chars", 200)))
        if len(text) <= max_msg_chars:
            return text
        return text[:max_msg_chars] + "..."

    async def _chain_text(
        self, comps: list, event: AstrMessageEvent, is_bot: bool
    ) -> str:
        """Build record text from a message chain per file_component_mode.

        Non-text components (images, files, voice, etc.) are handled according
        to `file_component_mode`: ignored, marked with a placeholder, captioned
        by the session's provider, or forwarded as file text content.
        """
        if not comps:
            return ""
        parts: list[str] = []
        for comp in comps:
            if isinstance(comp, Comp.Plain) and comp.text:
                parts.append(comp.text)
            else:
                parts.append(await self._non_plain_text(comp, event))
        return "".join(parts)

    @staticmethod
    def _component_marker(comp: object) -> str:
        """Placeholder marker for a non-text component."""
        if isinstance(comp, Comp.Image):
            return "[图片]"
        if isinstance(comp, Comp.File):
            return f"[文件: {comp.name or '未知'}]"
        if isinstance(comp, Comp.Record):
            return "[语音]"
        if isinstance(comp, Comp.Video):
            return "[视频]"
        if isinstance(comp, Comp.At):
            return f"[At: {comp.name or comp.qq or '?'}]"
        if isinstance(comp, Comp.Reply):
            return "[引用]"
        if isinstance(comp, Comp.Forward):
            return "[转发]"
        if isinstance(comp, Comp.Face):
            return "[表情]"
        return f"[{type(comp).__name__}]"

    async def _non_plain_text(self, comp: object, event: AstrMessageEvent) -> str:
        """Text representation of a non-Plain component per file_component_mode.

        `caption` and `full` both transcribe images via the caption model
        (`caption_use_multimodal` selects the multimodal/text model); `full`
        additionally reads text file content.
        """
        mode = self._cfg("file_component_mode", "ignore")
        if mode == "ignore":
            return ""
        marker = self._component_marker(comp)
        if mode == "placeholder":
            return marker
        if mode in ("caption", "full") and isinstance(comp, Comp.Image):
            url = comp.url or comp.file
            if url:
                cap = await self._caption_image(url, event)
                if cap:
                    return f"[图片: {cap}]"
            return marker
        if mode == "full" and isinstance(comp, Comp.File):
            content = await self._read_file_text(comp)
            if content:
                return f"[文件: {comp.name or '未知'}\n{content}]"
            return marker
        return marker

    async def _caption_image(self, url: str, event: AstrMessageEvent) -> str:
        """Transcribe an image using the configured caption model.

        References AstrBot core's `_request_img_caption` (astr_main_agent.py)
        and `group_chat_context.get_image_caption`. The plain-text caption
        model (`caption_text_provider_id`) is always the transcription
        executor; when `caption_use_multimodal` is enabled, the multimodal
        caption model (`caption_multimodal_provider_id`) handles image content
        first, falling back to the text model. Empty provider ids fall back
        to the session provider. Note that captioning every image incurs an
        extra LLM call and may be expensive.
        """
        try:
            use_multimodal = self._cfg("caption_use_multimodal", True)
            text_provider = self._resolve_provider(
                self._cfg("caption_text_provider_id", ""), event
            )
            provider = text_provider
            prompt = self._cfg(
                "caption_prompt", "Please describe the image using Chinese."
            )
            if use_multimodal:
                provider = (
                    self._resolve_provider(
                        self._cfg("caption_multimodal_provider_id", ""), event
                    )
                    or text_provider
                )
            if not provider:
                return ""
            resp = await provider.text_chat(
                prompt=prompt,
                image_urls=[url],
                persist=False,
            )
            text = (resp.completion_text or "").strip()
            max_msg_chars = max(1, int(self._cfg("max_message_chars", 200)))
            if len(text) > max_msg_chars:
                return text[:max_msg_chars] + "..."
            return text
        except Exception as e:
            logger.error(f"shared_context: image caption failed: {e}")
            return ""

    def _resolve_provider(
        self, provider_id: str, event: AstrMessageEvent
    ) -> Provider | None:
        """Resolve a caption provider by id, falling back to the session
        provider (with a warning when the configured id is missing)."""
        if provider_id:
            provider = self.context.get_provider_by_id(provider_id)
            if provider:
                return provider
            logger.warning(
                f"shared_context: caption provider {provider_id} not found, "
                f"fallback to session provider"
            )
        return self.context.get_using_provider(umo=event.unified_msg_origin)

    async def _read_file_text(self, comp: Comp.File) -> str:
        """Read a text file's content for the `full` mode."""
        try:
            path = await comp.get_file()
            if not path or not os.path.exists(path):
                return ""
            max_chars = max(1, int(self._cfg("max_file_chars", 2000)))
            with open(path, encoding="utf-8", errors="replace") as f:
                content = f.read(max_chars + 1)
            if "\x00" in content:
                return ""  # binary file
            if len(content) > max_chars:
                return content[:max_chars] + "..."
            return content
        except Exception as e:
            logger.debug(f"shared_context: failed to read file: {e}")
            return ""

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent) -> None:
        """Record user messages from all channels."""
        try:
            text = await self._chain_text(event.get_messages(), event, False)
            text = text.strip()
            if not text:
                return
            if self._cfg("skip_command", True) and text.startswith("/"):
                return
            if self._cfg("file_component_mode", "ignore") != "full":
                text = self._truncate(text)
            await self._record(event, self._format_line(event, text, False))
        except Exception as e:
            logger.error(f"shared_context: failed to record message: {e}")

    @filter.after_message_sent()
    async def after_message_sent(self, event: AstrMessageEvent) -> None:
        """Record bot replies if enabled."""
        try:
            if not self._cfg("include_bot_replies", True):
                return
            result = event.get_result()
            if not result or not result.chain:
                return
            text = await self._chain_text(result.chain, event, True)
            text = text.strip()
            if not text:
                return
            if self._cfg("file_component_mode", "ignore") != "full":
                text = self._truncate(text)
            await self._record(event, self._format_line(event, text, True))
        except Exception as e:
            logger.error(f"shared_context: failed to record bot reply: {e}")

    @filter.on_llm_request()
    async def on_llm_request(
        self, event: AstrMessageEvent, req: ProviderRequest
    ) -> None:
        """Inject recent messages from other sessions into the LLM request.

        The injected block always sits at the tail of the last user message,
        so it can never hit server-side prefix caches (it changes every turn).
        With `enable_cache_optimization` off (default), the whole pool is
        injected every turn, capped only by `max_chars`. When the optimization
        switch is on, the block is kept as small as possible to preserve cache
        hit rates:
        - `incremental_injection`: after the first request of a session, only
          records newer than the last request (plus a small recent window) are
          injected, instead of the whole pool every turn.
        - `cache_ratio`: the block is additionally capped by the size of the
          cacheable context (session history + current prompt), so the block
          never dominates the request.
        """
        try:
            self_id = event.get_self_id()
            current_umo = event.unified_msg_origin
            allowed = self._allowed(current_umo)
            if allowed == set():
                return
            bot_out = allowed == "bot-out"
            global_share = allowed == "global"
            if bot_out or global_share:
                allowed = None
            logger.debug(
                f"shared_context: session hit | {current_umo} | "
                f"mode={'global' if global_share else 'bot-out' if bot_out else 'share_all' if allowed is None else 'rules(' + str(len(allowed)) + ')'} | "
                f"cross_bot={self._cross_bot()}"
            )

            optimized = bool(self._cfg("enable_cache_optimization", False))
            max_chars = max(1, int(self._cfg("max_chars", 3000)))
            if optimized:
                budget = min(max_chars, self._adaptive_budget(req))
            else:
                budget = max_chars
            window_minutes = int(self._cfg("time_window_minutes", 0))
            cutoff_ts = (
                int(time.time()) - window_minutes * 60 if window_minutes > 0 else None
            )

            incremental = optimized and bool(self._cfg("incremental_injection", True))
            keep_recent = max(0, int(self._cfg("keep_recent", 3)))
            key = (self_id, current_umo)
            last_seen = self._last_seen.get(key)

            items: list[tuple[str, int, int, str]] = []  # (sid, ts, seq, line)
            pool_max: dict[str, int] = {}
            for sid, pool in list(self._pools.items()):
                if not global_share and not self._cross_bot() and sid != self_id:
                    continue
                if not pool:
                    continue
                seen_sid = last_seen.get(sid, 0) if last_seen else 0
                max_seq = max((int(r.get("seq", 0)) for r in pool), default=0)
                pool_max[sid] = max_seq
                for record in pool:
                    umo = record.get("umo", "")
                    if (sid, umo) == (self_id, current_umo):
                        continue
                    if (bot_out or global_share) and self._in_any_group(umo):
                        continue
                    if allowed is not None:
                        platform = umo.split(":", 1)[0]
                        if ("umo", umo) not in allowed and (
                            "platform",
                            platform,
                        ) not in allowed:
                            continue
                    seq = int(record.get("seq", 0))
                    if cutoff_ts is not None and int(record.get("ts", 0)) < cutoff_ts:
                        continue
                    line = record.get("text", "")
                    if not line or len(line) > budget:
                        continue
                    if incremental and last_seen is not None:
                        is_new = seq > seen_sid
                        is_recent = max_seq - keep_recent < seq
                        if not is_new and not is_recent:
                            continue
                    items.append((sid, int(record.get("ts", 0)), seq, line))
            if not items:
                logger.debug(
                    f"shared_context: pools empty or no match, skip injection | "
                    f"{current_umo}"
                )
                return

            items.sort(key=lambda it: (it[1], it[2]), reverse=True)
            lines: list[str] = []
            used = 0
            injected_seq: dict[str, int] = {}
            for sid, _ts, seq, line in items:
                if len(line) > budget - used:
                    continue
                lines.append(line)
                used += len(line)
                if seq > injected_seq.get(sid, 0):
                    injected_seq[sid] = seq
            if not lines:
                logger.debug(
                    f"shared_context: nothing fits the budget, skip injection | "
                    f"{current_umo}"
                )
                return
            if incremental:
                self._last_seen[key] = injected_seq
                if len(lines) < len(items):
                    logger.debug(
                        f"shared_context: budget cut | {len(items) - len(lines)} "
                        f"lines pending for next request"
                    )

            block = CONTEXT_HEADER + "\n".join(reversed(lines)) + CONTEXT_FOOTER
            req.extra_user_content_parts.append(TextPart(text=block).mark_as_temp())
            logger.debug(
                f"shared_context: injected {len(lines)} lines ({used}/{budget} chars) "
                f"for session {current_umo}"
            )
        except Exception as e:
            logger.error(f"shared_context: failed to inject context: {e}")

    def _adaptive_budget(self, req: ProviderRequest) -> int:
        """Cap the injected block by the size of the cacheable context.

        Server-side prefix caches only cover the request prefix (system prompt,
        session history and the current user message); the injected block is
        always a miss. Capping the block at `cache_ratio` x the cacheable
        context keeps the hit rate at or above a predictable floor.
        """
        total = 0
        contexts = req.contexts if isinstance(req.contexts, list) else []
        for msg in contexts:
            content = msg.get("content", "") if isinstance(msg, dict) else ""
            total += len(content) if isinstance(content, str) else len(str(content))
        total += len(req.prompt or "")
        ratio = max(0.1, float(self._cfg("cache_ratio", 1.5)))
        return max(300, int(total * ratio))
