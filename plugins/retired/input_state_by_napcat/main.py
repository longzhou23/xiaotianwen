import asyncio
from typing import Dict, Tuple
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)
from astrbot.core.provider.entities import LLMResponse, ProviderRequest


@register("input_state_by_napcat", "ctrlkk", "[仅NapCat]输入状态显示", "1.2")
class MyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self._tasks: Dict[str, Tuple[asyncio.Task, asyncio.Event]] = {}

        self.interval = config.get("interval", 0.5)
        self.timeout = config.get("timeout", 120)

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
        await asyncio.gather(
            *(self._stop_input_state_task(uid) for uid in list(self._tasks.keys()))
        )

    async def _run_input_state_loop(
        self,
        uid: str,
        event: AstrMessageEvent,
        interval: float,
        timeout: float,
    ):
        stop_event = self._tasks[uid][1]
        try:

            async def loop():
                while not stop_event.is_set():
                    await self.show_input_state(event)
                    await asyncio.sleep(interval)

            await asyncio.wait_for(loop(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(f"针对uid {uid} 的任务超时")
        finally:
            stop_event.set()
            self._tasks.pop(uid, None)

    async def _start_input_state_task(
        self,
        uid: str,
        event: AstrMessageEvent,
        interval: float,
        timeout: float,
    ):
        if uid in self._tasks and not self._tasks[uid][0].done():
            return  # 已经有任务在运行
        stop_event = asyncio.Event()
        task = asyncio.create_task(
            self._run_input_state_loop(uid, event, interval, timeout)
        )
        self._tasks[uid] = (task, stop_event)

    async def _stop_input_state_task(self, uid: str):
        if uid in self._tasks:
            task, stop_event = self._tasks[uid]
            stop_event.set()
            await task
            self._tasks.pop(uid, None)

    async def show_input_state(self, event: AstrMessageEvent):
        """显示输入状态。"""
        if not isinstance(event, AiocqhttpMessageEvent):
            return

        if event.get_group_id():
            return

        client = event.bot
        user_id = event.get_sender_id()
        payloads = {"user_id": user_id, "event_type": 1}
        await client.api.call_action("set_input_status", **payloads)

    # 设置为1，在其它插件进入on_llm_request阶段前就开始显示，避免插件耗时操作等待太多时间
    @filter.on_llm_request(priority=1)
    async def on_llm_req(self, event: AstrMessageEvent, req: ProviderRequest):
        """请求开始"""
        uid = event.unified_msg_origin
        await self._start_input_state_task(
            uid, event, interval=self.interval, timeout=self.timeout
        )

    @filter.on_llm_response()
    async def on_llm_resp(self, event: AstrMessageEvent, resp: LLMResponse):
        """请求结束"""
        uid = event.unified_msg_origin
        await self._stop_input_state_task(uid)

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        """发送消息前"""
        await self.show_input_state(event)

    @filter.after_message_sent()
    async def after_message_sent(self, event: AstrMessageEvent):
        """消息发送完成"""
