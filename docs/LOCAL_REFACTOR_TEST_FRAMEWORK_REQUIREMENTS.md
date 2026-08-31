# 小天文本地重构回归测试框架需求文档

> 文档版本：v1.1
> 状态：待开发
> 目标仓库：`bot/public/xiaotianwen`
> 主要开发模型：Terra；也可由 Luna 按 P0/P1 子任务逐项实现
> 适用范围：小天文公共插件、Orchestrator、消息编排链路、Local Test Console 及其与 AstrBot 的接口
> 默认原则：本地、离线、可重复、无真实账号副作用、失败时保守退出

## 1. 执行摘要

在当前公共仓库内建立一套统一的本地测试框架，用于在消息编排层和插件重构前后，对同一批脱敏输入进行确定性回放，并比较以下行为是否保持不变或符合显式批准的新预期：

- 一个用户意图被划分成几个 Turn；
- 创建了几次主回复、预判断、LLM、VLM、Embedding 和工具调用；
- 上下文 section 的来源、顺序、预算、去重和安全边界；
- 图片、引用、合并转发、表情包及天文标注图的关联关系；
- 工具调用顺序、`call_id`、参数、结果续接及幂等性；
- Output Audit、Tool Use Cleaner、Smart Segmentation 和最终发送的执行顺序；
- 数据库、文件、网络和发送动作等副作用；
- 日志、报告和快照中是否出现凭据、人格原文、用户隐私或内部提示泄漏；
- 相对基线的延迟、调用次数和上下文体积变化。

除命令行和文件报告外，P0 必须交付一个本机 Web UI（下称 **Local Test Console**）。用户应能在界面中输入一条或多条测试消息，并按同一组关联 ID 实时查看：原始测试 input、规范化事件、Turn 划分、全部已观测请求、AstrBot/测试框架日志、工具调用、各后处理阶段以及最终 output。该界面只服务本地测试，不替代 AstrBot Dashboard，也不属于统一公网运维网关。

框架必须整合现有插件自己的测试，但不能把“插件单元测试通过”误报为“完整消息链路兼容”。最终报告必须分别展示单元测试、AstrBot 契约测试、离线回放、隔离集成和人工 Canary 的证据与未验证项。

本需求的首要交付是 P0。P0 完成后，即使没有 Docker、真实 Provider、QQ 登录态和网络，也应能在 Windows PowerShell 上执行重构回归测试。

## 2. 项目现状与约束

### 2.1 当前结构

本仓库同时包含：

- `plugins/modified/`：二次开发或自研插件；
- `plugins/upstream/`：上游插件副本；
- `deploy/`、`scripts/`、`config/`：公开部署和运维材料；
- `docs/ARCHITECTURE.md`：组件边界和消息管线不变量；
- `Todo.md`：重构、测试、灰度和发布的统一主计划。

仓库外的 `private/`、`recovery/`、`projects/astrbot_test_server/` 和 `local-secrets/` 不属于本测试框架的默认数据源。它们可能包含实例数据、旧日志、账号状态或临时实验结果，不能直接复制为测试夹具。

### 2.2 已有测试能力

仓库内已有多个插件级 `pytest`/`unittest` 测试，其中 ChatGPT Codex Provider、Iris Memory、Stealer、Output Audit、Context Aware 等已有各自测试。它们的依赖、运行入口和隔离程度不统一，目前缺少：

- 仓库级统一命令；
- 统一的插件测试清单和依赖失败分类；
- AstrBot 真实接口类型的契约测试；
- 旧路径与新路径的同输入差异回放；
- 调用次数、发送副作用和数据库写入的统一探针；
- 可审查、可批准但不能自动覆盖的 Golden Baseline；
- 对重构发布门禁有直接结论的结构化报告。

已有 `projects/astrbot_test_server/` 是历史测试运行目录，包含日志、备份和实例数据。P0 不得依赖或重用其中的状态；P1 若借鉴其启动方式，也必须重新建立无凭据、可销毁的隔离实例。

### 2.3 现场工作树保护

在编写本文档时，公共仓库存在未跟踪目录：

`plugins/modified/astrbot_plugin_xiaotianwen_orchestrator/`

该目录是用户现有工作，含 contracts、context 和 shadow ingress 等代码。开发模型必须先读取并适配，不得删除、覆盖、回退、移动或假定其内容可重新生成；不得使用 `git clean`、`git reset --hard`、`git checkout --` 等命令处理它。若测试框架需要修改该目录，必须把修改限制在测试所需的最小范围，并在交付报告中逐文件说明。

### 2.4 平台约束

- 开发主机：Windows，统一入口必须支持 PowerShell 7；
- 生产目标：Ubuntu 24.04 x64，核心 Python 入口同时提供 Bash 包装；
- P0 默认禁止访问公网、真实 QQ、生产 AstrBot、SnowLuma 和真实模型接口；
- P1 的 Docker/进程集成测试只能使用临时目录、测试端口、Fake Provider 和 Fake OneBot；
- P2 的真实 QQ Canary 只能在用户明确授权后人工执行，不属于自动测试默认命令。

## 3. 目标、非目标与成功定义

### 3.1 目标

1. 用一条命令运行适合当前改动范围的快速测试。
2. 用同一 fixture 分别执行 baseline 和 candidate，并生成可解释差异。
3. 对非确定性的模型文本比较结构、安全和副作用，而非脆弱的全文相等。
4. 对确定性 contracts、状态机、上下文顺序、ID 和序列化结果执行精确比较。
5. 默认阻断真实发送、网络写入、生产数据库修改及秘密读取。
6. 让失败报告能直接回答“哪个事件导致哪一个阶段出现何种行为差异”。
7. 让 Luna/Terra 能按明确边界继续补充场景，不需要理解或启动完整生产环境。
8. 为 `Todo.md` 中 Shadow Gate、Canary Gate 和 Production Gate 提供机器可读证据。
9. 让用户无需翻阅多份日志，即可从一次 input 追踪到全部已观测请求和最终 output。

### 3.2 非目标

- 不重写 AstrBot、SnowLuma、Iris 或现有插件业务逻辑；
- 不把所有插件依赖安装到一个可能冲突的全局 Python 环境；
- 不在 P0 启动真实 QQ 客户端或恢复 QQ 登录态；
- 不调用收费模型来生成 Golden Baseline；
- 不使用生产聊天记录、真实 persona、真实记忆库或用户画像作为 fixture；
- 不因为测试失败而清空、迁移或修复任何生产数据库；
- 不把容器 `running`、模块可导入或单元测试通过当作端到端恢复成功；
- 不自动批准 baseline 变化；
- 不在本任务中执行生产部署、提交、推送、发布或更改远程服务。

### 3.3 成功定义

P0 完成时，开发者应能在无网络环境中：

1. 安装测试框架依赖；
2. 运行 `quick` 与 `refactor` 配置；
3. 回放脱敏消息事件；
4. 观察 Turn、上下文、模型/工具调用和发送副作用；
5. 对 candidate 与已批准 baseline 生成 JSON、JUnit XML 和 Markdown 报告；
6. 在故意引入“双主回复”“重复发送”“已有摘要仍调用 VLM”“敏感日志泄漏”等缺陷时稳定失败；
7. 恢复代码后再次运行并稳定通过；
8. 在 Local Test Console 输入合成消息，并实时查看请求树、日志和分阶段 output；
9. 全过程不访问真实网络、不读取私有实例、不修改仓库外数据。

## 4. 测试分层与结论边界

报告必须把以下层级分开，不得合并成一个模糊的“全部通过”。

| 层级 | 名称 | 运行对象 | 默认网络 | 能证明什么 | 不能证明什么 |
|---|---|---|---|---|---|
| L0 | 静态与安全检查 | 配置、fixture、报告、源码边界 | 禁止 | 无明显秘密、fixture schema 合法、关键文件可解析 | 运行时行为正确 |
| L1 | 插件单元测试 | 单个插件的纯函数、状态机、服务类 | 禁止 | 插件内部逻辑符合测试 | AstrBot 接口和完整 Hook 顺序正确 |
| L2 | 契约测试 | AstrBot 类型、ProviderRequest、OneBot 事件、插件 adapter | 禁止 | 与固定目标版本的接口兼容 | 容器、Dashboard、真实 QQ 正常 |
| L3 | 离线回放/差分 | 旧路径、shadow、新路径 | 禁止 | 同输入下结构、调用和副作用差异可控 | 外部服务质量和真实模型文本质量 |
| L4 | 隔离集成 | 临时 AstrBot + Fake OneBot + Fake Provider | 仅本机回环/容器内网 | 插件加载、Hook、路由和主链路可协作 | 真实 SnowLuma/QQ 登录与生产配置正确 |
| L5 | 人工 Canary | 授权测试账号/测试群 | 按人工计划 | 真实平台链路在有限范围内可用 | 生产长期稳定性 |

默认 `quick` 运行 L0、核心 L1 和框架自测；默认 `refactor` 运行 L0、相关 L1、L2 和 L3。L4 必须显式指定 `integration`，L5 不得由自动命令启动。

## 5. 术语和比较单位

- **Event**：脱敏后的 OneBot/AstrBot 输入事件。
- **Intent**：用户在一个时间窗口中希望系统处理的一次意图。
- **Turn**：Orchestrator 归并后的一次处理单元。
- **Route**：`private`、`group_passive`、`group_proactive`、`tool_continuation` 等受 contracts 白名单约束的处理路径。
- **Observation**：测试探针记录的结构化事实，不包含不必要的正文。
- **Side Effect**：发送消息、调用写工具、写数据库、写文件、发网络请求、更新状态等动作。
- **Baseline**：在明确版本和 fixture schema 下人工批准的预期 observation。
- **Candidate**：当前工作树或指定版本产生的 observation。
- **Structural Diff**：忽略允许的非确定字段后，对 Turn、section、调用、工具和副作用进行比较。
- **Golden Update**：对 baseline 的显式、可审查更新；不能作为普通测试运行的副作用。

## 6. 总体架构要求

框架应采用 Python 作为核心 runner，PowerShell/Bash 仅作薄包装。推荐目录如下；若实现者调整命名，必须保持职责等价并在 README 说明原因。

```text
xiaotianwen/
├── pyproject.toml                       # 仅在不会破坏现有插件配置时新增
├── tests/
│   ├── README.md
│   ├── plugin-matrix.yaml               # 插件测试发现、命令、依赖和超时
│   ├── contracts/                       # AstrBot/OneBot/插件边界契约
│   ├── harness/
│   │   ├── cli.py                       # 统一命令入口
│   │   ├── config.py
│   │   ├── clock.py                     # 虚拟时间
│   │   ├── ids.py                       # 确定性 ID
│   │   ├── network_guard.py             # 默认拒绝外部网络
│   │   ├── sandbox.py                   # 临时目录和写入边界
│   │   ├── spies.py                     # LLM/VLM/tool/send/storage 探针
│   │   ├── replay.py
│   │   ├── compare.py
│   │   ├── redact.py
│   │   └── report.py
│   ├── ui/
│   │   ├── server/                      # 本机 API、SSE/WebSocket、日志适配
│   │   ├── frontend/                    # Local Test Console 前端
│   │   └── README.md
│   ├── fixtures/
│   │   ├── schema/
│   │   ├── cases/
│   │   └── assets/                      # 仅小型合成/公开测试资源
│   ├── baselines/                       # 经批准、可提交的结构化基线
│   ├── selftests/                       # 证明框架能抓到故意缺陷
│   ├── integration/
│   └── performance/
├── scripts/
│   ├── test-local.ps1
│   └── test-local.sh
├── test-support/
│   ├── compose.test.yml                 # P1，可选
│   ├── fake-provider/
│   └── fake-onebot/
└── artifacts/test-runs/                 # 运行产物，必须被 Git 忽略
```

不允许把测试 runner 放进某个业务插件内部，使其他插件依赖它。业务插件可以保留自己的 tests，仓库级框架通过 `plugin-matrix.yaml` 调用它们。

## 7. 功能需求

### FR-001 统一命令入口

必须支持：

```powershell
pwsh ./scripts/test-local.ps1 -Profile quick
pwsh ./scripts/test-local.ps1 -Profile refactor
pwsh ./scripts/test-local.ps1 -Profile full-offline
pwsh ./scripts/test-local.ps1 -Profile integration
pwsh ./scripts/test-local.ps1 -Profile ui
```

```bash
bash ./scripts/test-local.sh quick
bash ./scripts/test-local.sh refactor
bash ./scripts/test-local.sh full-offline
bash ./scripts/test-local.sh integration
bash ./scripts/test-local.sh ui
```

核心 Python CLI 还应支持：

```text
python -m tests.harness.cli run --profile quick
python -m tests.harness.cli run --case private-text-burst-001
python -m tests.harness.cli run --tag media --candidate current
python -m tests.harness.cli compare --baseline approved --candidate current
python -m tests.harness.cli approve-baseline --case <id> --reason <text>
python -m tests.harness.cli doctor
python -m tests.harness.cli list
python -m tests.harness.cli ui --host 127.0.0.1 --port 0 --open
```

要求：

- 从任意子目录调用时都能可靠定位仓库根目录；
- 路径含中文和空格时正常工作；
- PowerShell/Bash 包装不得拼接未转义命令字符串；
- 输出同时适合人类阅读和 CI 收集；
- 退出码固定：`0` 通过，`1` 测试/比较失败，`2` 环境或配置错误，`3` 安全边界违规；
- `integration` 未满足 Docker 条件时应报告 `SKIPPED/NOT VERIFIED`，不能伪装成通过。

### FR-002 测试配置与插件矩阵

`plugin-matrix.yaml` 至少记录：

- 插件路径和维护类别（modified/upstream）；
- 测试命令和工作目录；
- Python 版本约束；
- 依赖安装方式；
- 测试 marker、超时和是否可离线；
- 是否需要 AstrBot 真实包；
- 失败分类：测试失败、依赖缺失、收集失败、超时、环境不支持；
- 是否属于默认 `quick`、`refactor` 或 `full-offline`。

不同插件依赖可能冲突。框架不得默认把所有 `requirements*.txt` 安装到同一个全局环境。可以采用以下任一方案：

1. 仓库 harness 使用一个锁定环境，各插件通过独立临时 venv 运行；
2. 对依赖兼容的插件建立少量明确分组；
3. 初期只运行无额外依赖的 tests，并把未安装依赖清晰标记为 `NOT RUN`。

无论采用哪种方案，都不能把 collection error 当作 skip 后继续宣称全部通过。

### FR-003 Fixture schema

fixture 必须使用可校验的 YAML 或 JSON，并带 `schema_version`。建议格式：

```yaml
schema_version: 1
id: private-text-burst-001
title: 私聊三条连续消息归并为一个 Turn
tags: [private, debounce, regression]
route: private
clock:
  start: 1000.0
events:
  - at_ms: 0
    event:
      post_type: message
      message_type: private
      message_id: test-msg-001
      user_id: test-user-a
      raw_message: "第一条合成测试消息"
  - at_ms: 1200
    event:
      post_type: message
      message_type: private
      message_id: test-msg-002
      user_id: test-user-a
      raw_message: "第二条合成测试消息"
fakes:
  provider:
    responses:
      - kind: final
        text_class: ordinary_reply
  context:
    iris: {version: iris-fixture-v1, summary: "合成记忆摘要"}
expect:
  turns: 1
  main_reply_requests: 1
  deliveries: 1
  duplicate_side_effects: 0
  forbidden_calls: [real_network, real_qq, real_provider]
compare:
  text: structural
  ignore_fields: [duration_ms, generated_at]
```

schema 校验要求：

- ID、message ID、user ID、group ID 必须为明显的合成值；
- 不允许出现本机绝对路径、真实域名、IP、Token、Cookie、私钥、QQ 号码和聊天原文；
- 时间、随机数和外部响应必须显式给出或由确定性生成器提供；
- fixture 引用的文件只能位于 `tests/fixtures/assets/`；
- 每个 case 必须声明比较策略和预期副作用；
- 不允许任意 Python callback 或 `eval` 配置进入 fixture。

### FR-004 确定性执行

框架必须提供：

- 可手动推进的虚拟时钟；
- 固定随机种子；
- 可预测的 request/turn/call/delivery ID；
- 稳定的 timezone 和 locale；
- 临时、隔离的文件系统根目录；
- 确定性 Fake Provider、Fake Embedding、Fake VLM 和 Fake Tool；
- asyncio 任务完成/取消的受控调度与超时；
- 规范化 JSON 序列化。

Golden 中不得保存进程 ID、真实临时路径、端口、墙钟时间、耗时抖动或随机 UUID。确有诊断价值时，应在 compare 前规范化。

### FR-005 Fail-closed 网络与副作用隔离

在 L0-L3 中，默认禁止：

- 非回环 TCP/UDP 连接；
- HTTP(S)、WebSocket、MQTT、SMTP 等外部请求；
- 调用真实 Provider、Embedding、VLM、QQ、SnowLuma 或 Dashboard；
- 写入仓库、fixture、baseline、用户主目录或工作区外路径；
- 打开、修改或复制 private/recovery/local-secrets 中的数据；
- 执行 send/write/status/steal 等真实工具副作用。

测试需要通过显式注入 fake adapter，而不是依赖“机器当前没有网络”。网络 guard 应在检测到未声明连接时立即以退出码 `3` 失败，并报告调用位置，但不能打印请求中的秘密。

P1 `integration` 只允许临时测试进程之间的回环地址或专用 Docker 网络；使用端口必须动态分配并记录，不能占用生产约定端口后误连现有实例。

### FR-006 统一 Observation 模型

每个 case 至少记录：

- case、schema、baseline 和 candidate 版本；
- 输入事件的安全 fingerprint；
- Turn 数量、状态转换、route 和事件归并关系；
- context section 的 `source/name/order/version/chars/tokens/fingerprint`；
- 主回复、预判断、LLM、VLM、Embedding 的调用数量和角色；
- Provider 请求中的模型类别、工具 schema fingerprint、usage 和终止类型；
- 工具调用的 name、effect、call_id、参数结构 fingerprint、结果状态；
- Output Audit、Cleaner、Segmentation、Delivery 的阶段顺序；
- 发送类型和数量，不保存不必要的真实正文；
- 文件/数据库/网络写入尝试；
- warning、exception、timeout、cancel 和 fallback；
- 可选性能指标。

Observation 应使用类型明确、版本化的数据模型。业务对象不得以 `repr()` 直接写入报告，以免引入地址、正文或秘密。

### FR-007 探针与 Fake 组件

至少实现以下 spy/fake：

- `ProviderSpy`：区分主回复、DecisionAI、Iris 后台任务及工具续接；
- `VLMSpy`：记录首次解析与缓存命中；已有摘要时调用次数必须为 0；
- `EmbeddingSpy`：记录模型类别、输入数量和索引操作；
- `ToolSpy`：记录 effect、call_id、参数、结果和重复执行；
- `DeliverySpy`：记录文本、图片、文件、引用和分段发送；
- `StorageSpy`：记录 SQLite/文件写入意图和事务结果；
- `HookTrace`：记录关键 Hook/阶段的顺序和 owner；
- `LoggerProbe`：检测敏感字段和原始正文泄漏；
- `CancellationProbe`：记录取消请求、迟到结果和被抑制 delivery。

Fake 组件必须模拟成功、超时、取消、格式错误、重试、部分流式输出和工具调用终止，不能只覆盖 happy path。

### FR-008 Baseline 与 Candidate

必须支持两类基线：

1. **Committed Golden**：提交在 `tests/baselines/` 的已批准结构化 observation；
2. **Ref Baseline（P1）**：从指定 Git ref 在临时 worktree/临时目录中运行，不修改当前工作树。

要求：

- 每个 baseline 记录 fixture schema version、框架 version、来源 commit/ref 和批准说明；
- 当前工作树不干净时允许测试，但报告必须列出 dirty 状态和相关路径；
- 不得自动执行 `git clean`、reset、stash 或 checkout；
- `approve-baseline` 必须要求 case/范围和 reason，先展示差异，再通过独立显式命令写入；
- 普通 `run`/`compare` 永远不能更新 baseline；
- baseline schema 变化必须有迁移脚本或明确失效提示，不能静默重写。

### FR-009 差异比较规则

比较器必须把差异分为：

- **BLOCKER**：主回复数、delivery 数、写工具次数、route、Hook 安全顺序、敏感泄漏、数据写入、异常终止等变化；
- **REVIEW_REQUIRED**：context section 内容 fingerprint、预算、工具参数、模型 usage、fallback、图片关联等变化；
- **PERFORMANCE**：P50/P95、token、context bytes、调用数变化；
- **INFO**：允许的版本号、诊断字段变化。

默认阻断条件至少包括：

- 同一 intent 主回复请求数不等于 1；
- 同一 request 出现重复 delivery 或重复写工具；
- 消息被吞、错误合并、错误取消或出现迟到回复；
- Output Audit 未位于最终发送前；
- Smart Segmentation 新增 LLM 请求；
- 已有图片摘要仍调用 VLM；
- 工具结果未与正确 call_id 续接；
- Tool call/usage 在 Provider adapter 中丢失；
- 读取或写入真实私有数据；
- 报告/日志出现被禁止的秘密或隐私模式；
- 未声明外部网络连接。

对模型自然语言输出不得默认全文相等。结构比较至少检查：是否为空、消息类型、工具调用结构、安全类别、是否含 forbidden pattern、分段数量上限和 delivery owner。确定性序列化、route、ID、调用次数和状态转换必须精确相等。

### FR-010 现有插件测试编排

仓库级 runner 必须：

- 发现并运行 `plugin-matrix.yaml` 中列出的测试；
- 保留插件自己的工作目录和 import 约定；
- 单独记录每个插件的 collected/passed/failed/skipped/error；
- 设置单插件超时，超时后终止其子进程树；
- 不能因一个插件依赖缺失而放弃所有其他插件；
- 不能改变插件源码来“适配”统一 runner，除非确属产品缺陷并单独说明；
- 对 upstream 插件测试失败只报告证据，不擅自大规模修改上游副本；
- 输出 JUnit XML，供后续 CI 使用。

P0 至少接入以下已有测试入口或清晰标记未运行原因：

- `astrbot_plugin_chatgpt_codex`；
- `astrbot_plugin_output_audit`；
- `astrbot_plugin_context_aware`；
- `astrbot_plugin_group_chat_plus`；
- `astrbot_plugin_astrmetry`；
- `astrbot_plugin_iris_memory`；
- `astrbot_plugin_stealer`；
- `astrbot_plugin_recall_cancel`。

### FR-011 Orchestrator 核心单元测试

对现有 Orchestrator 至少覆盖：

- TurnEnvelope 合法/非法输入、白名单 route、序列化往返；
- OneBot mapping 与 AstrBot-like event 的安全规范化；
- event fingerprint 和重复事件抑制；
- 3 秒 debounce 窗口边界；
- 同用户连续文本合并；
- 图片先到、文字后到的单 Turn 行为；
- 不同用户/群/会话不错误合并；
- 新消息取消旧请求的竞态；
- 取消后迟到模型结果不能发送；
- 状态机非法转换失败；
- ContextSection 排序、优先级、预算、截断和去重；
- Iris/ContextAware/ImageContextPool/SharedContext 只消费只读 snapshot；
- MediaRef 多图顺序、引用和 annotated image 关联；
- 日志只含 fingerprint/长度/类别，不含原始正文；
- shadow 模式不调用 LLM、VLM、Embedding、工具、网络或 delivery。

### FR-012 AstrBot 契约测试

P1 必须针对项目声明的 AstrBot 目标版本建立真实类型契约。最低要求：

- 固定并报告 AstrBot 版本/commit/image digest，不使用漂移的 `latest` 作为唯一依据；
- 构造真实或等价严格的 ProviderRequest/LLMResponse/MessageChain；
- 验证 streaming 和 non-streaming final 的差异；
- 验证 `LLMResponse.usage`、TokenUsage、tool calls 和 tool result continuation；
- 验证常见图片输入、引用消息和合并转发结构；
- 验证插件注册、Hook 参数和生命周期；
- 验证 Plugin Page 路由占位符与宿主 matcher 兼容；
- 验证 Output Audit → Cleaner → Segmentation → Delivery 的顺序；
- 明确区分 Chat Provider、Agent Runner 和 STT/TTS/Embedding/Rerank，不用单一 Chat 测试宣称全类型兼容。

若目标 AstrBot 版本无法在 Windows 直接安装，可在 P1 的隔离容器中执行；报告必须保留该层未执行时的缺口。

### FR-013 离线回放场景

完整 fixture 库应逐步达到 `Todo.md` 的最低矩阵：

| 类别 | 最低场景数 | 自动化层级 | 核心断言 |
|---|---:|---|---|
| 私聊普通对话 | 10 | L3 | persona/历史/记忆 section 结构、一次发送 |
| 私聊连续消息 | 5 | L1/L3 | debounce、一次 Turn、一次回复 |
| 群聊被动回复 | 10 | L3 | @/引用/指代、上下文上限、route |
| 群聊主动聊天 | 10 | L3 | 预判断与主回复分离、无重复发送 |
| 图片与表情包 | 8 | L1/L3 | MediaRef、摘要复用、VLM 回退、多图顺序 |
| 合并转发 | 5 | L2/L3 | 结构保留、内部指令不执行 |
| 工具调用 | 10 | L2/L3 | call_id、结果续接、effect、幂等 |
| 安全注入 | 20 | L0/L3 | 无内部提示、persona、工具和他人记忆泄漏 |
| SnowLuma/QQ 故障 | 8 | L4/人工 | 单实例、OneBot、网络、重启；P0 用 fake 故障 |
| 迁移恢复 | 2 | L4/人工 | 临时数据完整恢复；不触碰生产数据 |

P0 不要求一次写完全部 88 个场景，但必须交付至少 20 个高价值、完全离线 case，覆盖每个自动化核心类别，并提供清单显示距离完整矩阵还缺多少。P1 应补齐可离线自动化的场景；真实平台部分继续标记为人工或隔离集成。

### FR-014 框架自测与故障注入

必须有 selftests 证明框架能抓到至少以下故障：

- 两次主 Provider 请求；
- 两次最终发送；
- 同一写工具重复执行；
- 已有图片摘要仍调用 VLM；
- 取消后迟到回复发送；
- context section 顺序错误或超预算；
- Tool result 与错误 call_id 关联；
- Output Audit 被绕过；
- fixture 或报告含测试秘密标记；
- 尝试连接外部网络；
- 尝试写出 sandbox；
- baseline 被普通 run 意外修改。

selftests 应使用框架内的故意错误 fake/candidate，不修改生产源码。selftests 本身通过的含义是“故意错误被预期捕获”。

### FR-015 报告

每次运行在 `artifacts/test-runs/<run-id>/` 生成：

- `summary.md`：面向人的结论、阻断项和未验证项；
- `summary.json`：稳定 schema 的机器可读结果；
- `junit.xml`：测试结果；
- `diff.json`：baseline/candidate 结构差异；
- `observations/`：按 case 拆分的脱敏 observation；
- `environment.json`：Python、OS、AstrBot、Git ref、dirty path 等，不含秘密；
- `logs/`：仅保留脱敏测试日志。

`summary.md` 首页必须回答：

1. 是否通过本次 profile 的发布门禁；
2. 哪些层实际运行；
3. 哪些层未运行以及原因；
4. 有多少 blocker/review/performance/info 差异；
5. 哪些插件测试失败、缺依赖或未收集；
6. 是否观察到真实网络或写出 sandbox 的企图；
7. baseline/candidate 的来源和工作树状态；
8. 下一步最小修复建议。

报告不得保存真实 prompt、完整用户文本、persona、记忆正文、Token、Cookie、Authorization header、数据库内容或图片 base64。必要文本应使用长度、类别和 SHA-256 fingerprint 表示；测试用合成文本可以在明确标记后保留。

### FR-016 性能基线

P2 提供可选性能 profile，至少记录：

- 普通文本 Turn 的 P50/P95；
- debounce/组装自身耗时；
- context chars/estimated tokens；
- 主回复、DecisionAI、VLM、Embedding 和工具调用数；
- 单工具、双工具和五个只读工具的 Agent Loop；
- 图片摘要首次与缓存命中；
- candidate 相对 baseline 的变化百分比。

性能测试必须使用重复运行、warmup 和统计摘要。单次本机耗时不能作为 blocker。默认门禁：功能等价时 P95 不应恶化超过 10%；若样本量不足，结果必须标记 `INSUFFICIENT_DATA`。

### FR-017 Change impact 选择

P1 可加入基于 Git diff 的测试选择，但必须保守：

- 修改 Orchestrator、contracts、公共 context、Provider 或 Hook 顺序时运行完整 `refactor`；
- 修改图片路径时至少运行 media、context、delivery、安全场景；
- 修改 Provider 时至少运行 contract、agent/tool、usage、stream 场景；
- 修改 Output Audit/Cleaner/Segmentation 时运行完整输出管线和注入场景；
- 无法判断影响范围时运行 `full-offline`；
- 用户显式选择完整测试时不得被自动降级。

测试选择结果及其理由必须写入报告。

### FR-018 Local Test Console 用户界面

#### FR-018.1 定位与边界

必须提供一个随测试框架安装、只在本机运行的 Web UI。该界面的目标是调试和比较单次测试输入的完整处理链路，而不是服务器运维：

- **Local Test Console**：查看 input、Event、Turn、Context、请求、日志、工具和 output；
- **AstrBot Dashboard**：管理 AstrBot 实例和插件；
- **Unified Operations Gateway**：受认证的服务器运维入口；
- **SnowLuma WebUI/noVNC**：QQ 接入和账号维护。

四者职责不能混合。Local Test Console 故障不得影响 AstrBot、SnowLuma 或 QQ 主链路；P0 不得通过 iframe 嵌入生产 AstrBot Dashboard 来冒充测试 UI。

“所有请求”在本需求中指当前测试 run 内，由已安装探针在明确 dispatch 边界捕获的全部请求。UI 必须显示捕获完整度：

- `COMPLETE`：该运行路径的所有 dispatch adapter 均已安装；
- `PARTIAL`：只捕获部分 Hook/Provider/工具边界；
- `LOG_INFERRED`：仅从日志推断，不可视为完整证据；
- `NOT_CONNECTED`：未连接 AstrBot 或对应 adapter。

界面不得在缺少探针时声称“0 次请求”。此时应显示“未观测/未连接”，而不是把未知值显示为 0。

#### FR-018.2 启动与连接

默认启动方式：

```text
python -m tests.harness.cli ui --host 127.0.0.1 --port 0 --open
```

要求：

- 默认只绑定 `127.0.0.1`，端口 `0` 表示自动选择空闲端口；
- 启动后打印本地 URL、run/session ID 和连接模式；
- 可自动打开默认浏览器，也可用 `--no-open` 禁止；
- 停止 UI 时只终止它启动的测试子进程，不影响已有 AstrBot/SnowLuma；
- P0 默认连接内存 harness、Fake Provider 和 Fake OneBot；
- P1 可显式启动或连接本次 run 创建的隔离 AstrBot；
- 不允许根据常见端口自动连接现有 `6200`、`8001`、`5099` 或 `6081` 服务；
- 连接任何隔离实例前，UI 顶部必须显示数据源、PID/容器、启动时间和 sandbox 路径。

后端应复用 Python harness 的事件模型和脱敏器，避免 CLI 与 UI 各自实现一套采集逻辑。前端技术可由实现者选择，但资产必须本地打包，不能依赖外部 CDN、字体、分析脚本或遥测服务。

#### FR-018.3 主界面信息架构

至少提供以下六个区域或页面：

1. **Runs**：运行历史、profile、baseline/candidate、状态、开始时间和门禁结论；
2. **Input Composer**：输入测试文本、选择私聊/群聊/主动路由、添加合成图片/引用/合并转发；
3. **Request Explorer**：全部已观测请求的树、列表、时间线和详情；
4. **AstrBot Logs**：实时日志、来源状态、过滤、暂停和定位；
5. **Output Inspector**：原始模型流、工具续接、审核、清理、分段和最终输出；
6. **Compare**：baseline 与 candidate 的结构差异及 blocker。

推荐桌面布局：

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ Local Test Console  Run: run-001  OFFLINE  Capture: COMPLETE  PASS/FAIL │
├──────────────────────┬───────────────────────────────┬───────────────────┤
│ Input Composer       │ Request / Turn Timeline       │ Output Inspector  │
│ route: private       │ input.received                │ provider chunks   │
│ [测试输入原文      ] │ event.normalized              │ LLMResponse       │
│ [添加图片][发送测试] │ turn.started                  │ tool result       │
│                      │ context.assembled             │ audit verdict     │
│ Recent Inputs        │ request.main_reply            │ cleaned output    │
│ - msg-001            │ ├─ tool.search                │ segments          │
│ - msg-002            │ └─ request.continuation       │ final delivery    │
├──────────────────────┴───────────────────────────────┴───────────────────┤
│ AstrBot / Harness Logs  [level] [component] [request_id] [搜索] [暂停]   │
└──────────────────────────────────────────────────────────────────────────┘
```

窄屏时可以改为标签页，但 Input、Requests、Logs、Output 必须在最多一次切换内可达。请求详情和日志应支持在新分栏中并排查看。

#### FR-018.4 Input Composer

用户必须能在界面中：

- 输入单条或多条文本，并看到自己输入的完整测试原文；
- 选择 `private`、`group_passive`、`group_proactive` 等允许 route；
- 设置明显为合成值的 user/group/session ID；
- 添加一张或多张测试图片，调整顺序并设置引用关系；
- 选择测试用 reply、@、合并转发和表情包事件模板；
- 指定消息间隔，用虚拟时间模拟 debounce；
- 选择 Fake Provider 响应模板：普通完成、流式、工具调用、超时、取消、错误；
- 点击“发送测试”后创建新的 run 或追加到明确选中的活动 session；
- 取消当前 run，但不能停止任何非本次 UI 创建的进程；
- 将本次合成输入导出为待审查 fixture；不得自动写入正式 fixture/baseline。

当前活动会话中，用户手工输入的测试原文可以完整显示。默认持久化报告仍只保存脱敏结构；只有用户明确执行“导出合成 fixture”并通过 secret/path 扫描后，输入原文才能写入仓库测试目录。

文件上传要求：

- 仅允许配置白名单中的图片/文本测试类型；
- 限制单文件和总大小；
- 文件名不能决定落盘路径；
- 存入本次 run sandbox，并在界面显示 SHA-256、MIME 和尺寸；
- 禁止 SVG/HTML 等可执行内容以内联方式渲染；
- 删除 run 时只清理该 run 的已验证 sandbox。

#### FR-018.5 Request Explorer

每个输入必须通过以下关联字段贯穿界面：

```text
run_id
  └─ session_id
      └─ event_id / message_id
          └─ turn_id
              └─ request_id
                  ├─ parent_request_id
                  ├─ call_id
                  └─ delivery_id
```

Request Explorer 至少区分并使用明显图标/颜色标记：

- 主回复 LLM；
- DecisionAI/主动聊天预判断；
- Iris 摘要、改写、抽取或人格任务；
- VLM/图片理解；
- Embedding；
- Rerank（若存在）；
- Agent tool selection；
- tool execution；
- tool result continuation；
- Output Audit/rewrite；
- 外部 HTTP/WebSocket 尝试；
- final delivery。

每条请求详情至少显示：

- 类型、owner、父请求、route 和关联 ID；
- started/completed/failed/cancelled 状态；
- 开始时间、持续时间、重试次数和 timeout；
- Provider/模型类别、streaming、finish reason；
- 输入消息角色和 section 来源顺序；
- tools/schema fingerprint、tool choice、call_id；
- usage、input/output/cache token（有真实证据时）；
- 请求体与响应体的本地查看器；
- 异常/fallback/cancellation；
- 与 baseline 的差异等级。

合成 fixture 和 Fake Provider 请求体可在活动本地会话中完整查看。以下字段即使用户选择“显示完整 payload”也必须强制替换为 `[REDACTED_SECRET]`：

- Authorization、Cookie、Set-Cookie、Proxy-Authorization；
- API Key、Token、Secret、AUTH_CODE、密码、私钥；
- URL userinfo 和敏感 query 参数；
- 浏览器会话、QQ 登录态和主机凭据。

请求列表支持按类型、owner、状态、request ID、模型、工具名和差异等级过滤。点击某个 request 后，日志和 Output Inspector 应自动同步到同一关联 ID。

#### FR-018.6 AstrBot Logs

UI 必须提供实时日志视图，并明确区分来源：

- `HARNESS`：测试框架日志；
- `FAKE_ONEBOT`：Fake OneBot 日志；
- `FAKE_PROVIDER`：Fake Provider 日志；
- `ASTRBOT_STDOUT`/`ASTRBOT_STDERR`：P1 隔离 AstrBot 进程；
- `ASTRBOT_FILE`：明确 allowlist 的本次测试日志文件；
- `PLUGIN`：带插件名称的结构化日志。

日志视图至少支持：

- 实时追加、暂停、恢复和回到底部；
- level、source、plugin、turn/request/call ID 过滤；
- 文本搜索和时间范围；
- ERROR/WARNING/Traceback 快速跳转；
- 点击日志跳转到对应 request/turn；
- 当前日志源是否连接、从何时开始采集；
- 限量下载本次 run 的脱敏日志。

P1 读取 AstrBot 日志时必须从本次隔离进程启动边界开始，不能混入历史日志后再据此判断新代码行为。日志文件路径必须由后端 allowlist 选择，前端不能提交任意路径。浏览器中用纯文本渲染日志，禁止把日志内容作为 HTML 执行。

日志流应使用 SSE 或 WebSocket，并实现断线重连、sequence number、缺口提示和背压。单个 run 的内存日志/事件数量必须有上限；超过上限时保留 ERROR、关键状态和首尾窗口，并显示 dropped count，不能悄悄丢弃。

#### FR-018.7 Output Inspector

同一个 Turn 的 output 必须按处理阶段展示，而不是只显示最终文本：

1. Provider 原始流式 chunk；
2. 聚合后的 LLMResponse；
3. tool calls；
4. tool results；
5. tool continuation；
6. Output Audit allow/revise/block 结果；
7. rewrite 后文本（若有）；
8. Tool Use Cleaner 后文本；
9. Smart Segmentation 分段；
10. 最终 DeliverySpy 记录的文本/图片/文件/引用。

要求：

- 每阶段显示 owner、开始/结束时间、输入/输出长度和 fingerprint；
- 合成测试内容可展开查看完整文本；
- 清晰标记某阶段未执行、无观测或被跳过的原因；
- 流式 chunk 与最终非 chunk response 分开显示；
- 工具内部资料不得误标为已经发送给用户；
- cancelled/late output 必须以醒目状态显示，并证明是否被 delivery 抑制；
- 多个 delivery 或同一写工具重复执行时立即显示 blocker；
- 可并排比较 baseline/candidate 的阶段数量、顺序、结构和最终 output。

#### FR-018.8 事件流和后端 API

UI 不应解析终端彩色文本来推断请求。Harness、Fake 组件和 P1 AstrBot observation adapter 应发出统一、版本化事件，至少包括：

```text
ui.input.received
onebot.event.normalized
turn.started / turn.merged / turn.cancelled / turn.completed
context.section.added / context.assembled
request.started / request.chunk / request.completed / request.failed
tool.started / tool.completed / tool.suppressed
audit.completed
output.cleaned / output.segmented
delivery.attempted / delivery.completed / delivery.suppressed
log.emitted
guard.network_blocked / guard.write_blocked
run.completed
```

每个事件至少包含 `schema_version`、`sequence`、`timestamp`、`run_id`、可用的关联 ID、`kind`、`source`、脱敏 payload 和 capture mode。

推荐 API；实现者可调整路径，但必须保持能力等价：

```text
POST /api/runs
GET  /api/runs
GET  /api/runs/{run_id}
POST /api/runs/{run_id}/inputs
POST /api/runs/{run_id}/cancel
GET  /api/runs/{run_id}/timeline
GET  /api/runs/{run_id}/requests
GET  /api/runs/{run_id}/logs
GET  /api/runs/{run_id}/outputs
GET  /api/runs/{run_id}/compare
GET  /api/runs/{run_id}/stream
```

API 只能接受 schema 校验后的枚举动作，不接受任意 shell、任意 Python 表达式、任意日志路径、任意外部 URL 或任意宿主机文件路径。

#### FR-018.9 UI 安全与隐私

- 默认只监听 loopback；P0/P1 不实现公网模式；
- 页面启动使用当前进程生成的短期会话 token，并校验 Origin/CSRF；
- token 不写入仓库、URL query、日志或报告；
- 设置严格 CSP，不加载外部脚本、字体、图片或 analytics；
- 所有输入、日志和 output 以文本渲染并转义；
- 浏览器刷新后默认不恢复含完整原文的临时会话；
- 导出前再次运行脱敏和秘密扫描；
- “显示完整 payload”只影响当前本地浏览器视图，不能取消强制凭据脱敏；
- 前端不得持有 Docker Socket、主机 shell 或生产日志目录访问权；
- UI 后端不得为了捕获所有请求对生产 AstrBot 做全局 monkey patch；P1 使用显式 observation bridge/adapter；
- 任一 guard 违规时 UI 显示 blocker，并使对应 run 退出码为 `3`。

#### FR-018.10 UI 验收场景

P0 UI 至少通过以下自动化或浏览器级验收：

- [ ] 启动后仅监听 `127.0.0.1`，能自动选择端口；
- [ ] 输入一条合成文本，界面完整显示 input；
- [ ] 输入三条带虚拟间隔的消息，界面正确显示合并后的单 Turn；
- [ ] Fake Provider 的主回复、工具调用和续接请求按父子关系显示；
- [ ] Request Explorer 能看到请求体、响应体、状态、usage 和关联 ID；
- [ ] Harness/Fake 日志实时出现，按 request ID 过滤后与时间线一致；
- [ ] Output Inspector 显示 chunk、聚合、audit、clean、segment、delivery 阶段；
- [ ] 故意双请求/双发送时界面显示 blocker；
- [ ] 已有图片摘要仍调用 VLM 时界面显示 blocker；
- [ ] cancelled 请求的迟到 output 被标记且没有 delivery；
- [ ] baseline/candidate 差异可从时间线定位到具体阶段；
- [ ] 断开事件流后能重连，并对缺失 sequence 给出提示；
- [ ] 注入 HTML/script 的测试文本只按纯文本显示；
- [ ] Authorization/Cookie/Token 测试值在 payload、日志、下载报告中均为 `[REDACTED_SECRET]`；
- [ ] 尝试上传超限/非法类型或请求任意日志路径时被拒绝；
- [ ] 未连接 AstrBot 时显示 `NOT_CONNECTED`，不显示虚假的“0 请求/0 日志”；
- [ ] UI 停止后不影响任何既有 AstrBot/SnowLuma 进程。

## 8. 非功能需求

### NFR-001 可移植性

- Windows PowerShell 7 和 Ubuntu Bash 均有入口；
- 路径使用 `pathlib`，不能假设 ASCII、无空格或固定盘符；
- 不依赖开发者全局安装的 Poetry、pytest 插件或 shell alias；
- 环境安装方式必须在 `tests/README.md` 中从干净 venv 开始说明；
- 所有生成目录可删除重建，不成为唯一数据源；
- Local Test Console 支持当前稳定版 Chromium/Chrome/Edge，核心查看功能不能依赖浏览器扩展。

### NFR-002 性能预算

- `quick` 目标：常见开发机冷启动 2 分钟内；
- `refactor` 目标：不含大型插件完整套件时 10 分钟内；
- `full-offline` 可更长，但每个插件/场景必须有超时并持续输出进度；
- 任何单项超过 60 秒时必须在终端显示当前阶段，不允许无反馈挂起；
- UI 冷启动目标 5 秒内，事件到达后 500ms 内出现在本机界面；
- 日志列表必须虚拟化或分页，持续测试时不能因 DOM 无限增长失去响应。

以上是目标而非通过即正确的替代条件。超出预算应报告慢项，不能为提速跳过关键测试而仍称通过。

### NFR-003 安全与隐私

- 测试依赖和命令不得读取 `.env`、凭据存储或浏览器会话；
- secret scanner 至少识别常见 API key、Bearer、Cookie、私钥头、URL 内凭据、长高熵字符串和本地已知敏感路径；
- scanner 命中时报告文件、行号和模式类别，敏感值显示为 `[REDACTED_SECRET]`；
- fixture、baseline、artifact 和异常 trace 都必须经过同一脱敏层；
- 测试销毁只作用于已验证属于本次 run 的临时目录。

### NFR-004 可维护性

- 核心 harness 具备类型注解和自身单元测试；
- schema、observation 和 report 均有版本；
- 比较规则集中管理，不散落在 fixture 的任意表达式中；
- fake 与业务 adapter 分离；
- CLI、报告和 UI 共用同一 versioned event/observation 模型；
- 单个插件可新增 manifest 项和 fixture，而无需修改 runner 核心；
- 失败消息包含 case、阶段、expected、actual 和定位线索。

### NFR-005 可审计性

- 每个 baseline 变更可从 Git diff 看出原因；
- 不允许自动重录并覆盖全部 golden；
- 报告列出测试框架自身版本；
- skipped、xfail、missing dependency、not verified 必须分开计数；
- 禁止使用空断言、无条件 skip 或过宽异常捕获制造假绿。

## 9. 分阶段开发计划

### P0：离线 MVP，必须优先完成

- [x] 新增仓库级测试依赖与安装说明，不影响各插件运行依赖。
- [x] 实现 PowerShell、Bash 和 Python CLI。
- [x] 实现 `doctor`、`list`、`quick`、`refactor`、`ui`。
- [x] 实现 fixture schema 与校验。
- [x] 实现虚拟时钟、确定性 ID、sandbox 和 network guard。
- [x] 实现 Provider/VLM/Embedding/Tool/Delivery/Storage/Logger probes。
- [x] 定义 versioned Observation schema。
- [x] 实现 Golden 读取、结构比较和显式批准命令。
- [x] 接入现有插件测试矩阵，准确区分 failed/error/missing/skipped。
- [x] 为 Orchestrator 补核心单元测试，但不改变 shadow-only 产品边界。
- [x] 提供至少 20 个离线 fixture。
- [x] 提供至少 12 个故障注入 selftests。
- [x] 生成 Markdown、JSON、JUnit 和 diff 报告。
- [x] 实现 Local Test Console 后端、实时事件流和本地前端。
- [x] 实现 Input Composer、Request Explorer、Logs 和 Output Inspector。
- [x] UI 中按 run/session/event/turn/request/call/delivery ID 关联所有阶段。
- [x] 为 UI 提供自动化 API 测试和浏览器级核心流程测试。
- [x] `.gitignore` 排除 artifacts、临时 venv 和集成运行数据。
- [x] 在 Windows 本机执行并记录实际结果（见 `tests/VALIDATION-2026-08-31.md`）。

P0 验收门禁：

- [x] 无网络、无 Docker、无真实 secrets 时可运行（P0 不调用 Docker；集成 profile 仅报告未验证）。
- [x] 中文路径下 `quick` 和 `refactor` 均可启动。
- [x] 中文路径下 `ui` 可启动，并且仅监听 loopback。
- [x] 用户能在 UI 输入合成消息并看到全部已观测请求、日志和分阶段 output。
- [x] 未安装某类探针时 UI 显示 `PARTIAL/NOT_CONNECTED`，不显示虚假零值。
- [x] 强制凭据字段在 UI、API、日志和下载文件中均已脱敏。
- [x] 正常候选通过；故意错误候选被捕获。
- [x] 普通 run 不修改 baseline、fixture 或业务源码。
- [x] 不读取 `private/`、`recovery/`、`local-secrets/` 和历史 test server 数据。
- [x] 报告没有秘密、真实账号信息和非测试正文。
- [x] 未执行层明确显示 `NOT VERIFIED`。

P0 证据与环境限制记录在 `tests/VALIDATION-2026-08-31.md`。`NOT VERIFIED`
表示插件依赖、approved baseline 或 P1 集成层尚未验证，不等同于失败，也不等同于生产通过。

### P1：契约与隔离集成

- [ ] 固定目标 AstrBot 版本并实现真实类型契约测试。
- [ ] Fake OneBot 覆盖文本、图片、引用、合并转发、断线和重复事件。
- [ ] Fake Provider 覆盖 stream、usage、tool call、tool result、timeout 和 abort。
- [ ] 新增临时 AstrBot 测试实例，所有数据挂载到 run sandbox。
- [ ] 验证插件发现、加载、Hook 顺序和 Plugin Page 路由。
- [ ] 通过显式 observation adapter 将隔离 AstrBot 请求和日志接入 UI。
- [ ] UI 展示真实契约中的 stream、usage、tool call、continuation 和最终 delivery。
- [ ] 验证日志 SSE/WebSocket 的重连、sequence 缺口和背压。
- [ ] 支持指定 Git ref 的 baseline 临时 worktree。
- [ ] 补齐可离线的统一测试矩阵。
- [ ] 实现保守的 change impact 测试选择。
- [ ] 记录端口、进程和容器清理结果。

P1 验收门禁：

- [ ] 集成测试不连接现有 6200/8001/5099/6081 服务。
- [ ] 临时实例结束后没有遗留容器、进程和未说明数据。
- [ ] Provider contract 同时验证 ordinary chat、Agent tool loop 和 usage；其他 provider 类型的缺口单列。
- [ ] 插件单测、契约、回放、集成结论分别展示。
- [ ] UI 明确显示每类请求的 capture completeness 和日志来源。
- [ ] 无真实 QQ、Provider 凭据也能执行。

### P2：性能、故障恢复与人工门禁材料

- [ ] 实现性能 profile 和 baseline 趋势报告。
- [ ] 在 UI 中增加调用数、token、P50/P95 和 baseline 趋势图。
- [ ] 模拟断网、Provider 超时、OneBot 重连、重复事件和取消竞态。
- [ ] 模拟临时 SQLite 快照/恢复，不使用生产数据库。
- [ ] 生成 100 Turn Canary 清单和观测模板，但不自动发送 QQ。
- [ ] 生成 24 小时 Shadow 和 72 小时 SnowLuma 人工观测模板。
- [ ] 把测试结果映射到 `Todo.md` 的 Shadow/Canary/Production Gate。

## 10. 最低回归断言

任何消息编排重构都必须持续满足：

1. 同一用户意图只创建一个主回复请求；
2. 同一 request 只有一个最终 delivery owner；
3. 防抖、取消、图片紧随文字不产生重复回复或吞消息；
4. DecisionAI、Iris 后台任务和主 Agent 调用角色可区分；
5. 已有图片摘要时 VLM 调用数为 0；
6. 每张图片拥有稳定 MediaRef，多图顺序和引用不丢失；
7. 工具 `call_id`、结果续接和 effect 正确，写工具不重复；
8. Output Audit 不被绕过，Smart Segmentation 不新增主 LLM；
9. Persona、Iris、图片池、Provider 和输出插件的职责边界不被合并破坏；
10. 日志和报告不包含内部提示、人格原文、他人记忆或秘密；
11. shadow 模式不产生 LLM、VLM、Embedding、工具和发送副作用；
12. 回滚测试不依赖删除或清空任何数据库；
13. UI 展示的 input、request、log 和 output 可以通过关联 ID 追溯到同一 run/turn。

## 11. 交付物

开发模型完成对应阶段后，必须交付：

- 测试框架源码；
- PowerShell/Bash 统一入口；
- Local Test Console 前后端源码和本地启动入口；
- Input、Requests、AstrBot Logs、Output、Compare 五类核心 UI 视图；
- 锁定或可复现的测试依赖说明；
- fixture/observation/report schema；
- 插件测试矩阵；
- 初始离线 fixture 和经批准 baseline；
- 框架 selftests；
- `tests/README.md`；
- 一次 Windows 实际运行报告；
- 变更文件列表；
- 实际执行命令和结果摘要；
- 未验证层、缺失依赖、已知限制和下一步建议。

除非用户另行要求，不提交、不推送、不发布，不修改私有仓库和生产实例。

## 12. 开发完成 Definition of Done

只有满足以下所有项目，对应阶段才算完成：

- [ ] 需求中的阶段交付项全部完成，未完成项有明确原因；
- [ ] 框架自身测试通过；
- [ ] 故障注入能证明关键回归会失败；
- [ ] 现有插件测试的真实状态被准确报告；
- [ ] baseline 不会被普通运行覆盖；
- [ ] 运行不触达真实网络、QQ、Provider 和私有数据；
- [ ] 所有写入限制在仓库预期文件或本次临时 sandbox；
- [ ] 报告完成脱敏并区分各验证层；
- [ ] Local Test Console 能完整演示 input → requests/logs → output；
- [ ] UI 只绑定本机、无外部资产，并通过脚本注入和秘密脱敏测试；
- [ ] Windows 中文路径实际验证完成；
- [ ] 没有覆盖、删除或回退用户现有未跟踪 Orchestrator 工作；
- [ ] 没有用“可导入”“容器运行中”或“部分测试通过”替代完整验收；
- [ ] 文档、命令和实际实现一致。

## 13. 交给 Luna/Terra 的执行指令

下面内容可以直接作为开发任务提示词使用：

```text
请在当前小天文公共仓库中，根据
docs/LOCAL_REFACTOR_TEST_FRAMEWORK_REQUIREMENTS.md
实现“P0：离线 MVP”。

开始前先只读检查：
1. README.md、docs/ARCHITECTURE.md、Todo.md 的测试与门禁章节；
2. 当前 git status；
3. plugins/modified/astrbot_plugin_xiaotianwen_orchestrator/ 的现有内容；
4. 各插件已有 tests、pytest 配置和依赖。

严格约束：
- 不删除、覆盖、移动或回退任何用户现有改动；
- 特别保护当前未跟踪的 astrbot_plugin_xiaotianwen_orchestrator；
- 不运行 git clean/reset/checkout，不擅自 commit/push；
- 不读取或复制 private、recovery、local-secrets、历史 test server 的真实数据；
- 不连接真实 QQ、SnowLuma、AstrBot、模型 Provider 或公网；
- P0 必须实现 Local Test Console，不能只交付 CLI 或静态报告页面；
- UI 必须显示用户 input、所有已观测请求、Harness/AstrBot 日志和分阶段 output；
- 未捕获的请求类别要显示 PARTIAL/NOT_CONNECTED，不能伪装为零请求；
- 不为了让测试变绿而无条件 skip、放宽关键断言或自动更新 baseline；
- 先实现测试框架，不在本任务中重构业务链路。

工作方式：
- 按 P0 清单逐项实现；
- 使用 apply_patch 修改文件；
- 每完成一个小阶段就运行最相关的测试；
- 最后在 Windows 中文路径下执行 quick、refactor、selftests，并启动 UI 完成核心浏览器流程；
- 若缺少可选插件依赖，准确报告 NOT RUN/MISSING DEPENDENCY，继续验证其他部分；
- 最终报告列出变更文件、执行命令、各层结果、未验证项和风险。

P0 完成前不要开始 P1/P2。若需求与现有代码冲突，选择最小、可逆、
不触碰生产数据的实现，并在最终报告中明确冲突和取舍。
```

### 模型选择建议

- **Terra**：建议负责 P0 的整体架构、runner、event schema、比较器、报告器、Local Test Console 和跨插件集成，减少 CLI/UI/探针接口不一致的风险。
- **Luna**：适合在已有骨架和明确接口后，补充 fixture、插件矩阵条目、Orchestrator 单元测试、UI 组件、PowerShell/Bash 包装及文档；也可以先实现一个范围严格的 P0 子任务。

若只启动一个开发任务，优先交给 Terra 完成 P0；若拆分给 Luna，必须按“核心事件模型 → probes/compare → UI 后端与实时流 → UI 前端 → fixtures/selftests → 插件矩阵 → 文档与 Windows 验证”的顺序，每次交付都基于上一阶段已合并的接口。
