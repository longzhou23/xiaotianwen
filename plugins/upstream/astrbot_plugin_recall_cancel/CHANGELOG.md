# Changelog

## v2.1.3 - 2026-05-23
- 修复：`safe` 策略通过 context_aware Bot 记录 ID、响应时间和会话级响应序号确认目标仍是最后一条后再删除，避免并发新回复或同内容新回复被误删。
- 修复：补充 deferred cleanup，覆盖撤回发生在 context_aware 已记录 Bot 回复但低优先级标记钩子尚未执行的竞态窗口。
- 测试：新增并发新回复、同内容新回复、旧匹配记录和低优先级标记竞态回归测试。

## v2.1.2 - 2026-05-23
- 优化：context_aware Bot 回复清理默认改为 `safe` 策略，只在确认本次 LLM 响应对应的 context_aware Bot 记录仍是最后一条时删除，避免误删同会话旧回复或并发新回复。
- 配置：新增 `_conf_schema.json`，支持在 AstrBot WebUI 中配置记录 TTL、清理间隔、context_aware 联动和 Bot 回复清理策略。
- 优化：移除发送前固定 `sleep(0.1)`，在 `on_decorating_result` 阶段直接停止已撤回消息对应的发送流程。
- 测试：新增 `unittest` 回归测试，覆盖撤回通知传播、safe/legacy 策略、请求/响应/发送阶段阻断和配置默认值。
- 文档：补充 streaming 已发送片段无法追回、`legacy_last` 旧行为风险和新配置说明。

## v2.1.1 - 2026-05-23
- 修复：撤回通知事件处理后不再调用 `event.stop_event()`，避免阻断 `astrbot_plugin_anti_revoke`、`astrbot_plugin_qqadmin` 等后续插件处理同一撤回通知。
- 整理：统一插件代码、`metadata.yaml` 和 `README.md` 中的版本描述。

## v2.0.1 - 2026-02-23
- 修复：撤回状态键改为 `unified_msg_origin::message_id`，避免跨会话 message_id 冲突导致误拦截。
- 增强：UUID（带短横线）识别兼容；待处理/已撤回状态读写统一按会话维度。

## v2.0.0
- 完整重构版本（详见 git 历史）。
