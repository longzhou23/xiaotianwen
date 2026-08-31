# P1/P2 本地实现记录

更新时间：2026-08-31

本次把 Todo 中可以在公共仓库、合成数据和无凭据环境完成的部分实现为
Orchestrator 的平台无关内核。目标是让 Luna/Terra 后续接入时有稳定的
边界和可复现的失败证据，不把本地通过误报为生产切换。

## P1：显式观测与隔离 Fake Runtime

`integration/observer.py` 提供版本为 2 的事件模型。每条事件有 sequence、
timestamp、run/turn/request/call/delivery 关联字段、source、status、capture
mode 和脱敏 payload。默认只记录长度、hash、数量和类别；只有调用方显式
设置 `capture_text=True` 时，才在本地观察记录中保留经过凭据脱敏并限长的
显示文本。`RuntimeObservationStore` 和公共测试框架 `TraceStore` 都支持按
事件时间的可配置 retention，过期事件自动丢弃，不把未连接状态折算成零值。

`integration/astrbot_adapter.py` 是显式适配器：调用方把真实或等价的
ProviderRequest/LLMResponse 传入指定方法，适配器读取 contexts、stream、
model、ToolSet、AstrBot 4.27.x 的 `tools_call_*` 和 TokenUsage 字段，不改写
对象，也不替换 AstrBot 的全局函数。当前测试使用已安装的 AstrBot 4.27.4
实体类型，但没有发起真实网络 Provider 请求。

`integration/fake_runtime.py` 提供本地四类合同场景：

- Fake OneBot 文本、图片、引用和转发结构的规范化；
- Fake Provider 的普通完成、部分 stream、usage、超时/abort/error；
- tool continuation 的父 request 和 call_id 关联；
- shadow 不派发、OneBot 断线和 `NOT_CONNECTED` capture mode。

命令：

```powershell
python -m tests.harness.cli run --profile integration --run-id p1-local
```

输出目录仍是 `artifacts/test-runs/p1-local/`。profile 会运行 fake 检查，
同时把真实 AstrBot 临时实例、插件发现、Hook 顺序、Plugin Page、真实
Provider/QQ 和长时观测写入 `NOT_VERIFIED`，因此正常退出不代表真实集成通过。

## P2：迁移内核

| 文件 | 责任 | 当前边界 |
|---|---|---|
| `p2/provider_registry.py` | provider 注册、source 唯一、相同内容去重、唯一 assembler 调用 | 不接收 ProviderRequest；真实插件迁移尚未切换 |
| `p2/service.py` | 给 Web/API 使用的稳定只读状态 facade | 不反向导入 Web，不负责生产发送 |
| `p2/hook_contract.py` | 读取字段、写入字段、priority、owner、effect 清单 | 是源码/目标契约，不是运行时顺序证明 |
| `p2/tools.py` | pure/read/write/send effect、read single-flight、最多 3 路读并发、写串行、call_id 顺序、发送幂等和双层截断 | handler 仍由未来隔离宿主注入 |
| `p2/security.py` | 固定输入风险、非可信 OCR/VLM/转发来源、工具权限和最终输出门 | 未接入真实 AntiPromptInjector/Output Audit |
| `p2/affection.py` | 明确 interactive/idle Provider、错误分类、message ID 幂等、每 bot 一个 decay handle | 不读写现有情绪/Iris 数据 |
| `p2/operations.py` | 容器/WebUI/登录/OneBot/AstrBot/最小收发分层健康状态、有限重试、SQLite companion 和脱敏 manifest | 不执行远端维护动作 |
| `p2/performance.py` | P50/P95、10% 回归判断、100 Turn 不发送清单、24/72 小时模板 | 长时和人工门禁仍未开始 |
| `p2/experiments.py` | feature flag、独立 branch/session、400 即终止且不重试 | 不把实验参数发到生产 |
| `p2/proactive.py` | 群聊/私聊共享主动聊天 policy，以及超过阈值后按 request_id 关联的 show/update/retract 状态意图 | 只返回纯策略结果，不创建虚拟事件、后台任务或出站消息 |

`tests/harness/cli --profile audit` 对 `plugins/` 做静态 AST 清单，记录 Hook、priority
和 direct LLM 调用；checked-in baseline 发生漂移时运行失败，并将完整清单写入本次
run 的 `hook-manifest.json`。它是升级漂移安全网，不是运行时加载顺序证明。

Group Chat Plus 的 70 万字节 legacy `main.py` 没有被整体重写；现有
`ingress/`、`context/`、`output/` 和新增 `p2/` 是可逐步迁移的公共内核。
这样可以先验证 source/owner/effect，再由真实隔离 Hook 记录确认每个旧 manager
没有调用方，最后才考虑删除。没有使用新的运行时 monkey-patch，也没有修改
私有实例、数据库、图片池、Iris 数据或现役 SnowLuma 容器。

## 本次自动门禁

```powershell
python -m pytest -q tests
python -m pytest -q plugins/modified/astrbot_plugin_xiaotianwen_orchestrator/tests
python -m compileall -q plugins/modified/astrbot_plugin_xiaotianwen_orchestrator
git diff --check
python -m tests.harness.cli run --profile audit --run-id p0-audit-final-20260831
```

当前全量结果为 `105 passed`；静态审计为 `PASS`，扫描到 56 个 Hook 和 25 个
direct LLM 调用。通过表示公共代码、fake 合同、策略和脱敏边界通过。以下仍必须单独执行并
保留证据：真实 AstrBot 4.27.x 临时实例、Hook/Plugin Page、真实 Provider
契约、100 Turn Canary、24 小时 shadow、SnowLuma 72 小时、故障恢复和生产
主回复切换。
