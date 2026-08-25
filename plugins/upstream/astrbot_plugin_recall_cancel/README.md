# AstrBot 消息撤回取消插件

> **作者**: 木有知  
> **仓库**: [https://github.com/muyouzhi6/astrbot_plugin_recall_cancel](https://github.com/muyouzhi6/astrbot_plugin_recall_cancel)  
> **版本**: v2.1.3
> **标签**: 消息管理 | 撤回处理 | LLM控制 | 用户体验 | 自动化

## 📋 功能说明

当用户撤回触发 LLM 回应的消息时，如果 LLM 的回复还没发送出去，插件会自动取消发送。

**v2.1.3 新特性：**
- 🔗 **context_aware 联动**：撤回时同步清理 context_aware 插件中的消息记录，防止已撤回消息污染上下文
- 🔧 **完全重构**：修复消息 ID 匹配问题，确保撤回检测准确可靠
- ⚡ **兼容撤回生态**：优先记录并取消目标 LLM/Agent 事件，但不再截断撤回通知本身，避免影响防撤回、群管等插件
- 🛡️ **并发安全清理 Bot 回复**：默认仅在确认本次 LLM 回复对应的 context_aware 记录仍是最后一条 Bot 回复时才删除，避免误删旧回复或并发新回复
- ⚙️ **配置面板支持**：新增 `_conf_schema.json`，可在 AstrBot WebUI 中调整记录保留时间、清理间隔和 context_aware 清理策略

**解决场景：**
- 🚫 用户发错消息后撤回，但机器人仍然回复
- 🧹 撤回的消息不应出现在 LLM 的上下文历史中
- 🛡️ 防止恶意用户发送大量消息后撤回导致的资源浪费
- ⚡ 提升用户体验，避免无效回复

## 🔧 工作原理

```
用户发消息 → LLM 开始处理 → 用户撤回 → 插件拦截 → 取消回复 + 清理上下文
```

1. **📝 跟踪 LLM 请求**：监听 LLM 请求开始，记录消息 ID 和事件引用
2. **👂 监听撤回事件**：实时监听 QQ 平台的消息撤回通知事件（高优先级）
3. **❌ 智能取消**：检测到原消息被撤回时，立即停止正在处理的事件
4. **🔗 上下文清理**：如果安装了 context_aware 插件，同步删除已记录的消息
5. **🧹 自动清理**：定期清理过期记录，防止内存泄漏
6. **🤝 继续分发**：撤回通知继续向后传播，保证 anti_revoke、qqadmin 等插件仍能收到同一事件

## 🔌 支持平台

| 平台 | 状态 | 说明 |
|------|------|------|
| QQ (OneBot V11/NapCat) | ✅ 支持 | 完整支持群聊和私聊撤回检测 |
| Telegram | ❌ 不支持 | Telegram 撤回消息后无法获得原消息 ID |
| QQ 官方 | ❌ 不支持 | 官方 API 暂不提供撤回事件通知 |

## 🔗 context_aware 插件联动

如果同时安装了 [astrbot_plugin_context_aware](https://github.com/muyouzhi6/astrbot_plugin_context_aware) 插件（v2.5.1+），本插件会自动：

1. **删除用户消息**：从 context_aware 的会话历史中删除被撤回的消息
2. **按策略删除 Bot 回复**：默认 `safe` 策略只在确认本次回复对应的 context_aware Bot 记录仍是最后一条时删除，避免误删会话里更早或更新的不相关 Bot 回复

这确保了撤回的消息不会出现在后续的 LLM 上下文中，保持对话历史的准确性。

**注意**：即使不安装 context_aware，本插件也能正常工作（仅取消回复，不清理上下文）。

Bot 回复清理策略说明：

| 策略 | 行为 | 建议 |
|------|------|------|
| `off` | 不删除 context_aware 中的 Bot 回复 | 最保守，适合只想删除用户消息的场景 |
| `safe` | 仅在本插件确认本次 LLM 响应对应记录仍是最后一条 Bot 回复时删除 | 默认值，推荐使用 |
| `legacy_last` | 只要检测到撤回就尝试删除最后一条 Bot 回复 | 兼容旧行为，可能误删同会话最近一条不相关 Bot 回复 |

## ⚙️ 配置

本插件**开箱即用**，安装后自动生效。v2.1.2 起提供 AstrBot 配置面板支持，可按需调整：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `record_expire_seconds` | `300` | 待处理请求和撤回记录保留时间，单位秒 |
| `cleanup_interval` | `60` | 后台清理过期记录的间隔，单位秒 |
| `context_aware_cleanup` | `true` | 是否联动清理 context_aware 上下文 |
| `remove_bot_response_policy` | `safe` | context_aware Bot 回复清理策略，可选 `off`、`safe`、`legacy_last` |

## 🧪 调试与测试

### 状态查询指令

发送 `/recall_stats` 查看插件运行统计：

```
📊 撤回取消插件统计
━━━━━━━━━━━━━━━━━━━━
检测撤回: 5 次
阻止请求: 3 次
阻止响应: 1 次
阻止发送: 1 次
清理用户上下文: 4 次
清理Bot上下文: 1 次
━━━━━━━━━━━━━━━━━━━━
Bot回复清理策略: safe
上下文联动: 开启
当前待处理: 2 条
当前撤回记录: 3 条
```

### 功能测试步骤

1. **环境确认**：确保 QQ 客户端（如 NapCat）已正确连接
2. **触发测试**：发送能触发 LLM 回复的消息（如 `你好`）
3. **执行撤回**：在 LLM 回复发送前快速撤回原消息
4. **观察结果**：确认 AstrBot 停止发送回复
5. **状态验证**：使用 `/recall_stats` 查看插件工作状态

## 🔬 技术细节

### 实现架构

- **多层拦截**：在 `on_llm_request`、`on_llm_response`、`on_decorating_result` 三个阶段检查撤回状态
- **事件引用**：保存原始事件引用，支持跨 Task 停止事件处理
- **分层优先级**：撤回和阻断钩子使用 `priority=100` 提前执行，`on_llm_response_recorded(priority=-100)` 在 context_aware 默认钩子之后标记 Bot 回复可安全清理
- **消息 ID 提取**：正确从 `raw_message.message_id` 获取被撤回消息的真实 ID
- **发送前即时阻断**：在 `on_decorating_result` 阶段直接停止事件，不再人为 `sleep` 等待，减少竞态窗口
- **上下文安全清理**：context_aware 的 Bot 回复在 `on_llm_response` 阶段记录，插件默认通过 Bot 记录 ID、响应时间和会话级响应序号复核目标仍是最后一条后再删除

### 已知边界

- 如果平台或 AstrBot 已经把 streaming 片段发送出去了，本插件只能阻止后续生成/发送，不能追回已经发出的片段。
- 默认 `safe` 策略不会在无法确认本次 Bot 回复仍是 context_aware 最后一条 Bot 记录时删除 Bot 回复；如果你确实想恢复旧行为，请改为 `legacy_last`。

### 性能优化

- **内存管理**：定期清理过期记录（默认 5 分钟后过期）
- **异步处理**：所有操作均为异步，不阻塞主流程
- **线程安全**：使用 `asyncio.Lock` 保护共享状态

### 数据结构

```python
PendingRequest:  # 正在处理的 LLM 请求
  - message_id: str      # 原始消息 ID
  - unified_msg_origin: str  # 会话标识
  - sender_id: str       # 发送者 ID
  - timestamp: float     # monotonic 请求时间戳
  - event: AstrMessageEvent  # 事件引用
  - llm_response_preview: str | None  # 对齐 context_aware 记录的回复预览
  - llm_response_started_at: float | None  # LLM 响应开始 wall time
  - llm_response_sequence: int  # 会话级 LLM 响应序号
  - context_bot_msg_id: str | None  # context_aware 中对应 Bot 记录 ID

RecalledMessage:  # 已撤回的消息
  - message_id: str      # 被撤回消息 ID
  - unified_msg_origin: str  # 会话标识
  - operator_id: str     # 撤回操作者 ID
  - timestamp: float     # monotonic 撤回时间戳
  - context_user_removed: bool  # 用户消息是否已从 context_aware 删除
  - context_bot_removed: bool  # Bot 回复是否已从 context_aware 删除
```

## 📚 版本历史

### v2.1.3 (2026-05-23)
- 🛡️ **并发安全清理**：`safe` 策略通过 Bot 记录 ID、响应时间和会话级响应序号复核目标仍是最后一条后再删除
- 🧪 **竞态测试补齐**：覆盖并发新回复、同内容新回复、context_aware 已记录但低优先级钩子未执行等场景

### v2.1.2 (2026-05-23)
- 🛡️ **安全清理**：默认使用 `safe` 策略清理 context_aware Bot 回复，避免误删最近的不相关回复
- ⚙️ **配置支持**：新增 `_conf_schema.json`，支持在 AstrBot WebUI 中配置记录 TTL、清理间隔和 context_aware 策略
- ⚡ **时序优化**：移除发送前固定等待，减少撤回后仍发送的竞态窗口
- 🧪 **测试补齐**：新增回归测试覆盖撤回传播、safe/legacy 策略、请求/响应/发送阶段阻断和配置默认值

### v2.1.1 (2026-05-23)
- 🧩 **兼容修复**：撤回通知处理后不再调用 `event.stop_event()`，避免阻断防撤回、群管等依赖同一撤回通知的插件
- 📝 **版本同步**：统一代码、元数据、README 与 changelog 中的版本描述

### v2.0.0 (2025-01-18)
- 🔄 **完全重构**：全新架构，修复核心消息 ID 匹配问题
- 🔗 **context_aware 联动**：撤回时同步清理 context_aware 中的消息记录
- ⚡ **高优先级处理**：确保撤回事件优先处理
- 📊 **统计命令**：新增 `/recall_stats` 查看运行统计
- 🔧 **事件引用**：保存事件引用，支持跨 Task 停止处理
- 🧹 **改进清理**：优化过期记录清理机制

### v1.2.0 (2025-09-15)
- 优化撤回检测逻辑

### v1.1.1 (2025-09-10)
- 修复部分场景下的检测失败

### v1.0.0 (2025-09-03)
- 🎉 **初始发布**：完整的消息撤回取消功能

---

**💡 提示**：建议同时安装 context_aware 插件以获得最佳体验，确保撤回的消息不会污染 LLM 上下文。
