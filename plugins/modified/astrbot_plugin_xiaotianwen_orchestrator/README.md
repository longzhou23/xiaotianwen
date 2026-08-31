# 小天文编排器（P1 影子模式）

这个插件目录实现小天文后续编排层的 P1 基础能力：

- `contracts/`：`TurnEnvelope`、`ContextSection`、`MediaRef` 和 `ToolExecutionPolicy`；
- `ingress/`：OneBot/AstrBot 事件标准化、短 TTL 去重、3 秒安静窗口和记录式取消状态机；
- `context/`：ContextAware、Iris、ImageContextPool、Shared Context 的**快照型只读** adapter，以及统一的排序、预算、去重和脱敏差异比较。
- `media/`：媒体 ID、消息顺序、标注 artifact、缺失文件提示和 VLM `content_hash + provider + prompt_version` 缓存键；
- `decision/`、`request_plan.py`：显式 route 策略、请求级 context memo、工具续轮复用和脱敏的 P95/质量指标；
- `output/`、`ingress/ownership.py`：一次 delivery 幂等、取消/审计门禁、shadow/canary/active/disabled 所有权策略。

## P1 的安全边界

- 不导入 Group Chat Plus 的内部类；`compatibility/` 只比较传入的结构化观察记录。
- `contracts/`、`ingress/`、`context/` 都不导入 AstrBot；只有 `main.py` 是一个不注册 Hook 的 AstrBot 外壳。
- 不创建 `asyncio.Task`，没有后台计时器；`flush_ready()` 由测试或未来观察 Hook 显式调用。
- 不创建 ProviderRequest，不调用 LLM、embedding、VLM、Iris 检索、工具或 QQ 发送 API。
- 默认不在私有实例的 `plugins.lock.yaml` 中启用，且 `main.py` 的 coordinator 保持 disabled。

这意味着本次变更可在本地独立验证，但**不会接管当前 Group Chat Plus 的主回复、图片链路或防抖配置**。

## 分批测试

在 public 仓库根目录运行。每一批均只测纯 Python，不需要 AstrBot 容器、QQ 登录态、模型密钥或网络。

```powershell
# A. P1-1：跨插件契约与非法输入
py -m pytest -q plugins/modified/astrbot_plugin_xiaotianwen_orchestrator/tests/test_contracts.py

# B. P1-2：事件规范化、去重、3 秒聚合、记录式取消
py -m pytest -q plugins/modified/astrbot_plugin_xiaotianwen_orchestrator/tests/test_turn_coordinator.py

# C. P1-3：只读上下文 adapter、预算、去重、脱敏结构差异
py -m pytest -q plugins/modified/astrbot_plugin_xiaotianwen_orchestrator/tests/test_context_assembler.py

# D. P1-4：媒体注册表、引用解析、清理后 metadata-only 恢复和 VLM cache key
py -m pytest -q plugins/modified/astrbot_plugin_xiaotianwen_orchestrator/tests/test_media_registry.py

# E. P1-5/P1-7/P1-8：所有权、delivery 幂等、memory single-flight、route 与 request plan
py -m pytest -q plugins/modified/astrbot_plugin_xiaotianwen_orchestrator/tests/test_ownership_delivery.py plugins/modified/astrbot_plugin_xiaotianwen_orchestrator/tests/test_memory_route_plan.py

# 全部 P1 单元测试（当前 34 项）
py -m pytest -q plugins/modified/astrbot_plugin_xiaotianwen_orchestrator/tests
```

Linux/Azure 上把首个 `py` 改为 `python3` 或容器内已验证的 Python 命令。上线前的真实回放、24 小时 shadow gate 和 canary gate 仍属于 Todo 的后续步骤，不能用这组单元测试替代。

本目录的 P1 实现是可部署但默认 dormant 的纯 Python library。它没有真实 AstrBot hook、ProviderRequest、Iris 查询、VLM/embedding、工具或 QQ delivery；`main.py` 仅加载配置并输出安全边界。因此测试通过表示契约和策略通过，不表示线上路径已经切换。

## 后续接入顺序

1. 由 Group Chat Plus/ContextAware 只读地导出一批实际事件和旧 payload 的结构观察；
2. 用 `ShadowTurnCoordinator` 与 `ContextAssembler` 做回放，记录 hash、长度、source、message id 和分批差异；
3. 完成 24 小时无额外请求的 shadow gate；
4. 再实现 P1-4 媒体注册表和 P1-5 的显式 `shadow/canary/active/disabled` 接入开关。
