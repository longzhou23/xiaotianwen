# P0 Hook 与直接 LLM 调用审计

> 生成方式：`pwsh ./scripts/test-local.ps1 -Profile audit` 或 `bash ./scripts/test-local.sh audit`
> 范围：`plugins/modified/`、`plugins/upstream/`、`plugins/retired/` 内的 Python 源码 AST
> 安全边界：不导入、不启动插件；不读取实例配置、请求正文、persona、记忆、工具参数或凭据。

## 当前静态快照

本快照基于 2026-08-31 的公共工作树扫描。扫描到 56 个 Hook 声明和 25 处直接 `text_chat()`、`text_chat_stream()`、`llm_generate()` 或 `request_llm()` 调用：

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

## 当前关键顺序风险

| 生命周期 | 关键声明 | 已知 priority | P0 关注点 |
|---|---|---:|---|
| 事件入口 | Group Chat Plus `command_filter_handler` | 动态 | 需与 Debounce、Recall Cancel 的实际顺序隔离回放 |
| 事件入口 | Recall Cancel `on_all_message` | `100` | 新消息取消与迟到输出不能造成第二次发送 |
| 等待请求 | Debounce `on_waiting_llm_request` | `100` | 只能合并/等待，不能额外创建主回复 |
| LLM 请求 | Debounce `on_llm_request` | `100` | 记录 route 与 request owner |
| LLM 请求 | AntiPromptInjector 最终检查 | `999` | 记录安全边界，不能记录原始 prompt |
| LLM 请求 | Output Audit `disable_streaming_for_audit` | `90` | 审核前禁用流式必须可观测 |
| LLM 请求 | ContextAware | 动态 | 记录 section 来源、字符数与 hash |
| LLM 请求 | ImageContextPool | 动态 | 已有摘要时 VLM 调用数必须为零 |
| LLM 请求 | Group Chat Plus 收束 | 动态 | 不应依赖未记录的加载顺序恢复字段 |
| LLM 响应 | Smart Segmentation | 默认 | 不得增加 LLM 请求 |
| 装饰结果 | Output Audit | 动态 | 必须在最终发送之前看到 allow/revise/block |
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

## 未完成的运行时证据

- 当前启用插件清单、最终加载顺序、相同 priority 的 tie-breaker；
- 各 Hook 读取/写入字段的完整所有权表；
- Debounce 与 Recall Cancel 的真实共享状态和实际取消时机；
- Group Chat Plus `priority=-100000` 收束路径的字段恢复行为；
- AntiPromptInjector 与 Output Audit 在真实 ProviderRequest/结果链中的边界；
- 每条直连 LLM 调用实际属于主回复、Decision、VLM、Iris 后台还是工具续轮。

这些事项必须在隔离 AstrBot 回放中继续验证；不得为获取这类证据直接切换生产主回复路径。
