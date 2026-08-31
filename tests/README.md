# 小天文本地重构测试框架（P0/P1/P2）

这是一个仓库级、离线、可删除重建的回归框架。它不登录 QQ，不读取
Private Instance，不连接生产 AstrBot / SnowLuma / Provider，也不尝试访问
6200、8001、5099 或 6081。

## 快速开始

在仓库根目录（路径可包含中文和空格）执行：

```powershell
pwsh ./scripts/test-local.ps1 -Profile quick
pwsh ./scripts/test-local.ps1 -Profile refactor
pwsh ./scripts/test-local.ps1 -Profile full-offline
pwsh ./scripts/test-local.ps1 -Profile audit
pwsh ./scripts/test-local.ps1 -Profile ui -NoOpen
```

```bash
bash ./scripts/test-local.sh quick
bash ./scripts/test-local.sh refactor
bash ./scripts/test-local.sh full-offline
bash ./scripts/test-local.sh audit
bash ./scripts/test-local.sh ui --no-open
```

核心入口也可以从任意子目录调用：

```text
python -m tests.harness.cli doctor
python -m tests.harness.cli list
python -m tests.harness.cli run --profile quick
python -m tests.harness.cli run --case p0-group-text-debounce
python -m tests.harness.cli run --tag tool
python -m tests.harness.cli run --profile audit
python -m tests.harness.cli compare --baseline approved --candidate current
python -m tests.harness.cli ui --host 127.0.0.1 --port 0 --open
```

`integration` 现在运行本地、无网络的 P1 Fake OneBot/Fake AstrBot/Fake
Provider 合同场景，并将观测写入本次 sandbox；命令不会连接现有容器或生产
端口。它仍会把真实 AstrBot 临时实例、插件发现/Hook 顺序、真实 Provider 和
QQ 业务链路标为 `NOT_VERIFIED`。

## 干净 Python 环境

P0 框架运行时只使用 Python 标准库；`pytest` 仅用于框架自身和已显式选入
矩阵的单元测试。不要为“跑所有插件”把各插件 requirements 混装到全局环境。

```powershell
py -m venv .test-venv
.\.test-venv\Scripts\Activate.ps1
python -m pip install "pytest>=8,<10"
```

```bash
python3 -m venv .test-venv
. .test-venv/bin/activate
python -m pip install 'pytest>=8,<10'
```

## 运行产物和结论

每次 CLI run 在 `artifacts/test-runs/<run-id>/` 写入：

- `summary.md` / `summary.json`；
- `junit.xml`；
- `diff.json`；
- `observations/`、`logs/`、`environment.json`。

这些路径被 Git 忽略。报告只写脱敏的结构化 observation：真实 prompt、
persona、记忆正文、图片 base64、Cookie、Token、密码和私钥不会被保存。

`PASS` 只表示本次已运行的离线层通过。若插件依赖、approved baseline 或 P1
集成层未运行，报告会显示 `NOT VERIFIED`，而不是把未知结果变成零调用或通过。

## Baseline

正常 `run` 和 `compare` 只读取 `tests/baselines/approved/`。它们从不重录或
覆盖 Golden。审批是一项显式、单 case 的评审动作：

```text
python -m tests.harness.cli approve-baseline --case p0-group-text-single --reason "首次审核后的结构基线"
```

交互终端会要求输入 `APPROVE`；CI 等非交互使用场景必须额外传入 `--yes`。

## 目录与扩展边界

- `fixtures/replay/`：正在开发的初始 P0 catalog；`fixtures/cases/` 是后续新增位置；
- `plugin-matrix.yaml`：每个插件的测试入口及真实 `PASSED` / `FAILED` /
  `MISSING_DEPENDENCY` / `NOT_RUN` 状态；
- `selftests/`：故意错误候选，证明框架真的会抓到双请求、双发送、越界写入、
  外部网络、秘密泄漏等回归；
- `ui/`：仅回环监听的 Local Test Console，显示当前 synthetic run 的 input、
  request、log、output 与 compare；
- `integration/`：P1 隔离层说明；可执行 fake 合同入口位于
  `tests/harness/p1_integration.py`，不等同于真实 AstrBot 集成。
- `performance/`：P2 长期性能门禁说明；统计和 Canary 模板位于 Orchestrator
  的 `p2/performance.py`，不把单次耗时当作功能通过。

插件矩阵中的 `NOT_RUN` 是明确缺口，不应通过安装生产依赖、读取实例数据或改动
上游插件来强行变绿。P1 会为它们建立独立、可销毁的依赖环境和 AstrBot 契约层。
