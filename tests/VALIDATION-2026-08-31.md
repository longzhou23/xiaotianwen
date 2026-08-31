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
| `py -m pytest -q tests/selftests` | `28 passed` | 框架正向测试、12 个故障注入、自身报告/路径/脱敏/UI HTTP、P1 fake runner 和部署静态门禁 |
| `Python 3.12 -m pytest -q tests plugins/modified/astrbot_plugin_xiaotianwen_orchestrator/tests` | `99 passed` | P0 harness、UI、P1 fake integration、P1/P2 Orchestrator |
| `Python 3.12 -m pytest -q plugins/modified/astrbot_plugin_chatgpt_codex/tests` | 通过 | Codex Provider 本地测试集合全部通过；首次暴露并修复 Windows SQLite 临时文件句柄问题 |
| `Python 3.12 -m pytest -q plugins/modified/astrbot_plugin_astrmetry/tests` | `3 passed` | P1 依赖环境验证 |
| `Python 3.12 -m pytest -q plugins/upstream/astrbot_plugin_iris_memory/tests` | `1496 passed, 1 skipped, 4 failed` | 上游 Windows/配置边界问题，保留为未通过，不篡改上游行为 |
| `Python 3.12 -m pytest -q plugins/upstream/astrbot_plugin_stealer/tests` | `205 passed, 2 failed` | 上游自定义 category 配置断言问题，保留为未通过 |
| `pwsh -NoProfile -File .\scripts\test-local.ps1 -Profile quick -RunId p0-final-quick-20260831` | `PASS` | 5 条代表性回放 + 可运行矩阵项 |
| `Python 3.12 -m tests.harness.cli run --profile refactor` | `NOT VERIFIED` | 功能回放 `23/23`；已启用矩阵项通过，3 个上游插件仍按 P0 策略明确未运行 |
| `Python 3.12 -m tests.harness.cli run --profile full-offline` | `NOT VERIFIED` | 功能+注入回放 `43/43`；插件矩阵 `7 PASSED / 3 NOT_RUN / 0 FAILED` |
| `py -m tests.harness.cli compare --case p0-group-text-single --baseline approved --run-id p0-compare-no-approved` | `NOT VERIFIED` | 没有 approved baseline；命令不会自动创建或覆盖 Golden |
| `Python 3.12 -m tests.harness.cli run --profile integration --run-id p1-p2-final-20260831` | `NOT VERIFIED` | 4/4 fake checks 通过，46 条观测；真实 AstrBot/Provider/QQ/长跑门禁保持未验证 |
| `Python 3.12 -m tests.harness.cli run --profile full-offline --run-id p1-p2-offline-regression-20260831` | `NOT VERIFIED` | 回放 43/43；插件矩阵 7 PASSED / 3 NOT_RUN / 0 FAILED |

## P1/P2 本轮实现增量

本轮完成的是公共仓库内、默认 dormant 的本地策略和观测内核，不改变当前生产主回复链：

- `integration/` 提供不持有平台对象的 AstrBot adapter、结构化观测、脱敏字段摘要和 disposable fake runtime；fake runtime 不连接 socket、Docker、QQ、Provider、数据库或模型。
- `p2/` 提供 Provider registry、声明式 Hook contract、工具 effect/幂等执行、安全边界、Affection 绑定与后台任务注册表、分层健康/备份 manifest、隔离实验 ledger、性能摘要和稳定只读 service facade。
- `main.py` 只准备配置、观测存储和显式 adapter，不注册真实 AstrBot ingress、LLM Hook、Provider、工具、定时任务或 QQ delivery；`observation_capture_text` 默认关闭。
- 99 项全量 Python 测试、编译检查和 `git diff --check` 通过。`integration` 的 46 条观测明确包含 `COMPLETE`、`PARTIAL`、`NOT_CONNECTED`，没有把未连接状态记作 0。

仍未完成且不能由上述结果替代的门禁：真实 AstrBot 临时实例/插件发现/Hook priority/Plugin Page、真实 Provider/QQ/SnowLuma、Group Chat Plus 旧路径只读接线、100 Turn canary、24 小时 Shadow Gate、72 小时 SnowLuma 长跑、空白 VM 恢复和任何生产 active 切换。P2 的旧 manager 删除、`plugins.lock.yaml` 退役标记及实际 `on_llm_request` 所有权迁移继续保持未勾选。

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

## Azure 代码同步验证（2026-08-31）

本次同步先执行了部署脚本的私有实例备份，随后仅拉取 public `main` 并执行
`RESTORE_INSTANCE=0 bash deploy/install.sh`；没有拉取新镜像，也没有恢复 private
快照。备份文件为远端 `backups/xiaotianwen-instance-20260831-002816.tar.gz`。

| 项目 | 结果 |
|---|---|
| public commit | `810d28459e70db530443176a760fa2a0b09e64e8` |
| public/private 工作区 | clean |
| Codex Storage / Recall Cancel 运行时文件 | 与 public 文件 SHA-256 一致 |
| AstrBot Dashboard / OneBot / SnowLuma WebUI / noVNC | 全部健康或监听 |
| 容器状态 | `astrbot running restarts=0`；`snowluma running restarts=0` |
| 部署脚本 403 检查 | 通过 |
| Orchestrator 私有锁/运行时目录 | absent-disabled；未启用 |

这证明的是“已启用插件代码同步且服务恢复”，不是用户会话、真实 Provider、真实
多模态请求或 Orchestrator 24 小时 Shadow Gate 的通过证明。部署后日志只做了不输出正文
的宽泛错误模式计数，未将其作为业务结论；如需进一步判断必须在测试会话中做脱敏
结构观测，不直接打印原始日志。
