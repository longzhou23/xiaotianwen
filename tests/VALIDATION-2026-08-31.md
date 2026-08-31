# P0 本地离线测试验证记录（2026-08-31）

本记录对应框架版本 `0.1.0`，工作区为 Windows 中文路径下的公共仓库。
P0 回放验证只使用合成 fixture、内存 fake adapter 和临时 `artifacts/test-runs/`；
没有登录 QQ，没有读取 `private/`、`recovery/`、`local-secrets/`，也没有连接
SnowLuma、真实 Provider 或公网服务。为验证插件兼容性，另在隔离的 Python 3.12
虚拟环境中安装了 AstrBot 4.27.4 及测试依赖；这些测试仍设置了网络拒绝环境变量。

## 已执行命令

| 命令 | 结果 | 说明 |
|---|---|---|
| `py -m py_compile <tests/harness、tests/selftests、tests/ui、Orchestrator 测试的全部 Python 文件>` | 通过 | 语法编译完成 |
| `py -m pytest -q tests/selftests` | `26 passed` | 框架正向测试、12 个故障注入、自身报告/路径/脱敏/UI HTTP 测试 |
| `Python 3.12 -m pytest -q tests plugins/modified/astrbot_plugin_xiaotianwen_orchestrator/tests` | `77 passed` | P0 harness、UI、43 条回放支撑测试、P1 Orchestrator |
| `Python 3.12 -m pytest -q plugins/modified/astrbot_plugin_chatgpt_codex/tests` | 通过 | Codex Provider 本地测试集合全部通过；首次暴露并修复 Windows SQLite 临时文件句柄问题 |
| `Python 3.12 -m pytest -q plugins/modified/astrbot_plugin_astrmetry/tests` | `3 passed` | P1 依赖环境验证 |
| `Python 3.12 -m pytest -q plugins/upstream/astrbot_plugin_iris_memory/tests` | `1496 passed, 1 skipped, 4 failed` | 上游 Windows/配置边界问题，保留为未通过，不篡改上游行为 |
| `Python 3.12 -m pytest -q plugins/upstream/astrbot_plugin_stealer/tests` | `205 passed, 2 failed` | 上游自定义 category 配置断言问题，保留为未通过 |
| `pwsh -NoProfile -File .\scripts\test-local.ps1 -Profile quick -RunId p0-final-quick-20260831` | `PASS` | 5 条代表性回放 + 可运行矩阵项 |
| `Python 3.12 -m tests.harness.cli run --profile refactor` | `NOT VERIFIED` | 功能回放 `23/23`；已启用矩阵项通过，3 个上游插件仍按 P0 策略明确未运行 |
| `Python 3.12 -m tests.harness.cli run --profile full-offline` | `NOT VERIFIED` | 功能+注入回放 `43/43`；插件矩阵 `7 PASSED / 3 NOT_RUN / 0 FAILED` |
| `py -m tests.harness.cli compare --case p0-group-text-single --baseline approved --run-id p0-compare-no-approved` | `NOT VERIFIED` | 没有 approved baseline；命令不会自动创建或覆盖 Golden |

当前 `full-offline` profile 的插件矩阵状态为：

- `PASSED`：框架 selftests、Orchestrator、Codex Provider、Output Audit、ContextAware、Group Chat Plus、Recall Cancel；
- `NOT_RUN`：Astrmetry、Iris Memory、Stealer（按 P0 隔离策略明确不运行）。

P1 依赖环境专项测试已单独执行并如上记录；它们不自动改变 P0 矩阵的 enabled 状态，
也不会为了变绿而修改上游插件测试或生产插件锁。

## Local Test Console 验证

通过 `py -m tests.harness.cli ui --host 127.0.0.1 --port 2491 --no-open` 启动
临时控制台，并在本机浏览器完成了核心流程：

1. 打开 Input Composer，选择 `group_passive` 和 fake `tool` provider；
2. 输入两条合成消息并创建 Run；
3. 查看 Request Explorer、Output Inspector、Timeline/Logs 和 Compare；
4. 确认 `turn/request/call/delivery` 关联 ID 可见，token 计数可见，且
   `AstrBot: NOT_CONNECTED` 被明确展示；
5. 确认控制台只监听 `127.0.0.1`，结束后关闭临时页面和服务。

UI 的自动 HTTP 测试覆盖 CSRF、回环绑定、合成输入、分阶段 output、SSE、
快速创建多个 Run 和关联 ID。浏览器验证使用的也是合成文本和 fake adapter。

## 安全与可复现性检查

- `NetworkGuard` 在测试子进程中拒绝外部 DNS/socket；P0 回放没有真实网络调用。
- `RunSandbox` 只允许写入 `artifacts/test-runs/<run-id>/`，路径策略拒绝
  `private/`、`recovery/`、`local-secrets/`、`.ssh/`、`runtime/` 以及仓库外路径。
- 报告保存结构化 observation，不保存 persona、完整 prompt、记忆正文、图片
  base64、Cookie、Token、密码或私钥；账号标识使用稳定别名。
- 报告和 UI 只显示相对 sandbox 标签，不写入本机绝对路径或用户名。
- 正常运行只读取 approved baseline；本轮没有生成任何 approved baseline。
- `artifacts/test-runs/`、临时虚拟环境和覆盖率输出已经加入 `.gitignore`。

## 尚未验证的范围

这不是生产通过证明。下列项目仍属于 P1/P2 或需要单独环境：

- 真实 AstrBot Provider/Agent Runner 契约、真实 QQ/OneBot、真实 VLM/Embedding；
- Iris Memory 的 4 个 Windows/上游失败和 Stealer 的 2 个上游失败；
- approved baseline 的人工评审与变更批准；
- Docker 临时实例、真实 AstrBot Hook 顺序、插件页面、SSE 重连/背压和 24/72 小时观测；
- Ruff 等可选 lint 工具（当前环境未安装）。

因此 `refactor` 与 `full-offline` 的正确结论是 `NOT VERIFIED`：已运行的
离线层通过，但不能据此宣称整套生产链路已经验收。
