# P0 Hook 与直接 LLM 调用审计

> 生成方式：`pwsh ./scripts/test-local.ps1 -Profile audit` 或 `bash ./scripts/test-local.sh audit`
> 范围：`plugins/modified/`、`plugins/upstream/`、`plugins/retired/` 内的 Python 源码 AST
> 安全边界：不导入、不启动插件；不读取实例配置、请求正文、persona、记忆、工具参数或凭据。

## 当前静态快照

本快照基于 2026-09-02 的公共工作树扫描。扫描到 56 个 Hook 声明、56 份 Hook 字段/副作用记录和 25 处直接 `text_chat()`、`text_chat_stream()`、`llm_generate()` 或 `request_llm()` 调用：

| 类别 | 数量 |
|---|---:|
| `on_llm_request` | 16 |
| `event_message_type` | 9 |
| `on_llm_response` | 8 |
| `on_decorating_result` | 8 |
| `after_message_sent` | 7 |
| `platform_adapter_type` | 4 |
| `on_astrbot_loaded` | 2 |
| `on_waiting_llm_request` | 2 |

| 目录 | Hook 数量 |
|---|---:|
| `plugins/modified/` | 20 |
| `plugins/upstream/` | 32 |
| `plugins/retired/` | 4 |

静态命中不代表插件当前已启用，也不代表调用就是主回复。运行时仍必须按 `route`、`owner`、`request_id`、`call_id` 和最终 delivery 观察；P1/P2 不能将本文件当作真实加载顺序或完整调用图的证据。

P2 新增的声明式字段/所有权清单位于
`plugins/modified/astrbot_plugin_xiaotianwen_orchestrator/p2/hook_contract.py`。
它把当前 legacy Hook 与隔离目标 assembler 分开记录，并将 Group Chat Plus
兼容 Hook 默认标为关闭；这份清单仍不能替代隔离 AstrBot 的运行时顺序验证。

## 当前关键顺序风险

| 生命周期 | 关键声明 | 已知 priority | P0 关注点 |
|---|---|---:|---|
| 事件入口 | Group Chat Plus `command_filter_handler` | 动态 | 需与 Debounce、Recall Cancel 的实际顺序隔离回放 |
| 事件入口 | Recall Cancel `on_all_message` | `100` | 新消息取消与迟到输出不能造成第二次发送 |
| 等待请求 | Debounce `on_waiting_llm_request` | `100` | 只能合并/等待，不能额外创建主回复 |
| LLM 请求 | Debounce `on_llm_request` | `100` | 记录 route 与 request owner |
| LLM 请求 | AntiPromptInjector 最终检查 | `999` | 记录安全边界，不能记录原始 prompt |
| LLM 请求 | Output Audit `disable_streaming_for_audit` | `90` | 审核前禁用流式必须可观测 |
| LLM 请求 | ContextAware | `-10` | 记录 section 来源、字符数与 hash |
| LLM 请求 | ImageContextPool | `-20` | 已有摘要时 VLM 调用数必须为零 |
| LLM 请求 | AntiPromptInjector 输入检查 | `-1000` | 可改写 prompt、直接发送拒绝并停止事件 |
| LLM 请求 | Group Chat Plus 收束 | `-100000` | 恢复/合并请求字段，是 legacy 最终写入者 |
| LLM 响应 | Smart Segmentation | 默认 | 不得增加 LLM 请求 |
| 装饰结果 | Output Audit | `-90` | 必须在最终发送之前看到 allow/revise/block |
| 装饰结果 | Group Chat Plus / Stealer | 默认 / `100` | 需确认只有一个最终 delivery owner |
| 发送后 | Smart Segmentation | `200` | 多段发送应以 idempotency key 追踪 |

## 已发现的独立 LLM 调用类别

以下为重点路线，不表示所有调用都会在一次用户意图中同时发生：

- `ContextAware`：历史压缩、图片描述；
- `Group Chat Plus`：DecisionAI、主动聊天、私聊/群聊回复、图片转文本；
- `Output Audit`：review、rewrite、safe fallback；
- `AntiPromptInjector`：注入审查；
- `Iris Memory`：记忆生成与直接文本调用；
- `Affection`：无意识分析；
- `Shared Context`、`Smart Segmentation`、`Stealer`：图片描述、分段、情绪/VLM 分析；
- `ChatGPT Codex Provider`：`text_chat`/`text_chat_stream` Provider 接口实现。

## 使用方式

完整表会在本地源码变化后重新生成；运行命令只执行 AST 扫描，不导入任何插件：

```powershell
pwsh ./scripts/test-local.ps1 -Profile audit
```

```bash
bash ./scripts/test-local.sh audit
```

生成后应审查新增 Hook、priority 漂移和新的直连 LLM 调用，并为它们补充 owner/route/副作用分类。若 Hook 名称、priority 或调用路径有变化，P0 回放 fixture 和 Local Test Console 的观测标签也必须同步更新。

## 2026-09-02 关键边界结论

已在 Azure 当前 AstrBot 容器中只读核对 `astrbot/core/star/star_handler.py`：
handler 按 `priority` **降序**执行；相同 priority 依赖稳定排序保留的注册顺序。
因此源码里的负数 priority 不能再记作“动态”，AST 审计已修正对负整数字面量的识别。

### Debounce 与 Recall Cancel

- Debounce 的 `on_waiting_llm_request(100)` 在 session lock 前维护自己的 session/message 集合；`on_llm_request(100)` 只停止、缓冲或放行事件，不写 `ProviderRequest` 字段。
- Recall Cancel 的 recall notice 入口是 `event_message_type(100)`；它以 message ID + UMO 维护独立状态。其 `on_llm_request(100)` 同样不写请求字段，只登记 pending request，并在已撤回时停止事件。
- 两者没有共享插件内可变容器；共同边界只有 AstrBot event 的停止状态和平台请求生命周期。由于两个 `on_llm_request` priority 相同，tie-breaker 仍是当前插件注册顺序，但不存在请求字段“后写覆盖前写”。升级后若任一方新增请求字段写入，fingerprint gate 会失败。

### Group Chat Plus 收束字段

`Group Chat Plus.on_llm_request(-100000)` 是当前 legacy 请求的最终收束写入者。机器审计确认它会读取/写入或合并：

- `prompt`、`system_prompt`、`contexts`；
- `extra_user_content_parts`；
- `image_urls`、`audio_urls`；
- `func_tool`。

它不直接发送消息，但会清理本轮使用的 event extras。P1/P2 在切换 assembler owner 前必须保证这些字段逐项有替代者；不能只替换 prompt/contexts 后就关闭兼容 Hook。

### AntiPromptInjector 与 Output Audit

- AntiPromptInjector 只作用于 `on_llm_request`。当前所谓“最终检查” priority 为 `999`，按 AstrBot 的降序规则实际早于 `90/-1000/-100000`，因此名称不等于真正的链尾检查；输入拦截函数还可能直接 `event.send()` 并停止事件。
- Output Audit 在 `on_llm_request(90)` 关闭流式，在 `on_decorating_result(-90)` 修改完整候选结果；它不调用 `event.send()`，保留 AstrBot 单一正常发送路径。
- 两者并不重叠：前者是输入风险/请求改写边界，后者是最终自然语言候选输出边界。AntiPromptInjector 的直接拒绝发送仍可能绕过 persona 化输出与 Output Audit，必须在 P2 安全链迁移时消除，不能把当前 priority 布局视为最终安全架构。

机器可检验部分位于 `tests.harness.hook_audit.scan_hook_effects()`；它只记录字段名、event extra key 和 stop/send/replace 布尔值，不记录任何正文或参数值。

## 未完成的运行时证据

- 当前启用插件清单、最终加载顺序、相同 priority 的 tie-breaker；
- 各 Hook 读取/写入字段的完整所有权表；
- Debounce 与 Recall Cancel 在 recall 与新消息同时发生时的真实竞态回放；
- Group Chat Plus `priority=-100000` 字段恢复在真实 ProviderRequest 上的逐字段值级差异；
- AntiPromptInjector 直接拒绝发送迁移到 persona 化单一 delivery gate 后的真实链路；
- 每条直连 LLM 调用实际属于主回复、Decision、VLM、Iris 后台还是工具续轮。

这些事项必须在隔离 AstrBot 回放中继续验证；不得为获取这类证据直接切换生产主回复路径。
