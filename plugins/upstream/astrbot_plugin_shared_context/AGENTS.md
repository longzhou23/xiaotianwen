# AGENTS.md

本仓库是一个 AstrBot 插件（源码树），目标：让同一个机器人下不同用户/渠道的对话共享 LLM 上下文（如 A 私聊说了"我去吃饭了"，B 私聊时 AI 知道）。

## 环境（本地已验证的事实）

- AstrBot 源码与存档：`/home/int_256t/Documents/Projects/AstrBot/`（本仓库外，只读参考）。官方文档在 `docs/zh/dev/star/`，以 `plugin-new.md` 为准，详细 API 看 `plugin.md`，存储看 `guides/storage.md`。
- 插件调试方式：插件是运行时注入的，必须放进运行中的 AstrBot。把本仓库放到 `AstrBot/data/plugins/astrbot_plugin_shared_context/`（或克隆），在 WebUI 插件管理点"重载插件"；AstrBot 本体启动：`uv run main.py`（默认 API 端口 6185）。
- 参考实现（同机已装）：`AstrBot/data/plugins/astrbot_plugin_courier/` —— 跨会话主动发消息的插件，`main.py` 内含跨会话管理的完整写法。
- 本仓库当前还不是 git 仓库，提交前先 `git init`。
- 访问 GitHub 需要代理时用 `http://127.0.0.1:7897`（如 `gh`/`git` 配置 `https_proxy`）。

## 发布流程（重要）

- 日常开发/测试只在**测试仓库** `Galaxy1108/astrbot_plugin_shared_context_unstable`（remote 名 `unstable`，版本号带 `-unstable` 后缀）进行，改动推它的 `main` 分支。
- **主仓库**（`origin`）的 `main` 分支只在**发布时**更新：正式版本号 + CHANGELOG 后 merge/推送。
- 主仓库日常保持干净，不要在 main 上提交开发改动。

## 插件结构（缺一不可）

- `metadata.yaml`：必填 `name`/`desc`/`author`/`version`；可选 `display_name`、`short_desc`、`support_platforms`、`astrbot_version`。AstrBot 没有它不会加载插件。
- `main.py`：类必须继承 `from astrbot.api.star import Star`，所有 handler 必须写在类内，参数前两个是 `self`、`event`。
- 第三方依赖只能靠插件目录下 `requirements.txt`（pip 机制）。
- `_conf_schema.json`：可选配置 Schema，会自动生成 `data/config/<plugin_name>_config.json` 并以 `AstrBotConfig` 传入 `__init__`。

## 共享上下文的实现要点（本项目核心，容易踩坑）

- 隔离原因：每个会话的 LLM 历史按 `event.unified_msg_origin`（格式 `platform_name:message_type:session_id`）隔离；`platform_settings.unique_session`（WebUI"隔离会话"）开启后群内每人上下文独立。
- 注入跨会话上下文的正规做法：`@filter.on_llm_request()` 钩子，签名 `(self, event, req: ProviderRequest)`，把内容 append 到 `req.extra_user_content_parts`（`TextPart` 来自 `astrbot.core.agent.message`）。只影响本轮请求且不入会话历史则 `.mark_as_temp()`。
- 禁止把每轮变化的内容 append 到 `req.system_prompt`：会破坏模型服务端提示词缓存，成本涨约 7–20 倍（官方文档明确警告）。
- 现成范本：`AstrBot/astrbot/builtin_stars/astrbot/group_chat_context.py` 就是"记录各会话消息 + on_llm_request 注入"的完整实现（群上下文感知）；`AstrBot/astrbot/core/conversation_mgr.py` 是会话历史存储逻辑。
- 监听所有渠道消息：`@filter.event_message_type(filter.EventMessageType.ALL)`。
- 钩子（on_llm_request 等）里不能 `yield`，发消息用 `await event.send(...)`；想终止后续流程用 `event.stop_event()`。
- 取消息内容：`event.get_messages()`（消息链）、`event.get_message_str()`；发送者/平台/群：`event.get_sender_id()`、`event.get_platform_name()`、`event.get_group_id()`。

## 约定与规则（AstrBot 官方要求）

- 日志必须用 `from astrbot.api import logger`（不要用 `logging`）。
- 网络请求禁止 `requests`，用 aiohttp/httpx。
- 持久化数据放 `data/` 下，绝不能放插件目录（更新/重装会被清掉）。小数据用 KV：`self.put_kv_data/get_kv_data/delete_kv_data`（>=4.9.2，按插件隔离）；大文件放 `data/plugin_data/{plugin_name}/`（路径用 `astrbot.core.utils.astrbot_path.get_astrbot_data_path`）。
- 提交前 `ruff format . && ruff check .`；注释用英文；docstring 用 Google 格式。
