# Shared Context · 共享上下文

![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)
![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.9.2-blue)
![Python](https://img.shields.io/badge/python-%3E%3D3.10-blue)
![Version](https://img.shields.io/github/v/tag/Galaxy1108/astrbot_plugin_shared_context)
![GitHub stars](https://img.shields.io/github/stars/Galaxy1108/astrbot_plugin_shared_context)

> [!IMPORTANT]
> 本项目代码完全由 AI 生成

让同一台 AstrBot 上的**不同会话**共享 LLM 上下文——可以是同一机器人的不同用户/群，也可以（显式配置后）是不同机器人的会话。A 私聊说"我去吃饭了"，B 私聊时 AI 会知道（AI 不会向 B 透露 A 的身份和消息来源）。

> [!NOTE]
> 本插件**不修改**任何会话的上下文。
> 它不写入会话历史、不修改 `system_prompt`、不改变 `/reset` 和会话隔离的行为；只在每轮 LLM 请求中**临时附加**其他会话的近期消息（`.mark_as_temp()`），请求结束即丢弃。

## 原理

插件被动记录各机器人下所有会话的消息流水（用户消息 + 机器人回复），在每次 LLM 请求时把**其他会话**最近的消息作为临时上下文块注入（`.mark_as_temp()`）：

- 不进入任何会话的历史存储（`/reset`、WebUI 历史面板、会话隔离全部不受影响）
- 不修改 `system_prompt`（不破坏模型服务端提示词缓存）
- 当前会话自己的历史已在请求中，注入时自动排除，不重复

## 特性

- **默认即用**：开箱即用，同一机器人的所有会话互相共享
- **多共享组**：可配置多个组，仅组内会话互相共享（闭组）
- **跨机器人默认隔离**：消息池按 `self_id` 分桶，不同机器人默认永不互通；仅显式开启 `cross_bot_share` 并分组后才互通
- **体积有界**：池子条数、单轮注入字符数、单条截断长度、时间窗四层上限
- **持久化**：流水存 KV，插件重载/重启不丢失
- 隐私指令内置于注入块：模型不得向用户透露其他会话的消息内容与来源

## 安装

在 AstrBot WebUI 插件市场搜索 `shared_context` 安装，或：

```bash
cd AstrBot/data/plugins
git clone https://github.com/Galaxy1108/astrbot_plugin_shared_context
```

然后在 WebUI 插件管理点"重载插件"。

## 配置（WebUI 可视化）

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| **自定义共享组**（分组） | | |
| `enable_custom_groups` | `false` | 启用自定义共享组。关闭时（默认）同一机器人下的所有会话共享全部上下文 |
| `out_of_group_mode` | `isolate` | 组外会话的处理方式（组内只见组内，组外只见组外，互不可见）：`isolate` = 完全独立；`bot` = 与本机器人其他组外会话共享；`global` = 与所有机器人的组外会话共享（不推荐） |
| `cross_bot_share` | `false` | 允许跨机器人共享，仅对 `share_groups` 中显式分组的会话生效 |
| `share_groups` | `{}` | 多共享组：键为组名，值为该组的 `unified_msg_origin` 数组 |
| `max_messages` | `50` | 共享池保留的最大消息条数 |
| `max_chars` | `3000` | 每轮 LLM 请求注入的字符数上限 |
| `max_message_chars` | `200` | 单条消息记录时的截断长度 |
| `time_window_minutes` | `0` | 只注入最近 N 分钟内的消息，0 表示不限 |
| `include_bot_replies` | `true` | 是否记录并共享机器人回复 |
| `skip_command` | `true` | 跳过以 `/` 开头的指令消息 |
| `include_timestamps` | `true` | 注入的记录行是否带时间戳（关闭可压缩每轮注入量） |
| **缓存优化**（分组，激进模式，默认关闭） | | |
| `enable_cache_optimization` | `false` | 启用缓存优化，牺牲少量跨轮上下文连续性换取服务端提示词缓存命中率（详见下文「缓存优化」） |
| `incremental_injection` | `true` | 增量注入：会话第一次请求注入全量，之后只注入新增记录 + 最近 `keep_recent` 条保底 |
| `keep_recent` | `3` | 跨轮保底保留的最近记录条数，越大越接近旧行为、越小缓存越友好 |
| `cache_ratio` | `1.5` | 注入块与可缓存上下文（会话历史 + 当前消息）的体积比上限，保证缓存命中率有下限 |
| **文件与图片转述**（分组） | | |
| `file_component_mode` | `ignore` | 消息中的非文本组件（图片/文件/语音等）如何处理：`ignore` = 忽略；`placeholder` = 占位标记（如 `[图片]`）；`caption` = 图片用 AI 转述；`full` = 文件读取文本内容转发、图片同样转述 |
| `caption_use_multimodal` | `true` | 多模转述开关：开启时图片由多模态转述模型看图直述 |
| `caption_text_provider_id` | `` | 纯文本转述模型提供商（**始终启用**：多模关闭时执行图片转述，开启时作为兜底），留空用当前会话提供商 |
| `caption_prompt` | `Please describe the image using Chinese.` | 图片转述提示词（与 AstrBot 内置一致，仅多模开关开启时显示） |
| `caption_multimodal_provider_id` | `` | 多模态转述模型提供商（仅转述图片等多模态内容，仅多模开关开启时显示），留空回退到纯文本转述模型/当前会话提供商 |

### 自定义共享组

1. 开启 `enable_custom_groups`
2. 在 `share_groups` 的 JSON 编辑器里填写：**键为组名，值为该组的 `unified_msg_origin` 数组**（编辑器带实时 JSON 校验）
3. 向机器人发送 `/sid`（AstrBot 内置指令），可直接查看当前会话的 `unified_msg_origin`（格式 `平台实例ID:消息类型:会话号`，如 `qq-bot:FriendMessage:10001` 是 QQ 私聊、`qq-bot:GroupMessage:20002` 是 QQ 群聊）

```json
{
  "工作群": ["qq-bot:GroupMessage:20002", "wechat-bot:GroupMessage:789"],
  "好友": ["qq-bot:FriendMessage:10001", "telegram-bot:FriendMessage:222"],
  "全家桶": ["*"],
  "整台机器人": ["qq-bot:*"]
}
```

组条目支持三种写法：

- `qq-bot:FriendMessage:10001`：精确指定一个会话（`/sid` 可查）
- `*`：展开为**当前会话所属机器人的所有会话**
- `qq-bot:*`：指定机器人的全部会话

#### `*` 的准确语义（容易误解，注意）

对**任何**会话而言，`*` 就等于"我这台机器人里的全部对话"——每个会话都是相对自己求值的，`*` 永远只展开本机器人，不会把其他机器人的会话展开进来。

因此组 `["*", "wechat-bot:FriendMessage:10001"]` 的实际效果是：

- 本机所有会话：能看到微信那一个会话（组里的显式条目）
- 微信那一个会话：看到的是**它自己机器人**的全部会话 + 自己——看不到本机

这是**单向桥**。想让微信侧也能看到本机，把本机机器人也显式声明进组：

```json
{ "对称桥": ["*", "qq-bot:*", "wechat-bot:FriendMessage:10001"] }
```

此时微信侧的 `qq-bot:*` 条目对它同样生效，双向对等。

规则：

- 共享关系**对等且隔离**：组内会话只见组内，组外会话只见组外，两组互不可见
- 会话可属于多个组（出现在任一组的数组里即算成员）；注入时取所属所有组的成员并集（排除自身）
- 未命中任何组条目的会话按 `out_of_group_mode` 处理：
  - `isolate`（默认）：完全独立——不记录、不接收共享（闭组）
  - `bot`：与本机器人的其他组外会话互相共享
  - `global`（不推荐）：与所有机器人的组外会话互相共享
- 留空 `{}` 或关闭开关 = 仍然共享该机器人的所有会话

### 跨机器人共享（可选）

默认关闭，不同机器人之间的上下文**物理隔离**。需要时：

1. 开启 `enable_custom_groups` 和 `cross_bot_share`
2. 把不同机器人的会话 umo 放进**同一个组**即可（不同机器人的 `平台实例ID` 不同，umo 天然互不冲突）：

```json
{
  "我的跨平台": ["qq-bot:FriendMessage:10001", "wechat-bot:FriendMessage:10001"]
}
```

- 未开启 `cross_bot_share` 时，组内属于其他机器人的条目会被忽略（按 umo 第一段 `平台实例ID` 过滤）
- 只有两个开关都开启且会话被显式分组时才会跨机器人共享——不开开关行为与旧版完全一致

## 与 enhance_mode 等群聊插件共存

- 群内感知（同会话消息注入回本群）是 AstrBot 内置群聊上下文感知 / [astrbot_plugin_astrbot_enhance_mode](https://github.com/Axi404/astrbot_plugin_astrbot_enhance_mode) 群聊历史增强的职责
- 本插件只注入**其他会话**的消息，注入时排除同会话记录，与上述插件不会重复注入
- 建议：启用 enhance_mode 群聊历史增强时，关闭 AstrBot 内置的群聊上下文感知功能，避免重复

## 注意

- 共享内容包含用户消息和（可选）机器人回复，机器人回复可能含用户私密信息，请按需关闭 `include_bot_replies`
- 非文本组件（图片/文件等）默认不记录；`file_component_mode` 可改为占位标记、图片 AI 转述（`caption`）或完整内容（`full`：文件读取文本内容、图片同样转述）
- `caption` 和 `full` 的图片转述：**纯文本转述模型**（`caption_text_provider_id`）始终作为转述执行者；开启多模转述（`caption_use_multimodal`）时，图片由**多模态转述模型**（`caption_multimodal_provider_id`）看图直述，失败/未配置时回退到纯文本转述模型；提示词用 `caption_prompt`（默认与 AstrBot 内置一致）；**转述每一张图片都会产生一次额外 LLM 调用，转述所有图片可能很昂贵**；模型不支持识图时转述失败会回退为 `[图片]` 占位
- 每轮请求都会携带共享块，token 是固定开销，可用 `max_messages` / `max_chars` 控制
- 需要 AstrBot >= 4.9.2（插件 KV 存储）

## 缓存优化

DeepSeek / OpenAI 等提供商的**前缀缓存**只命中请求前缀（系统提示 + 会话历史 + 当前消息）；本插件的共享块位于请求尾部且每轮内容都在变，因此这些 token **每轮都无法命中缓存**，会直接把整体命中率拉低（块越大降得越多）。

`enable_cache_optimization`（默认关闭，行为与旧版本一致）开启后从三方面把每轮 miss 量压到最小：

1. **增量注入**（`incremental_injection`，默认开）：会话的第一次请求注入全量；之后只注入"上次请求之后其他会话新增的记录 + 最近 `keep_recent` 条保底"。其他会话安静时，块从上千字符缩到几百字符。
2. **自适应上限**（`cache_ratio`，默认 `1.5`）：块大小 = `min(max_chars, 可缓存上下文字符数 × cache_ratio)`，历史短则块自动缩小，命中率始终有下限（约 60% 起步）。
3. **格式压缩**（`include_timestamps` 可单独关闭）：记录行不带时间戳，进一步省 token。

代价：较早的其他会话消息不会每轮重复注入（首次请求和新增时都会完整出现），跨多轮的"遥记"能力略有下降；`keep_recent` 调大即可更接近旧行为。

## 常见问题

**Q: 我的两个机器人会不会串台？**
默认不会。消息池按 `self_id` 分桶，不开 `cross_bot_share` 时跨机器人共享在代码层面被禁止；只有同时开启 `enable_custom_groups` 和 `cross_bot_share` 并显式分组后才会互通。

**Q: 群聊消息会被共享吗？**
会。群消息（含未唤醒机器人的闲聊）与机器人回复都会入池并共享给其他会话。开启"隔离会话"的群内成员之间也互相感知。

**Q: 改了配置要重启吗？**
配置在 WebUI 修改保存后，重载插件即可生效。
