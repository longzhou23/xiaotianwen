# 小天文 Agent Loop 缓存、效率与响应速度优化 TODO

状态：P0 provider/工具提醒改动已在本地工作区实现，待部署验证；Core 步数限制仍待线上配置

基线日期：2026-08-29

目标环境：AstrBot 4.27.4、`astrbot_plugin_chatgpt_codex` Responses transport、GPT-5.6 Luna 系列、2 核 4 GB 级实例

## 当前工作区实施状态（2026-08-30）

| 项目 | 本地状态 | 线上状态 |
|---|---|---|
| Codex provider 识别 Runner 已组装的动态上下文并避免二次追加 | 已实现并有定向测试 | 尚未部署/观察 |
| 动态上下文、工具 schema、请求 route 的脱敏诊断字段 | 已实现 | 尚未部署/观察 |
| `prompt_cache_key` 按 model/route/instructions/tools 稳定分桶 | 已实现并有定向测试 | 尚未部署/观察 |
| Group Chat Plus 原生 schema 存在时跳过全文工具提醒 | 已实现，默认开关为 `true` | 尚未部署/观察 |
| `max_agent_step=30 → 8` | 未改动（AstrBot Core 不在本仓库） | 待通过实例配置或版本化 Core 补丁实施 |
| 重复副作用工具硬保护、最终发送原子幂等 | 未改动 | 待单独设计和回归 |

本状态表只描述当前公共仓库工作区，不代表 Azure/VM 已经加载这些文件。部署前必须先备份实例配置，部署后按第 10 节的 payload、功能和 24 小时指标验收；未通过验收时回滚 provider/plugin 文件，不清理记忆、图片池或数据库。

## 1. 结论摘要

当前最值得先处理的并不是单纯缩短人格提示词，而是以下五件事：

1. **修复 Codex provider 对动态上下文的重复发送。** AstrBot Runner 已经把 `extra_user_content_parts` 组装进最后一条 user message，provider 又把同一批内容包在 `<astrbot_dynamic_context>` 中追加一次。Iris L1/L2/L3、画像、好感度和场景块因此可能在同一请求中出现两遍，并在每个工具续轮末尾再次追加。
2. **停止“工具 schema + 工具提醒全文”双份发送。** Group Chat Plus 当前 `enable_tools_reminder=true`，而 provider 已经通过 Responses `tools` 字段发送完整 schema；再把工具清单作为动态用户文本发送一次会增加未缓存输入，且可能让模型看到两套不完全一致的描述。
3. **重做 `prompt_cache_key` 的分桶方式。** 当前 key 只取 `session_id` 哈希：同一群的 DecisionAI、主回复和其他直连请求会共用一个分桶，而不同群即使拥有完全相同的稳定 persona/tools 又无法共用分桶。应按“请求类型 + 模型 + 稳定 instructions + 工具 schema”生成 64 字符哈希。
4. **把 Agent 最大步数从 30 降到 8，并增加重复工具硬保护。** 24 小时日志中未发现达到最大步数的任务，常见表情包和星图流程只需 2～3 个 Agent 模型轮次。30 步主要是在异常时放大延迟和费用。
5. **只并发真正无副作用的工具。** Core 已允许模型一次返回多个 tool calls，但实际执行仍是顺序 `for`。日志中一次 5 个 `send_meme` 串行约 37 秒。查询类工具可以有界并发；发送、状态修改、偷取/写入等副作用工具必须继续串行并加幂等锁。

执行顺序应是：**先加可观测性 → 修重复动态块 → 去掉工具提醒重复 → 调整缓存 key → 限制异常轮次 → 再评估并发、显式缓存和推理强度**。每次只改一组变量，保留行为 A/B 和回滚入口。

## 2. 研究范围与证据

### 2.1 已核对的运行代码

- AstrBot 容器内：`/AstrBot/astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py`
- AstrBot 容器内：`/AstrBot/astrbot/core/agent/runners/tool_loop_agent_runner.py`
- AstrBot 容器内：`/AstrBot/astrbot/core/provider/entities.py`
- `plugins/modified/astrbot_plugin_chatgpt_codex/agent_provider.py`
- `plugins/modified/astrbot_plugin_chatgpt_codex/codex_service.py`
- `plugins/modified/astrbot_plugin_chatgpt_codex/transport/responses.py`
- `plugins/modified/astrbot_plugin_group_chat_plus/main.py`
- `plugins/modified/astrbot_plugin_group_chat_plus/utils/reply_handler.py`
- `plugins/upstream/astrbot_plugin_iris_memory/iris_memory/core/llm_request_hook.py`
- ContextAware、ImageContextPool、Affection、Shared Context、Tool Use Cleaner、Astrmetry、AntiPromptInjector 的 LLM 请求钩子。

### 2.2 当前关键配置

| 配置 | 当前值 | 影响 |
|---|---:|---|
| `streaming_response` | `false` | 保留完整输出审计和消息分段兼容，但用户看不到增量文本 |
| `max_agent_step` | `30` | 异常工具循环上限过高 |
| `tool_call_timeout` | `120s` | 单个慢工具最多阻塞两分钟 |
| `tool_schema_mode` | `full` | 每个模型轮次发送完整工具 schema，不额外增加 skills-like 参数重查轮次 |
| `request_max_retries` | `5` | Codex provider 当前会丢弃此 kwarg；不能把它当作实际重试次数 |
| `context_limit_reached_strategy` | `llm_compress` | 超限时可能引入一次额外压缩模型调用 |
| Codex backend | `transport` | 使用 Responses HTTP/SSE，实际由 AstrBot Agent Runner 执行工具循环 |
| Codex harness | `lightweight` | 已避免完整 Codex harness 的额外提示开销 |
| Codex concurrent turns | `2` | 对 2 核 4 GB 实例合理，不建议盲目提高 |
| Group Chat Plus 上下文 | `40` 条 | 已执行既有主上下文限制 |
| Group wait window | `3000ms` | 最后一条消息后约 3 秒开始主流程，属于有意的防抖等待 |
| ContextAware | 历史 25、对话窗 8、图片窗 30 | 图片窗是元数据/按需回放窗口，不应变成每轮 30 张原图输入 |
| ImageContextPool | 30 张、TTL 21600s | 当前负责图片 ID/描述索引与按需回放 |
| Group Chat Plus 工具提醒 | 开启 | 与实际 `tools` schema 重复，是优先优化项 |

### 2.3 2026-08-29 使用量基线

数据来自 Codex provider 自己的 `usage.db` 聚合，只统计数值，不读取 prompt、persona 或会话正文：

| 指标 | 数值 |
|---|---:|
| provider turn 记录 | 3,199 |
| 输入 Token | 14,528,783 |
| 命中缓存 Token | 2,817,024 |
| 输出 Token | 967,493 |
| 加权缓存命中率 | **19.39%** |
| 各请求命中率的简单平均 | 11.52% |

这里的 turn 既包括主回复 Agent 轮次，也可能包括 DecisionAI、图片转述、记忆后台模型调用等独立请求，不能直接等同于“用户发了 3,199 条消息”。优化后的统计必须增加 `route` 标签才能区分请求类型。

### 2.4 最近 24 小时 Agent 工具基线

| 指标 | 数值 |
|---|---:|
| 有工具调用的 Agent 轮次 | 27 |
| 一轮返回多个工具的轮次 | 2 |
| 单轮最大工具数 | 5 |
| 高频工具 | `send_meme` 17、`search_meme` 9、`analyze_star_field` 4 |
| 达到 `max_agent_step` | 0 |
| 日志可见空输出重试 | 0 |

这说明降低最大步数的风险较小，而查询/表情包/星图工具的链路是最适合做专项性能回归的样本。

## 3. 4.27.4 的真实 Agent Loop

```text
消息事件
  │
  ├─ Debounce / Group Chat Plus 3 秒等待窗 / 是否回复判断
  │
  ├─ build_main_agent：构造 ProviderRequest 和 Runner
  │
  ├─ on_llm_request 钩子链：本条主请求只执行一次
  │    ├─ Affection / Iris / Shared Context / ContextAware
  │    ├─ ImageContextPool / Astrmetry / 安全插件
  │    └─ Group Chat Plus 最终收束（priority=-100000）
  │
  ├─ Runner.reset：把 system、contexts、prompt、extra、媒体组装成 messages
  │
  └─ Agent Loop
       ├─ round 0：完整 messages + 完整 tools → 模型
       │    ├─ 无 tool_calls → 最终回复
       │    └─ 有 tool_calls → 执行工具
       ├─ 追加 assistant(tool_calls) + tool(result)
       ├─ round 1：截至当前的完整 messages + 完整 tools → 模型
       ├─ ……
       └─ 最终回复 → on_llm_response → 分段/审计/发送/保存历史
```

### 3.1 必须纠正的旧判断

旧 `docs/todo.md` 曾写过“工具下一轮会再次执行整条 `on_llm_request` 链”。对当前 4.27.4 的内部 Agent Runner，这个判断已经不成立：

- `InternalAgentSubStage` 在 Runner reset 之前调用一次 `OnLLMRequestEvent`；
- `ToolLoopAgentRunner.step()` 后续只压缩/整理 `run_context.messages`、调用 provider、执行工具并追加消息；
- `step()` 本身不再调 `on_llm_request`。

因此：

- Iris 检索、画像、好感度和 ContextAware 场景通常只在该主请求开始时收集一次；
- 工具轮次的主要浪费来自**完整历史、完整工具 schema 和首次动态上下文的反复重放**；
- 插件自己调用 `provider.text_chat()`、`context.llm_generate()` 或再次 `event.request_llm()` 是另一条独立请求，必须用 `route` 单独计数，不能和主 Agent round 混在一起。

### 3.2 合理的模型调用数量

| 场景 | 主 Agent 的合理 provider 轮次 | 说明 |
|---|---:|---|
| 普通文字回复，无工具 | 1 | 一次模型生成最终文本 |
| 一个工具，然后回答 | 2 | 选择工具一次 + 读工具结果回答一次 |
| `search_meme → send_meme → 结束` | 2～3 | 若发送工具直接结束 Agent 可为 2，否则最后总结为 3 |
| `analyze_star_field → send_annotated → 结束` | 2～3 | 解算和按需发送各一轮 |
| DecisionAI + 普通主回复 | 决策 1 + 主 Agent 1 | 两条 route，不能说成“主回复重复两次” |

`tool_schema_mode=skills_like` 会先用轻量工具表选择工具，再用参数 schema 额外重查一次；它可能省输入 Token，却会给每次工具选择增加 1～2 次模型请求。当前工具数量和延迟目标下不应直接启用，必须独立压测。

## 4. 当前缓存链路

### 4.1 Codex transport 当前请求形态

`codex_service._stream_transport_turn()` 当前会：

1. 从 system/developer 消息生成 `instructions`；
2. 将全部 AstrBot 历史转换为 Responses `input`；
3. 每一轮附带完整 `tools`；
4. 使用 `sha256(session_key)` 作为 `prompt_cache_key`；
5. 设置 `store=false`；
6. 不使用 `previous_response_id`，每轮完整重放历史。

当前代码注释记录了该 ChatGPT Codex transport 在第一次请求后拒绝 `previous_response_id` 的实际兼容问题，所以**本阶段不要重新打开 previous-response 续接**。官方 Responses 接口支持它做多轮状态，但 `store=false`/当前 ChatGPT transport 的兼容行为必须以实测为准；现在应先把“完整重放”做得稳定、精简、可缓存。

### 4.2 哪些内容必须稳定

缓存友好的顺序应固定为：

```text
稳定 instructions
  persona / 永久安全边界 / 固定输出约束 / 固定工具总规则

稳定 tools
  名称、描述、参数 schema、顺序完全一致

历史前缀
  已完成的 user/assistant/tool 消息，不反复改写旧消息

动态尾部
  当前消息 / L1-L3 检索 / 画像 / 好感度 / 当前图片索引 / 当前工具结果
```

以下行为会直接降低命中：

- 当前时间、注意力、情绪、用户 ID 等实时值写进 `system_prompt`；
- 同一组 tools 每次顺序不同、描述中含时间/状态、同名 schema 不一致；
- 每轮重新格式化历史，导致空格、换行、JSON key 顺序变化；
- DecisionAI 与主回复使用相同 `prompt_cache_key`，但前缀完全不同；
- 相同 persona/tools 的不同群使用完全不同的 session key，无法进入同一相似请求分桶；
- 把完整工具清单同时放入 `tools` 和动态文本；
- 把同一 `extra_user_content_parts` 发送两次。

官方 OpenAI 文档说明 `prompt_cache_key` 用于相似请求缓存；GPT-5.6 还支持 `prompt_cache_options` 和显式缓存断点。正式启用显式模式前，必须先确认 ChatGPT Codex transport 接受这些字段，并记录 cache write/read，而不是在生产链路上直接试错。参考：

- <https://developers.openai.com/api/reference/cli/resources/responses/methods/create>
- <https://developers.openai.com/api/docs/guides/latest-model>

## 5. 已确认的实现问题

### 5.1 P0：动态上下文在 Codex provider 中重复

当前路径：

1. `ProviderRequest.assemble_context()` 已把 `prompt + extra_user_content_parts` 组装成最后一条 user message；
2. Runner 把这条 message 放进 `run_context.messages`；
3. provider `_normalize_request_inputs()` 又从最后一条 user message 提取文本作为 `latest_prompt`；
4. `build_input_items()` 在追加 `latest_prompt` 后，再把原 `extra_user_content_parts` 包入 `<astrbot_dynamic_context>`。

结果：文本请求中，动态块在 `latest_prompt` 内一份、`<astrbot_dynamic_context>` 内又一份。工具续轮时，第二份还会出现在最新尾部，增加 Token 并可能让模型误以为用户再次提供了相同记忆/规则。

### 5.2 P0：工具提醒与 tools schema 重复

Group Chat Plus 已经把 `req.func_tool` 交给 provider，`response_request()` 会发送 Responses `tools`。同时 `enable_tools_reminder=true` 又把名称、说明以及 full 模式下的参数信息格式化进临时用户上下文。

这会带来：

- 工具描述双份输入；
- schema 更新后两份文本可能不同步；
- 工具清单放在动态尾部，几乎每轮都是未缓存输入；
- 工具较多时，主回复和每个工具续轮都被放大。

### 5.3 P0：缓存 key 与请求类型错位

`prompt_cache_key=sha256(session_key)` 的问题：

- 同一会话的 DecisionAI、主回复、图片转述前缀不同，却可能共用 key；
- 不同会话的 persona、工具、固定规则完全相同，却一定使用不同 key；
- 无法直接通过 key 判断某次前缀变化来自 persona、tools 还是 route。

### 5.4 P0：异常轮次上限过高

`max_agent_step=30`，而 24 小时样本没有任何一次触顶。Core 只在相同工具+相同参数连续第 3/4/5 次时追加文字提醒，不会硬阻断重复副作用。

### 5.5 P1：多工具调用顺序执行

Responses payload 已设置 `parallel_tool_calls=true`，模型确实会一轮返回多个工具；但 `_handle_function_tools()` 使用顺序 `for`，没有 `asyncio.gather`。

不能全量改成并发：

- `web_search`、只读查询、互不依赖的文件读取可并发；
- `send_meme`、发图片、改状态、写数据库、偷表情包、删除/更新类工具有顺序和副作用，必须串行；
- 多个工具结果回填顺序必须与原 `tool_call_id` 顺序一致，否则模型可能关联错误。

### 5.6 P1：工具结果允许过大

Core 的通用溢出阈值约为 27,500 estimated tokens，预览仍可达 7,000。对 QQ 聊天 bot 来说过大：一次网页搜索原文就足以破坏后续所有工具轮次的缓存和延迟。

应在每个工具自己的边界先返回结构化摘要，把完整结果存为 artifact/缓存文件；Core 的超大结果落盘只当最后保险。

### 5.7 P1：感知延迟与实际延迟混在一起

当前 `streaming_response=false` 是为消息分段、输出审计和 pre-send 兼容保留的合理设置。直接打开可见流式输出可能重新引入：

- 分段插件拿不到完整文本；
- output audit 无法在发送前审阅完整候选；
- 用户看到工具前的中间文本或重复片段。

优化响应速度时，应先降低真实 provider/tool 耗时，再用“正在看图/检索”等非 LLM 状态改善感知，流式输出放到兼容性专项 A/B。

## 6. P0：第一阶段 TODO（低风险、高收益）

### P0-0 建立可比较的 Agent Loop 指标

- [ ] 为每个进入主回复的消息生成 `request_id`，只记录随机 ID，不记录 QQ 原文。
- [ ] 给所有模型调用标记 `route`：`main`、`decision`、`proactive_judge`、`vlm_caption`、`iris_background`、`compress`、`other`。
- [ ] 主 Agent 每轮记录 `round_index`；独立 DecisionAI 不得伪装成 round 0/1。
- [ ] 记录 `instructions_hash/chars`、`tools_hash/bytes/count`、`history_items/chars`、`dynamic_hash/chars/count`。
- [ ] 记录 `input_cached`、`input_other`、`output`、cache write tokens、模型、reasoning effort。
- [ ] 记录 queue wait、hook 总耗时、Iris 各阶段耗时、provider TTFT/总耗时、每个工具耗时、最终发送耗时。
- [ ] 记录 `retry_count`、`fallback_provider`、`error_class`、`final_response_sent`。
- [ ] 默认禁止记录 prompt、persona、记忆正文、工具参数原文、图片 base64、URL 凭据和 API key。
- [ ] 报表按 `route` 分开展示 24h 的调用数、加权缓存命中、P50/P95 延迟和错误率。

建议落点：

- provider 指标：`astrbot_plugin_chatgpt_codex/codex_service.py`
- payload 指纹：`astrbot_plugin_chatgpt_codex/transport/responses.py`
- 主 Agent request/round 关联：优先通过 provider 接收到的 `session_id + turn-local request_id` 完成；只有确实缺字段时才做版本化 Core 补丁。

验收：

- [ ] 一个无工具主回复显示 `main round=0` 且只有一次 main provider 调用。
- [ ] 一个单工具回复显示 `main round=0/1`，每轮 Token 和延迟可区分。
- [ ] DecisionAI 显示为独立 `decision`，不计入主 Agent round。
- [ ] 随机抽查日志不包含用户消息和 persona 原文。

### P0-1 修复 `extra_user_content_parts` 重复发送

- [ ] 修改 `_normalize_request_inputs()`，返回“最新 user message 是否来自 Runner 已组装 context”的显式标记。
- [ ] 若最后一条 context 已经包含本次 extra parts，则不得再向 `build_input_items()` 传入同一批 extras。
- [ ] 不做全局文本去重；只对本次由 AstrBot 组装产生的 extra parts 做来源明确的去重，避免误删用户真的重复说的话。
- [ ] 工具续轮不得在 tool result 之后再次插入原始动态上下文 user message。
- [ ] 保持直接 SDK 调用 `prompt + extra_user_content_parts` 的原行为：当 contexts 中没有组装后的 extras 时仍正常追加一次。
- [ ] 多模态 message 不能因为去重而丢图片、音频、reply/file/video part。

修改文件：

- `plugins/modified/astrbot_plugin_chatgpt_codex/agent_provider.py`
- `plugins/modified/astrbot_plugin_chatgpt_codex/transport/responses.py`
- `plugins/modified/astrbot_plugin_chatgpt_codex/tests/test_agent_provider.py`
- `plugins/modified/astrbot_plugin_chatgpt_codex/tests/test_transport.py`
- `plugins/modified/astrbot_plugin_chatgpt_codex/tests/test_cache_optimization.py`

必须新增的测试：

- [ ] 纯文本 + Iris/affection extras：每个动态标记只出现 1 次。
- [ ] 图片 + 描述/index extras：图片只出现 1 次，动态文本只出现 1 次。
- [ ] 一次工具调用后的续轮：原动态块仍只有 1 次，且位于最初用户消息内，不在 tool result 后重发。
- [ ] 直接 provider 调用、contexts 不含 extras：extras 正常出现 1 次。
- [ ] 两段内容相同但来源不同的真实用户消息不会被误删。

回滚：provider 配置增加短期兼容开关 `deduplicate_runner_extras`，默认先在测试环境开启；一周稳定后再移除旧路径。

### P0-2 去掉工具提醒全文重复

- [ ] 测试环境将 Group Chat Plus `enable_tools_reminder=false`。
- [ ] 保留实际 Responses `tools` schema 和一次固定 `TOOL_CALL_PROMPT`。
- [ ] 保留只包含跨工具通用规则的短提示，例如“按需调用；有副作用前确认权限”；不要再列工具名、参数和描述全文。
- [ ] 若 persona 需要隐藏一部分工具，应直接过滤 `req.func_tool`，不能只在提醒文本里隐藏。
- [ ] 工具描述的“何时用、输入、副作用、重试安全”以 schema description 为唯一权威来源。
- [ ] 建立 20 条工具选择回归集：搜索、表情包查找/发送/偷取、星图解析/发送标注图、状态修改、无工具闲聊。

验收：

- [ ] payload 中不再出现 `=== 可用工具列表 ===`，但 `tools` 数量不变。
- [ ] 20 条回归的正确工具选择率不下降超过 1 条。
- [ ] 无工具闲聊不误调用工具。
- [ ] `tools_schema_bytes + dynamic_context_chars` 明显下降。

### P0-3 重新设计 prompt cache key

- [ ] 增加稳定的 `route_namespace`，至少区分 `main/decision/proactive/vlm/compress/background`。
- [ ] 对工具 schema 做 canonical JSON：固定字段顺序、`sort_keys=True`、固定 separators、工具按稳定 name 排序。
- [ ] 计算：`sha256(cache_format_version + model + route + instructions_hash + tools_hash)`。
- [ ] 将 64 位 hex 作为 `prompt_cache_key`；不包含 QQ、群号、用户名、明文 persona 或工具参数。
- [ ] key 变化时记录是哪一项 hash 变化，但不记录内容。
- [ ] 同 persona/tools/model/route 的不同群应得到同一 cache family；DecisionAI 与主回复必须不同。
- [ ] persona、固定规则或工具 schema 真正更新后，key 必须变化，防止误用旧前缀。
- [ ] 对同一 key 的请求跟踪 cache write/read，避免高频写入却很少读取。

修改文件：

- `plugins/modified/astrbot_plugin_chatgpt_codex/codex_service.py`
- `plugins/modified/astrbot_plugin_chatgpt_codex/transport/responses.py`
- `plugins/modified/astrbot_plugin_chatgpt_codex/tests/test_cache_optimization.py`

验收：

- [ ] 重复构造 100 次，相同输入的 key 字节完全一致。
- [ ] 改当前用户名/好感度/L2 检索结果，main cache key 不变。
- [ ] 改 persona 或工具 schema，key 必须变化。
- [ ] main 与 decision 的 key 不同。
- [ ] key 长度不超过官方限制 64 字符。

### P0-4 限制工具循环和重复副作用

- [ ] 将 `max_agent_step` 从 30 调为 8。
- [ ] round 到 5 时告警，包含 request_id、tool name 和耗时，不记录参数正文。
- [ ] 相同工具 + canonical args 连续第 2 次时区分工具类型：
  - 只读且结果仍有效：返回 request-local 缓存结果；
  - 副作用工具：不再次执行，返回“本请求已执行过”的 tool result；
  - 明确声明可重复的工具：允许继续，但必须由工具 metadata 标记。
- [ ] 为发送/状态修改/写库工具生成 `idempotency_key=request_id+tool_name+args_hash`。
- [ ] `final_response_sent` 使用原子状态，fallback/retry/on_agent_done 不得重复发送。
- [ ] 达到最大轮次后拔掉 tools 生成一次最终总结，保持 Core 现有行为。

验收：

- [ ] 普通、表情包和星图 30 条回归全部在 5 个 Agent round 内结束。
- [ ] 相同 `send_meme`/状态修改参数不会在一个 request 内执行两次。
- [ ] 不同图片或不同 meme ID 的多个发送仍可按用户明确请求执行。
- [ ] 一周内无 max-step 触顶；若有，保留 trace 单独分析，不立即把上限改回 30。

## 7. P1：第二阶段 TODO（效率与延迟）

### P1-1 为工具声明执行类别

建议 metadata：

```yaml
execution_class: pure | read | write | send
parallel_safe: true | false
retry_safe: true | false
cache_ttl_seconds: 0
max_result_chars: 4000
```

- [ ] `web_search`、纯读取、独立元数据查询可设为 `read/parallel_safe`。
- [ ] `send_meme`、`send_annotated_star_field`、改状态设为 `send/parallel_safe=false`。
- [ ] `steal_meme`、数据库写入设为 `write/parallel_safe=false`。
- [ ] 未声明 metadata 的工具默认按最保守的 `write` 串行处理。
- [ ] 同一模型轮次中只对连续、互不依赖、全部 `parallel_safe=true` 的调用使用有界并发，2 核实例并发上限先设 3。
- [ ] `asyncio.gather` 完成后按原 tool_call 顺序回填，不能按完成时间排序。
- [ ] 单个工具异常只生成该 call 的 error result，不取消同批其他只读工具。

Core 改造约束：

- 这部分位于 AstrBot `ToolLoopAgentRunner._handle_function_tools()`，公共仓库当前不维护 AstrBot Core 源码，部署又使用 `latest` 镜像。
- 优先提交上游 PR；若必须自用，建立 `patches/astrbot/4.27.x/` 版本化补丁和启动时版本校验。
- 禁止用生产运行时 monkey-patch 静默覆盖 Runner。

### P1-2 工具结果瘦身与缓存

- [ ] `web_search` 默认最多 5 条，每条只保留 title、URL、短 snippet；正文按需二次读取。
- [ ] `search_meme` 返回稳定的 ID、短描述和评分，不把图片 base64/完整路径列表交给模型。
- [ ] `analyze_star_field` 返回面向模型的结构化摘要；jobid、标注图本地路径和远端 URL 存到 request artifact/index，需要发送时由工具用 ID 读取。
- [ ] 各工具设置自己的 `max_result_chars/max_result_tokens`，聊天工具建议先以 2,000 tokens 为软上限、4,000 为硬上限。
- [ ] 只读工具增加 request-local single-flight：相同 name+args 同时只执行一次。
- [ ] 跨请求缓存仅用于确定性读取：key 包含工具版本、canonical args、数据版本；TTL 到期自动失效。
- [ ] 任何 send/write/delete/status 工具不得使用“跳过执行直接复用成功结果”的跨请求缓存。
- [ ] Astrometry 按图片内容 SHA-256 缓存 solve 结果和标注图；同图再次请求直接复用，API key/算法版本变化时失效。
- [ ] VLM 图片描述按图片内容 hash + provider + prompt_version 缓存，避免相同表情包/图片重复走 VLM。

### P1-3 Iris 与场景注入预算

Iris 当前已经并行执行 L1、Profile、L2、Learning，并让 L3 只等待 L2；图片 related parse 也已移到后台。不要重新串行化这些阶段。

- [ ] L1 保留短近期对话，严格避免和 Group Chat Plus 40 条主上下文全文重复。
- [ ] Profile/L3/learning 使用版本号缓存；只有相关数据写入后才失效。
- [ ] L2 query rewrite/embedding 以 `normalized_query + memory_version + provider_version` 做短 TTL single-flight。
- [ ] 对单字、纯 @、戳一戳等低信息消息，先结合 Group Chat Plus 合并后的 3 秒窗口再检索，避免对每个碎片单独 embedding。
- [ ] 每个 section 有独立字符/Token 预算；为空时不输出标签。
- [ ] 在主 Agent 工具续轮中不重新检索 Iris；当前 Core 本来就不会重跑 hook，保持这一行为。
- [ ] 只有某个工具明确写入了新记忆且当前回答必须读取它时，才通过显式 `memory_refresh` 工具执行一次定向刷新。
- [ ] Iris 运行日志默认只保存 section 长度、耗时、hash 和命中 ID；正文日志仅在短期、受控调试中开启。

### P1-4 图片上下文保持“索引多、原图少”

- [ ] 图片池继续保留 30 条 metadata/description/index。
- [ ] 默认模型请求只回放 0～1 张原图；只有用户明确说“这些/全部/前几张”才增加。
- [ ] 首次 VLM 描述写入图片 ID，后续只用 `[IMG-xxxx] + 描述` 引用。
- [ ] Agent 每轮不得重复注入 30 张图片 base64。
- [ ] Astrometry/标注图等生成图片作为 artifact 保存；模型只拿 ID、摘要和“可按需发送”能力说明。
- [ ] 媒体文件不存在时返回可恢复错误和可用图片 ID，不让 Agent 对同一路径盲重试。

### P1-5 按 route 调整推理强度和输出预算

当前 Codex provider 使用全局 `reasoning_effort=auto`，`text_chat()` 会丢弃额外 kwargs，无法为 DecisionAI 单独调低推理。

- [ ] provider 支持可选的 per-request `reasoning_effort`，缺省仍使用全局设置。
- [ ] `decision/proactive_judge/frequency` 从 `none` 或 `low` 开始 A/B，输出限制为结构化 yes/no + 短 reason。
- [ ] `vlm_caption` 使用短输出预算，不生成长篇人格回复。
- [ ] 主聊天从 `low` 作为延迟基线；只有复杂搜索、长分析或明确高质量任务再升到 `medium`。
- [ ] 每种 route 记录质量通过率、Token 和 P95，不能只看速度。
- [ ] 不在同一次实验同时更换模型、reasoning、prompt 和上下文预算。

### P1-6 改善感知速度但保持完整输出审计

- [ ] 暂时保持 `streaming_response=false`。
- [ ] 工具预计超过 3 秒时发送平台原生“输入中”状态或一条可撤回的简短状态，不调用额外 LLM。
- [ ] 星图解析显示“正在解算”，完成后只发送 persona 化最终回复和按需图片。
- [ ] 状态消息与最终回复使用同一 request_id，取消/防抖时及时撤销或更新。
- [ ] 单独建立 streaming A/B 分支，验证 Smart Segmentation、Output Audit、Group Chat Plus、SnowLuma 全链路后再考虑开启。

## 8. P2：实验项，不直接上生产

### P2-1 GPT-5.6 显式 prompt caching

- [ ] 为 transport 增加 feature flag，先探测 endpoint 是否接受 `prompt_cache_options` 和内容块断点。
- [ ] 先用 `implicit` 作为对照，再测试 `explicit + ttl=30m`。
- [ ] 断点候选 1：稳定 instructions 结束。
- [ ] 断点候选 2：稳定 tools 结束。
- [ ] 历史/动态尾部不设置固定断点，避免高频 cache write。
- [ ] 同时记录 cache write tokens、cached tokens、延迟和总成本；只提高“命中百分比”但大量付费写缓存不算成功。
- [ ] endpoint 返回 400 时立即关闭 feature flag，不允许自动再发一份不同 payload 后把两份都送到用户。

### P2-2 `previous_response_id` / persisted reasoning

- [ ] 当前生产保持关闭。
- [ ] 仅在隔离环境验证 ChatGPT Codex transport 对 `store=false`、encrypted reasoning replay、previous response 的真实支持。
- [ ] 验证 persona/tools/动态记忆更新后的失效语义。
- [ ] 验证重启、会话迁移、response 过期、fallback provider 和隐私策略。
- [ ] 只有完整 100 条回归无上下文丢失、无 400、无重复回复后才讨论生产启用。

### P2-3 skills-like 工具 schema

- [ ] 对 `full` 与 `skills_like` 做相同 50 条工具任务对比。
- [ ] 记录 schema bytes、模型调用数、总 Token、P50/P95、工具参数正确率。
- [ ] 如果节省输入不足以抵消额外 requery 延迟，就继续使用 `full`。
- [ ] 更优的中间方案是按意图暴露工具子集，而不是为所有工具增加第二次模型选择。

### P2-4 Programmatic Tool Calling

- [ ] 仅评估“多个只读搜索/过滤/聚合”这类有界工作流。
- [ ] 发送、状态修改、写记忆、偷表情包等副作用工具不进入 programmatic 批处理。
- [ ] 先确认 ChatGPT Codex transport 是否支持相关 output item 和 caller/call_id 关联。
- [ ] 必须对比最终任务成功率；轮次更少但结果不完整不能视为优化。

## 9. 不建议做的事

- [ ] 不重新把 L1/L2/L3、画像、好感度和当前状态塞回 `system_prompt`。
- [ ] 不因为缓存低就删除 persona、Iris 或 ContextAware 的核心能力。
- [ ] 不直接启用 `previous_response_id`，当前 transport 已有实际拒绝记录。
- [ ] 不把全部工具无条件并发；副作用工具会导致重复发送和状态竞态。
- [ ] 不把 `max_agent_step` 降到 1～2；正常工具协议本来就需要选择工具和读取结果两轮。
- [ ] 不把 DecisionAI 的请求算作主 Agent 重复轮次。
- [ ] 不只看“请求数”判断防抖是否失效，必须关联 message_id/request_id/route。
- [ ] 不通过运行时 monkey-patch 修改 `ToolLoopAgentRunner`；使用上游 PR 或有版本校验的补丁。
- [ ] 不在同一天同时改缓存 key、工具 schema 模式、reasoning、persona 和消息窗口。

## 10. 验收测试矩阵

### 10.1 功能回归

| 用例 | 预期 |
|---|---|
| 普通群聊文字 | Decision 可选 1 次，主 Agent 1 次，不重复回复 |
| 连续发送 3 条 | 最后一条后约 3 秒进入一次合并主流程 |
| 普通图片 | 图片 ID/一次描述进入上下文，主回复看得到 |
| 表情包 | 有描述复用描述；无描述才调用一次 VLM |
| “把刚才那张发出来” | 通过图片 ID 回放 1 张，不注入整个 30 张池 |
| 搜索并发表情包 | search → send，发送一次，Agent 正常结束 |
| 同一发送参数重复 | 幂等保护阻止第二次副作用 |
| 星图解析 | 同图 solve 可缓存，结果进入上下文，标注图按需发送 |
| 一次多图 | 每张有独立 ID/描述/solve artifact，不互相覆盖 |
| 工具失败 | 返回一次可理解错误，不盲重试 30 轮 |
| 新消息打断 | 原 request 停止，合并后只产生一个最终回复 |
| 输出审计 | 仍在完整文本发送前执行，分段插件正常 |

### 10.2 payload 回归

- [ ] 每个 `<iris:...>` section 在单个请求 payload 中最多 1 次。
- [ ] Affection、ContextAware、ImageContextPool 标记各最多 1 次。
- [ ] `=== 可用工具列表 ===` 不再出现；Responses `tools` 正常存在。
- [ ] tools canonical hash 在没有插件启停/升级时保持稳定。
- [ ] 当前时间、好感度、用户 ID 变化不改变 instructions hash。
- [ ] round 1 只比 round 0 多 assistant tool call 和 tool result，不多一份原动态块。
- [ ] 图片 data URI 不在同一 payload 重复。

### 10.3 性能验收

以至少 24 小时、相近群活跃度为一个窗口：

| 阶段 | 目标 |
|---|---|
| P0-1 去重动态块 | uncached input 至少下降 15%，功能回归全过 |
| P0 完成 | 加权缓存命中从 19.39% 提升到至少 35% |
| P1 完成 | 加权缓存命中目标 50%，或 uncached input 相对基线下降至少 30% |
| 无工具主回复 | P50 总延迟下降至少 20%，provider 调用固定为 1 次 |
| 单工具任务 | 除必要两轮外无额外请求，P95 下降至少 20% |
| 多只读工具 | 并发版 wall time 比串行版下降至少 30% |
| 稳定性 | 重复最终回复 0、重复副作用 0、400 自动双发 0 |

命中率受请求长度门槛、流量重复度和服务端策略影响。若 24 小时样本太少，以 7 天加权值和 uncached input 绝对量为最终判断，不为追求百分比牺牲功能。

## 11. 实施批次

### 批次 A：先证明问题

- [ ] P0-0 指标字段和 route/round 关联。
- [ ] 抓取 20 个脱敏 payload 结构快照，确认 extra 重复率和 tools bytes。
- [ ] 固化 50 条功能/工具回归集。

### 批次 B：最大确定性收益

- [ ] P0-1 provider extra 去重。
- [ ] 单元测试、静态检查、容器内测试。
- [ ] 灰度一个测试会话 6 小时，再全量 24 小时。

### 批次 C：工具输入精简

- [ ] P0-2 关闭工具提醒全文。
- [ ] 保留 schema 和短通用规则。
- [ ] 跑 20 条工具选择回归并对比 tools/dynamic bytes。

### 批次 D：缓存分桶

- [ ] P0-3 cache family key。
- [ ] 主回复/Decision/VLM 分 route。
- [ ] 观察 cache write/read 和 24 小时命中。

### 批次 E：异常保护

- [ ] P0-4 最大步数 8、重复工具保护、最终发送幂等。
- [ ] 注入工具失败、超时、空结果和 fallback 测试。

### 批次 F：延迟专项

- [ ] P1 工具 metadata、只读并发、结果瘦身、内容 hash 缓存。
- [ ] P1 per-route reasoning effort。
- [ ] P1 非 LLM 状态提示。

### 批次 G：实验特性

- [ ] 显式 prompt caching。
- [ ] skills-like 对照。
- [ ] previous response / persisted reasoning 隔离验证。

## 12. 每批次交付物

- [ ] 变更前后配置 diff，不能包含密钥。
- [ ] 对应源码 commit，禁止把运行数据库、日志和 prompt 快照提交到 public repo。
- [ ] 单元测试与功能回归结果。
- [ ] 24 小时或 7 天指标对比：route、round、Token、缓存、P50/P95、错误率。
- [ ] 回滚命令和上一个可用镜像/插件版本。
- [ ] 更新本文件复选框和结论，不只在聊天中口头说明。

## 13. 建议的第一组实际改动

下一次实施直接从以下四项开始，暂不碰 AstrBot Core：

1. 在 Codex provider 的 payload diagnostics 增加 `route/round/dynamic_occurrences/tools_hash`；
2. 修复 `extra_user_content_parts` 重复，并补齐 5 类测试；
3. 测试配置关闭 Group Chat Plus 工具提醒全文；
4. 将 `max_agent_step` 调到 8。

这四项都能在现有插件/配置层完成，可独立回滚，也不会改变 persona、Iris 记忆能力、图片池容量或消息分段策略。完成并取得 24 小时数据后，再决定 cache family key 和 Core 工具并发的具体实现。
