# 小天文编排层整合与渐进式重构 TODO

> 计划版本：v1.0
> 建立日期：2026-08-31
> 适用仓库：`xiaotianwen` Public Repository
> 目标：保留 AstrBot、SnowLuma、Iris、Codex Provider、实例数据和部署系统，渐进式重构小天文自己的消息编排层。
> 核心原则：整合接口，不合并成新的巨型插件；不进行一次性推倒重写。

## 0. 文档状态与合并范围

本文件是仓库内唯一的 TODO 主计划，合并并取代：

- 旧 `docs/todo.md`：稳定性、上下文、安全、情绪、SnowLuma 和迁移计划；
- 旧 `docs/AGENT_LOOP_OPTIMIZATION_TODO.md`：Agent Loop、缓存、效率和响应速度专项；
- 根目录上一版 `Todo.md`：编排层整合与渐进式重构计划。

合并规则：

- 以 2026-08-31 的代码和仓库结构为准；
- 已经完成的工作保留状态和回归要求，不重新列成未完成任务；
- 旧快照中的插件名、目录和 Hook priority 仅作为历史线索，实施前重新读取当前源码；
- 同一任务只保留一个权威条目，专项细节放在对应工作包中；
- 与后续实测冲突的旧判断直接废弃，不为了“完整保存原文”继续传播错误结论；
- 人格、记忆、知识库、数据库和 secrets 不进入本文件的实例内容或示例。

### 0.1 当前实现状态

| 项目 | 本地状态 | 线上状态 |
|---|---|---|
| Codex Provider 避免重复追加 Runner 已组装动态上下文 | 已实现并有测试 | 尚待部署观察 |
| 动态上下文、工具 schema、route 脱敏诊断 | 已实现 | 尚待部署观察 |
| `prompt_cache_key` 按 model/route/instructions/tools 稳定分桶 | 已实现并有测试 | 尚待部署观察 |
| Group Chat Plus 在原生 schema 存在时跳过工具全文提醒 | 已实现 | 尚待部署观察 |
| 路由级推理强度和输出 token 上限 | 已实现并有测试 | 尚待部署观察 |
| 工具结果统一首尾保留式截断 | 已实现并有测试 | 尚待部署观察 |
| transport 默认最新 1 张原图、明确多图语义保留整组 | 已实现并有测试 | 尚待部署观察 |
| `max_agent_step=30 → 8` | 未修改 | 待实例配置或版本化 Core 方案 |
| 重复副作用工具硬保护和最终发送原子幂等 | P2 本地策略内核已实现并有测试 | 尚未接入生产工具/发送链 |
| Core 工具 metadata 和只读有界并发 | P2 隔离执行器已实现并有测试 | 待上游支持或版本化补丁 |

本轮新增 P1 integration bridge 和 P2 迁移/安全/运维/性能策略内核；真实 AstrBot
Hook、Provider、QQ、SnowLuma 长跑及 active 切换仍按后续验收门禁处理。

P0 基线已完成提交、测试、push 和 tag；本轮 P1/P2 本地内核已实现并完成本地回归，
后续只剩真实实例接线、授权人格样本和长时验收门禁。

### 0.2 已完成的基础设施

- [x] 建立 Public Repository，存放公开代码、部署脚本和文档。
- [x] 建立 Private Instance Repository，存放私有配置和实例数据。
- [x] 建立 `plugins.lock.yaml`，记录实例启用插件和来源。
- [x] NapCat 退出现役运行链路，SnowLuma 成为唯一 QQ 接入层。
- [x] 建立 AstrBot / SnowLuma Docker Compose 部署。
- [x] 建立 start/stop/restart/status/backup/restore/update/verify 脚本。
- [x] 显式更新 `latest` 时记录 image digest，并保留更新失败回退点。
- [x] 建立 AstrBot 容器内持久化 Codex CLI 安装流程。
- [x] 图片上下文池容量调整为 30 条索引窗口。

这些项目不因为合并 TODO 而重新开发；后续只做回归、补充测试和修复已发现的问题。

### 0.3 关联设计文档

- `docs/ARCHITECTURE.md`：当前系统结构、职责和数据流；
- `docs/VERSION_POLICY.md`：`latest`、image digest 和回退策略；
- `plugins/modified/astrbot_plugin_output_audit/README.md`：监管 AI 和最终输出审核实现；
- `docs/UNIFIED_OPERATIONS_GATEWAY_DESIGN.md`：历史运维面板设计，仅保留仍适用的安全边界；
- `docs/DEPLOYMENT.md` 与 `deploy/README.md`：部署和维护命令；
- `docs/DRILL-20260826.md`：首次新机演练记录。

## 1. 当前结论

小天文有必要进行架构重构，但不需要重写整个项目。

需要重点重构：

- 消息去重、防抖、取消和主回复创建；
- Group Chat Plus 的巨型实现；
- 多插件共同修改 `ProviderRequest` 的钩子链；
- 普通回复、主动聊天、图片回复和工具回复之间不统一的请求模型；
- 图片、表情包、星图的媒体引用与缓存协议；
- 上下文 section 的排序、预算、去重和缓存指纹；
- 工具副作用、并发、重试和幂等策略。

明确保留：

- Public / Private 双仓库和 secrets 分离；
- Docker Compose 部署、备份、验证和回退脚本；
- AstrBot Core，除非使用有版本检查的补丁或上游 PR；
- SnowLuma 作为 QQ / OneBot 接入层；
- Iris Memory 的 L2/L3、画像、好感度、人格学习和记忆数据；
- ChatGPT Codex Provider 的独立插件边界；
- Output Audit 的独立最终出站安全门；
- Smart Segmentation 的最终文本分段职责；
- Persona、知识库、数据库、图片索引和表情包库的原始数据。

## 2. 当前基线

### 2.1 仓库状态

- [x] Public Repository 已存在并连接远端 `main`。
- [x] Private Instance Repository 已存在并连接远端 `main`。
- [x] 公共代码、私有实例状态和主机 secrets 已建立基本边界。
- [x] 部署侧已有预检、备份、健康检查、镜像摘要和更新失败回退。
- [x] 提交当前尚未提交的 Agent Loop P1 修改（Public commit `85c8b16`）。
- [x] 在重构开始前创建可回退 tag（`pre-orchestrator-refactor-20260831`）。
- [x] 确认 Private Repository 工作区干净并记录对应 commit（`143239f`）。
- [x] 记录 Public / Private / 镜像 digest / 配置版本的联合基线（见 [P0-1 基线快照](#p0-1-提交和标记当前基线)）。

### 2.2 已确认的结构风险

- Group Chat Plus 约 54,590 行 Python，`main.py` 单文件约 689 KB。
- Group Chat Plus 同时承担群聊、私聊、主动聊天、上下文、Web 面板、工具提醒、消息聚合和回复策略。
- 群聊与私聊存在并行的 Context Manager / Proactive Manager，容易产生行为漂移。
- 当前启用插件中有大量插件包含 `on_llm_request` 处理，请求结果依赖钩子顺序和临时标记。
- Group Chat Plus 只有少量自动化测试，规模与测试保障明显不匹配。
- ImageContextPool、Debounce、Shared Context 等关键边界缺少独立契约测试。
- 普通回复、主动聊天、图片分析和工具续轮没有统一的请求对象与幂等标识。

### 2.3 当前工作区保护

- [x] 不覆盖或回退当前未提交的 P1 文件（本轮保留现有用户改动，提交时按路径暂存）。
- [x] 重构提交不得混入 persona、数据库、QQ 登录态、知识库正文或 secrets（本轮只提交公共代码、合成 fixture 和文档）。
- [x] 每个重构提交只处理一个职责，禁止顺手清理无关文件（本轮新增项集中于观测、审计和 P2 纯策略内核）。
- [x] 删除任何旧路径前，先保留一轮可运行版本和明确回滚入口（本轮只清理已确认为空的误拼写目录，未删除旧 manager）。

### 2.4 2026-08-29 Agent Loop 性能基线

以下数据来自 Codex Provider `usage.db` 的数值聚合，不包含 prompt、persona 或会话正文：

| 指标 | 基线值 |
|---|---:|
| Provider turn 记录 | 3,199 |
| 输入 Token | 14,528,783 |
| 命中缓存 Token | 2,817,024 |
| 输出 Token | 967,493 |
| 加权缓存命中率 | 19.39% |
| 请求命中率简单平均 | 11.52% |

最近 24 小时工具样本：

| 指标 | 基线值 |
|---|---:|
| 有工具调用的 Agent 轮次 | 27 |
| 一轮返回多个工具的轮次 | 2 |
| 单轮最大工具数 | 5 |
| 高频工具 | `send_meme` 17、`search_meme` 9、`analyze_star_field` 4 |
| 达到 `max_agent_step` | 0 |

解释限制：

- turn 包含主 Agent、DecisionAI、图片转述和记忆后台调用，不能等同于用户消息数；
- 后续指标必须按 route 分开统计；
- 流量重复度和请求长度会影响缓存命中，不能只追求百分比；
- 若 24 小时样本不足，以 7 天加权值和 uncached input 绝对量为准。

### 2.5 当前关键运行配置基线

| 配置 | 已观测值 | 计划影响 |
|---|---:|---|
| `streaming_response` | `false` | 暂时保持，保护 Output Audit 和分段链路 |
| `max_agent_step` | `30` | 计划降到 8，先做回归和回退 |
| `tool_call_timeout` | `120s` | 慢工具需要独立状态和超时分类 |
| `tool_schema_mode` | `full` | 继续作为基线，skills-like 只做隔离对照 |
| `context_limit_reached_strategy` | `llm_compress` | 可能增加独立压缩调用，必须使用独立 route |
| Codex backend | `transport` | 使用 Responses HTTP/SSE |
| Codex harness | `lightweight` | 保持 |
| Codex concurrent turns | `2` | 2 核 4 GB 实例不盲目提高 |
| Group Chat Plus 主上下文 | `40` 条 | 保持，后续转为 ContextSection 预算 |
| 消息安静窗口 | `3000ms` | 保持 |
| ImageContextPool | 30 条、TTL 21600s | 保留索引多、原图少策略 |

实施前必须从线上重新读取配置；表中数值是基线，不是永远不变的事实。

### 2.6 AstrBot 4.27.4 的真实 Agent Loop

```text
消息事件
  → 防抖 / 群聊判断
  → build_main_agent
  → on_llm_request 钩子链（主请求开始时一次）
  → Runner.reset 组装 messages
  → round 0：messages + tools → 模型
      ├─ 无 tool_calls → 最终回复
      └─ 有 tool_calls → 执行工具并追加 assistant/tool messages
  → round 1..N：完整历史 + tools → 模型
  → on_llm_response → 审核/分段/发送/保存历史
```

已纠正的旧判断：

- 当前内部 `ToolLoopAgentRunner.step()` 不会在每个工具续轮重新执行整条 `on_llm_request`；
- Iris、画像、好感度和 ContextAware 通常在主请求开始时收集一次；
- 工具续轮的主要开销是完整历史、工具 schema 和首次动态上下文的重放；
- 插件自行调用 `text_chat()`、`llm_generate()` 或 `request_llm()` 属于另一条独立请求，必须单独标 route；
- 旧 `docs/todo.md` 中“工具下一轮会再次执行整条 Hook 链”的描述已经废弃。

合理模型轮次：

| 场景 | 合理轮次 |
|---|---:|
| 普通文字，无工具 | 主 Agent 1 次 |
| 一个工具后回答 | 主 Agent 2 次 |
| `search_meme → send_meme` | 主 Agent 2～3 次 |
| `analyze_star_field → send_annotated` | 主 Agent 2～3 次 |
| DecisionAI + 普通主回复 | decision 1 次 + main 1 次 |

### 2.7 当前缓存前缀规则

缓存友好的请求顺序：

```text
稳定 instructions
  persona / 永久安全边界 / 固定输出约束

稳定 tools
  名称 / 描述 / 参数 schema / 固定排序

历史前缀
  已完成 user / assistant / tool 消息

动态尾部
  当前消息 / Iris 检索 / 画像 / 好感度 / 图片索引 / 工具结果
```

必须避免：

- 当前时间、情绪、用户 ID 等实时值进入稳定 system 前缀；
- 工具顺序、JSON key 顺序和空白格式每轮变化；
- `tools` schema 与动态文本中的完整工具清单重复；
- 同一 `extra_user_content_parts` 被追加两次；
- main、decision、proactive、vision 共用错误 cache family；
- 为提高命中率删除 persona、Iris 或 ContextAware 的核心能力。

## 3. 不可破坏的产品约束

- [x] 一次用户意图最多创建一个主回复请求（P0 合成回放；真实实例仍待验收）。
- [x] 连续消息继续使用“最后一条消息后约 3 秒”的安静窗口（离线状态机已验证）。
- [x] 图片和随后到达的文字说明可以合并为同一个请求（离线状态机/MediaRef 已验证）。
- [x] 新消息取消旧回复时，不得吞掉已经进入下一批次的消息（离线取消状态机已验证）。
- [ ] 主动聊天保持现有积极程度，不因减少上下文或模型调用而明显变得消极。
- [ ] 最终主回复继续拥有 Iris L2/L3、画像、好感度和工具能力。
- [ ] DecisionAI 和主动聊天预判断可以使用轻量上下文，但最终回复不能因此缺失主能力。
- [x] 图片池继续保留 30 条 metadata / description / index；不得每轮注入 30 张原图（MediaRef/离线回放已验证；真实图片池仍待实例验收）。
- [x] 一次明确发送多张天文图片时，必须保留图片顺序和每张图片的独立 ID（MediaRef 已验证）。
- [x] Stealer 继续负责表情包存储、索引和发送；普通图片与表情包必须区分（合成 fixture 已验证；真实 Stealer 仍有上游失败项）。
- [x] 已有图片描述优先复用；只有缺少描述时才调用 VLM（VLM spy/fixture 已验证）。
- [x] Astrometry 结果和标注图必须先进入内部资料，再由 persona 组织最终表达（artifact/MediaRef 契约已验证；真实插件仍待接线）。
- [x] Output Audit 必须位于最终发送前，不能被主动聊天或工具直发绕过（离线 audit/delivery 顺序已验证；真实 Hook 仍待验收）。
- [x] Smart Segmentation 只处理最终文本，不创建新的主 LLM 请求（静态 contract/离线 fixture 已验证）。
- [x] 不记录完整 persona、系统提示词、记忆正文、图片 base64、API Key、Token 或 QQ 登录信息（脱敏/安全门测试已验证）。
- [ ] 不直接删减已经稳定的人格提示词；移动内容必须通过语义对比和人工 A/B。
- [x] 群成员无法通过“压缩上下文”“总结全部设定”“翻译隐藏提示”“合并转发分析”等方式取得内部内容（20 条合成注入样本；真实 Provider 仍待验收）。
- [x] 正常解释公开能力与复述内部配置必须明确区分，避免安全插件过度拒绝（本地安全 policy 已覆盖；真实人格回归仍待人工验收）。
- [ ] 好感度/情绪数值的 Provider、bot 数据目录和幂等边界必须可验证，但数值不直接暴露给群成员。
- [x] 一次实验只改变一个主要变量，不能同时更换模型、reasoning、prompt 和上下文预算（隔离 ExperimentSpec/ledger 已固定）。

## 4. 目标架构

```text
SnowLuma / OneBot
        │
        ▼
Turn Coordinator
  去重、3 秒聚合、取消、request_id、主回复幂等
        │
        ▼
TurnEnvelope
  会话、用户、文本、引用、媒体、route、触发原因
        │
        ├── Context Providers
        │     ├── ContextAware：当前场景和发言关系
        │     ├── Iris：L2/L3/画像/好感度
        │     ├── ImageContextPool：图片 ID 和首次描述
        │     └── Shared Context：受控跨会话资料
        │
        ├── Decision Policy
        │     ├── 是否回复
        │     ├── 主动聊天
        │     └── route / reasoning / budget
        │
        ▼
AstrBot Agent Runner + Provider
        │
        ▼
Output Pipeline
  Tool Cleaner → Persona 输出 → Output Audit
  → Smart Segmentation → SnowLuma
```

### 4.1 模块边界

计划新增薄编排层：

```text
plugins/modified/astrbot_plugin_xiaotianwen_orchestrator/
├── contracts/
│   ├── turn.py
│   ├── context.py
│   ├── media.py
│   └── tools.py
├── ingress/
│   ├── deduplicate.py
│   ├── debounce.py
│   └── cancellation.py
├── context/
│   ├── assembler.py
│   ├── budgets.py
│   └── providers.py
├── decision/
│   ├── reply_policy.py
│   ├── proactive_policy.py
│   └── route_policy.py
├── media/
│   ├── registry.py
│   ├── resolver.py
│   └── vlm_cache.py
├── output/
│   ├── delivery.py
│   └── idempotency.py
├── compatibility/
│   └── group_chat_plus.py
└── main.py
```

约束：

- Orchestrator 只负责编排，不复制 Iris、Codex、SnowLuma 或 AstrBot Core。
- Group Chat Plus 先保留外部入口和 Web 面板，内部职责逐项迁移。
- 其他插件通过明确接口注册 context provider、media resolver 或 tool policy。
- 不允许新 Orchestrator 演变为另一个包含所有业务实现的巨型 `main.py`。

## 5. 核心契约草案

### 5.1 TurnEnvelope

```python
TurnEnvelope(
    request_id: str,
    session_id: str,
    route: str,
    trigger: str,
    sender_id: str,
    text: str,
    reply_to: str | None,
    media: list[MediaRef],
    received_at: float,
    batch_started_at: float,
    metadata: dict,
)
```

- [x] `request_id` 在一次用户意图的全部阶段保持一致（P1 fake runtime correlation 测试）。
- [x] route 只允许 `chat/agent/decision/proactive/vision/background`（contracts route 白名单测试）。
- [x] 文本、引用和媒体在进入模型前只标准化一次（event-to-envelope/MediaRef 测试）。
- [x] 不把平台原始事件对象直接写入长期持久化结构（结构化观察只保留摘要）。

### 5.2 ContextSection

```python
ContextSection(
    source: str,
    priority: int,
    content: str,
    max_chars: int,
    cache_scope: str,
    version: str,
    sensitive: bool,
)
```

- [x] Context provider 返回 section，不直接任意改写整份 `ProviderRequest`（registry/adapters）。
- [x] Assembler 统一排序、预算、去重和空 section 清理。
- [x] 同一 source 在一个 request 中最多注入一次。
- [x] section 指纹不包含正文日志，只保存 source/version/length/hash。
- [x] 动态内容稳定放在请求尾部，persona 和工具规则保持稳定前缀（local assembler policy）。

### 5.3 MediaRef

```python
MediaRef(
    media_id: str,
    kind: str,
    message_id: str,
    sender_id: str,
    order: int,
    description: str | None,
    local_path: str | None,
    source_url: str | None,
    artifacts: list[str],
    content_hash: str | None,
)
```

- [x] 普通图片、表情包、星图和标注图共享引用协议，但保留不同 kind。
- [x] 每张图片拥有稳定 ID，多图保持原消息顺序。
- [x] 首次 VLM 描述按内容 hash、provider 和 prompt version 缓存。
- [x] Astrometry job、标注图和本地 artifact 通过 media ID 关联。
- [x] 文件已清理或失效时返回可恢复错误和可用 ID，不让 Agent 盲重试路径。

### 5.4 ToolExecutionPolicy

```python
ToolExecutionPolicy(
    effect: str,          # pure/read/write/send
    parallel_safe: bool,
    retry_safe: bool,
    cache_ttl_seconds: int,
    max_result_chars: int,
)
```

- [x] 未声明策略的工具默认视为 `write`，串行、不可缓存。
- [x] `send/write/delete/status` 不允许复用跨请求成功结果。
- [x] 只有互不依赖且全部 `parallel_safe=true` 的 read 工具可以有界并发。
- [x] 并发结果仍按原 `tool_call_id` 顺序回填。
- [x] 发送工具使用 request-local 幂等键，避免同一轮重复发图或表情包。

## 6. P0：冻结基线与建立安全网

### P0-1 提交和标记当前基线

- [x] 审阅当前 P1 diff，只暂存本轮确认文件。
- [x] 运行 Codex Provider 完整 pytest（102 passed）。
- [x] 运行 `compileall`、JSON schema 结构/字段校验和 `git diff --check`。
- [x] 创建独立 commit，不混入架构重构代码（`85c8b16`）。
- [x] push Public Repository（`origin/main` 已更新）。
- [x] 确认 Private Repository 无未提交实例变更，必要时单独 snapshot（工作区干净，`143239f` 与远端一致）。
- [x] 创建重构前 tag（`pre-orchestrator-refactor-20260831`，指向 `85c8b16`）。
- [x] 记录 Azure 当前 Public commit、Private commit、容器 image digest（见下表）。

#### P0-1 基线快照（2026-08-31）

通过 SSH 别名 `azure-xtw-01`（Azure VM `172.197.160.79`）读取；摘要不包含凭据、提示词正文或用户数据。

| 项目 | 基线值 | 说明 |
|---|---|---|
| Public Repository（本次基线） | `85c8b16` | 已推送到 `origin/main`；完整提交为 `85c8b16` 开头的 commit |
| Public Repository（Azure 当前已部署） | `850863e` | 服务器尚未切换到本次基线，作为重构前线上运行状态记录 |
| Private Instance Repository | `143239f` | Azure 与本地/远端 `origin/main` 一致，工作区干净 |
| AstrBot image | `soulter/astrbot@sha256:5d23f264ba9cb9b03a2bc1ef87f1ac87c03932aa99459b497afacb6a7c38aa8e` | 容器 `astrbot` 当前镜像摘要 |
| SnowLuma image | `motricseven7/snowluma@sha256:9bf55db08bf293e76807e675118b708cc5fc5b987918b15d13a9b65e8d610b43` | 容器 `snowluma` 当前镜像摘要 |
| 回退 tag | `pre-orchestrator-refactor-20260831` | Public commit `85c8b16` 的不可变标记 |

校验记录：Codex Provider 全量测试 `102 passed in 5.25s`；Python `compileall` 通过；`_conf_schema.json` 解析及 25 个字段结构检查通过；本次暂存文件的 secret 扫描无命中；`git diff --cached --check` 通过。当前环境未安装通用 `jsonschema` 包，因此采用项目实际 schema 格式的 JSON 解析与字段约束检查。

验收：

- 当前线上功能不变；
- 可用 tag、两个仓库 commit 和 image digest 恢复到重构前状态；
- Git 历史不含 secret、数据库正文或 QQ 登录态。

### P0-2 建立功能回放样本

- [x] 普通群聊文字一问一答（P0 synthetic replay）。
- [x] 3 秒内连续发送 3 条文字，只生成一份主回复（P0 synthetic replay）。
- [x] 先发图片、后发“看看这个”，只生成一份主回复（P0 synthetic replay）。
- [x] 一次发送多张天文图片并要求逐张分析（P0 synthetic replay）。
- [x] 普通图片与表情包分类（P0 synthetic replay）。
- [x] 已有图片摘要复用，不重复调用 VLM（P0 synthetic replay/VLM spy）。
- [x] `search_meme → send_meme`（P0 synthetic replay）。
- [x] `analyze_star_field → 按需发送标注图`（P0 synthetic replay）。
- [x] 主动聊天触发和不触发样本（P0 synthetic replay；真实 proactive 仍待验收）。
- [x] 私聊下达指令、群聊执行（P0 synthetic replay）。
- [x] 新消息取消正在生成的旧回复（P0 synthetic replay）。
- [x] Prompt injection 请求不泄露 persona、记忆或工具内部内容（20 条 synthetic injection replay）。
- [x] Output Audit allow/revise/block 三条路径（P0 synthetic replay）。
- [x] Smart Segmentation 的首句、换行和多段发送（P0 synthetic replay；真实分段 Hook 仍待验收）。

回放数据要求：

- 使用脱敏、人工构造或经授权的事件；
- 不提交真实 QQ 号、群号、Token、图片 base64、完整 persona 和用户记忆；
- 保存预期模型调用数、主回复数、工具调用序列和最终消息类型；
- 模型文本允许非确定性，结构和副作用必须可断言。

### P0-3 增加可观测性

- [x] 每个 Turn 生成 request_id（P0/P1 replay 与 fake runtime）。
- [x] 记录 route、阶段耗时、模型轮次、工具名称、工具耗时和结果长度（结构化 observation）。
- [x] 记录 ContextSection 的 source、字符数、hash 和是否命中缓存（结构化 observation）。
- [x] 记录收到图片数、发送原图数、复用摘要数和 VLM 调用数（MediaRef/replay observation）。
- [x] 记录最终发送幂等键和发送次数（delivery observation）。
- [x] 默认禁止记录正文、工具参数原文和敏感 URL（capture_text/脱敏默认关闭）。
- [x] trace 保留时间可配置，自动清理（TraceStore/RuntimeObservationStore retention）。

验收：

- 能从一次重复回复追踪到两个 request_id 或同一 request_id 的重复发送阶段（离线关联字段；真实重复回复仍待实例回放）；
- 能区分 DecisionAI、主动聊天判断、最终回复和记忆后台调用（route/role contract；真实运行 trace 仍待接入）；
- 日志中搜索不到 persona 原文、API Key、Authorization 和图片 base64（脱敏测试；真实日志仍待取样）。

### P0-4 重新审计 Hook、直连 LLM 和字段所有权

- [x] 输出当前启用插件的 Hook 清单：函数、生命周期、priority、读取字段、写入字段、是否阻断和副作用（AST 清单 + P2 HookContract；启用状态/运行顺序仍待实例）。
- [x] 单独列出插件内部的 `text_chat()`、`llm_generate()`、`request_llm()` 调用（静态 AST 清单）。
- [x] 区分主回复、decision、proactive judge、VLM、Iris background、compress 和其他 route（静态调用清单 + route policy；真实调用归类仍待实例）。
- [x] 查明 Debounce 与 Recall Cancel 的当前执行顺序和共享状态：AstrBot 按 priority 降序、同 priority 保留注册顺序；两者均为 `100`，没有共享插件内状态或请求字段写入，只通过 event stop 与平台生命周期相交；真实同时 recall/new-message 竞态仍归入实例回放门禁。
- [x] 查明 Group Chat Plus 当前 `priority=-100000` 收束逻辑的全部字段恢复行为：机器审计覆盖 `prompt/system_prompt/contexts/extra_user_content_parts/image_urls/audio_urls/func_tool` 和本轮 extra 清理，见 `docs/HOOK_ORDER.md`；值级行为仍由真实 ProviderRequest 回放验收。
- [x] 查明 AntiPromptInjector 前置/最终检查与 Output Audit 的实际边界：AntiPromptInjector 仅在请求侧且可直接拒绝发送；其 `999` “最终检查”按降序规则实际不是链尾。Output Audit 在 `90` 禁流并于 `-90` 修改完整候选且不直发。P2 仍需迁移直接拒绝发送，不能把本项勾选理解为生产安全链已收敛。
- [x] 为 hook 顺序和字段所有权生成 `docs/HOOK_ORDER.md` 或机器可读 manifest。
- [x] 插件升级后自动检测新 Hook、priority 漂移和新增默认 priority（`audit` profile 对 baseline fingerprint 做漂移阻断）。

注意：目标不是继续用更多 priority 精细控制混乱链路。显式顺序只能作为迁移期安全网，最终应由 Context Provider/Assembler 契约取代大部分共享请求写入。

验收：

- 随机改变插件发现顺序时，关键 Hook 顺序仍确定；
- 能回答每个字段最后由谁拥有；
- 能识别独立插件 LLM 请求，不把它误算成 Agent 工具续轮；
- Hook 审计不包含完整请求正文（静态审计和 manifest 已验证；真实运行 trace 仍待接入）。

### P0-5 安全与人格基线

- [x] 建立不少于 20 条 prompt injection 测试（当前 20 条 synthetic catalog）。
- [x] 覆盖直接命令、角色扮演、上下文压缩、翻译转述、代码块包装、合并转发、图片文字和多轮诱导。
- [ ] 保存 20～30 个经脱敏的代表性人格输出，由人工标注语气、关系感、记忆准确性、主动性和分句方式。
- [x] 验证合并转发中的指令被当作材料，而不是系统指令执行（synthetic security replay）。
- [x] 验证后审查模型只接收候选输出和风险标签，不接收完整系统提示和其他用户记忆（SecurityBoundary reviewer payload）。
- [x] 记录风险类型和审查成本，不记录敏感原文（结构化标签/hash/Token 计数；真实 reviewer 仍待实例）。

直接失败条件：

- 输出系统提示词、人格原文、隐藏上下文、工具内部说明或其他用户记忆；
- 安全插件使普通总结、写作或技术问题大面积误拒绝；
- 拒绝文本暴露内部规则编号、插件实现或敏感判断细节。

## 7. P1：统一请求与上下文协议

### P1-1 创建 contracts 包

- [x] 实现 `TurnEnvelope` 数据模型和校验。
- [x] 实现 `ContextSection` 数据模型和校验。
- [x] 实现 `MediaRef` 数据模型和序列化。
- [x] 实现 `ToolExecutionPolicy` 数据模型。
- [x] 为每个模型增加单元测试和非法输入测试。
- [x] 模型不依赖 Group Chat Plus 内部类。

验收：

- contracts 可以被 Group Chat Plus、ContextAware、ImageContextPool 和测试独立导入；
- 没有导入 AstrBot 私有实现路径；
- JSON/字典序列化不会包含不可序列化的平台对象。

### P1-2 创建 Turn Coordinator 影子模式

- [x] 从 OneBot/AstrBot 事件生成 TurnEnvelope。
- [x] 实现消息 fingerprint 和短 TTL 去重。
- [x] 实现 3 秒安静窗口，但影子模式不创建主回复。
- [x] 实现新消息取消状态机，但影子模式不实际取消旧请求。
- [x] 提供与当前 Group Chat Plus 结构观察记录的对比器（实际回放接入仍待完成）。
- [x] 提供有界、仅内存的脱敏差异记录，不影响线上回复。

需要区分的状态：

```text
COLLECTING → READY → REQUESTING → TOOL_LOOP → RESPONDING → COMPLETED
     │          │          │             │
     └──────────┴── new message ─────────┘
                    ↓
                CANCELLED / MERGED
```

验收：

- 典型回放中影子批次划分与当前预期一致率达到 100%；
- 图片和紧随其后的说明文字属于同一 Turn；
- 同一 OneBot event 重放不会创建第二个 Turn；
- 影子模式关闭后不留下定时任务和缓存。

### P1-3 创建 Context Assembler 影子模式

- [x] 为 ContextAware 建立只读 adapter，返回 ContextSection。
- [x] 为 Iris 建立只读 adapter，返回 L2/L3/Profile/Affection sections。
- [x] 为 ImageContextPool 建立只读 adapter，返回图片 ID 和摘要 section。
- [x] 为 Shared Context 建立只读 adapter，并允许实例配置关闭。
- [x] 统一 section 顺序、预算、去重和指纹。
- [x] 同时计算旧 payload 和新 payload，只记录脱敏结构差异。
- [x] 不在影子模式调用第二次 LLM、embedding 或 VLM。

建议默认顺序：

```text
稳定 persona / 安全规则
稳定工具规则
短期历史
当前场景
Iris 检索结果
画像与好感度
图片 ID / 摘要
当前用户消息
```

验收：

- 同一 source 不重复出现；
- persona 和稳定工具前缀保持字节稳定；
- DecisionAI 不携带主回复专用 L2/L3/画像/工具全文；
- 最终主回复仍能获得配置允许的全部能力；
- 图片摘要不重复触发 VLM；
- 工具续轮不重新执行整条 context provider 链。

#### P1 本轮实现说明（2026-08-31）

- [x] 在 `plugins/modified/astrbot_plugin_xiaotianwen_orchestrator/` 建立纯 Python 契约、影子协调器、只读 adapter、结构比较和三批独立测试。
- [x] 默认不注册 AstrBot ingress/LLM Hook，不创建定时任务，不发送 QQ 消息，不调用 LLM、embedding、VLM、Iris 检索或工具。
- [x] 本地 P1/P2 单元测试共 58 项通过；运行命令和每批边界见插件 `README.md` 与 `tests/README.md`。
- [x] 增加 disposable fake runtime：覆盖流式 Provider、tool continuation、shadow 不发送、`NOT_CONNECTED` 和安全边界，并接入 `tests.harness.cli --profile integration`。
- [x] 在隔离 Python 3.12 环境中用 AstrBot 4.27.4 的真实 `ProviderRequest`、`LLMResponse`、`TokenUsage` 和 `ToolSet` 实体完成字段适配回归；这仍不等同于真实网络 Provider 请求。
- [ ] 从当前 Group Chat Plus/ContextAware 导出脱敏真实回放结构并接入 shadow observer。
- [ ] 用实际实例完成“图片 + 紧随文字”、重复 OneBot event、群聊并发和取消竞态回放。
- [ ] 完成 24 小时无额外请求的 Shadow Gate；在此之前禁止开启 canary 或替换生产主回复路径。

### P1-4 建立媒体注册表

- [x] 将 ImageContextPool 快照映射到 MediaRef。
- [x] 保留现有 image ID，禁止无迁移说明地重新编号。
- [x] 增加多图 message_id/order 关联。
- [x] 增加 VLM cache key：`content_hash + provider + prompt_version`。
- [x] 增加 Astrometry artifact 和 annotated image 关联。
- [x] 增加按 ID 获取原图/标注图的只读解析边界。
- [x] 增加媒体文件缺失、过期和已清理的 metadata-only 恢复提示。
- [x] 不把远端临时 URL 当作唯一恢复来源。

验收：

- “这张”“第一张”“刚才的标注图”可以解析为确定的 media ID；
- 同图重复发送时可复用摘要，但仍保留新消息关联；
- 多图分析不会只保留最后一张；
- 媒体清理后，记忆中仍保留 ID 和摘要，不保留失效 base64。

本轮离线验收：`test_media_registry.py` 覆盖以上 ID/order、图文引用、摘要复用、标注关联、清理后 metadata-only 和 VLM key 行为；真实 ImageContextPool 实例接入和文件恢复仍待 P1 集成测试。

### P1-5 逐步接管主回复创建

- [x] 增加 `orchestrator_mode=shadow|canary|active|disabled` 的纯策略解析。
- [x] canary 只对白名单私聊或测试群生效。
- [x] canary 策略下同一 event 只登记一个主回复 owner。
- [x] 主动聊天、普通回复和图片回复通过同一 TurnEnvelope 契约。
- [x] 每个 Turn 只有一个最终 delivery owner。
- [x] 新消息取消逻辑以 request_id 为单位，不使用模糊的全局 flag。
- [x] 失败时可按 session 回退旧 Group Chat Plus 路径。

验收：

- 100 次连续消息回放无双主请求、无吞消息；
- 图片 + 文字只生成一份最终回复；
- 取消中的旧回复不会在新回复之后迟到发送；
- 主动聊天不会触发旧路径和新路径各一份回复。

本轮离线验收：ownership/delivery 测试覆盖模式解析、canary 白名单、同 event 单 owner、request 级取消、session fallback 和幂等 delivery；真实 AstrBot hook priority、100 Turn 实例回放和主路径切换仍未完成。

### P1-6 部署并验证当前 Agent Loop P0/P1 改动

已经本地实现、待提交和线上验证：

- [x] 明确识别 Runner 已组装的动态上下文，避免 `extra_user_content_parts` 再追加；
- [x] 对工具 schema 做稳定排序和 canonical JSON；
- [x] 按 model/route/instructions/tools 生成稳定 64 字符 cache key；
- [x] 原生 tools schema 存在时跳过 Group Chat Plus 工具全文提醒；
- [x] 路由级 reasoning effort 和 max output tokens；
- [x] 工具结果统一 `max_tool_result_chars` 兜底；
- [x] transport 默认最新 1 张原图，明确多图语义保留整组。

部署任务：

- [x] 提交并 push 当前 Provider/Group Chat Plus 修改（public `810d284`）。
- [x] 部署前备份 Azure 实例配置和插件运行数据（2026-08-31 `xiaotianwen-instance-20260831-002816.tar.gz`）。
- [x] 将本轮公共测试框架/P1/P2 dormant 代码同步到 Azure `c1b6bd1`；停服前创建私有归档和运行时回退快照，使用 `RESTORE_INSTANCE=0` 保留现有实例，`deploy/verify.sh` 通过且两容器 restart count 为 0。
- [x] 在离线 transport 测试会话验证动态块、图片和 tools 各只出现一次；真实 Azure 会话仍由发布门禁复验。
- [x] 以只含长度、计数和指纹的脱敏 diagnostics 验证 instructions/tools/cache key 稳定，不保存正文。
- [x] 验证 direct provider 调用在 contexts 未包含 extras 时仍追加一次动态内容。
- [x] 验证真实用户重复说相同文本时不会被错误去重；并修正“长文本完全相等”误判边界。
- [x] 验证多模态 message 不丢 image/audio/reply/file/video part。
- [x] 验证工具续轮只新增 assistant tool call 和 tool result。
- [ ] 观察 24 小时 route、Token、cache、P50/P95 和错误率。
- [ ] 未通过时回滚插件 commit，不清理 Iris、图片池或数据库。

Payload 验收：

- [x] 每个 Iris/ContextAware/Affection/ImageContextPool section 最多一次（ContextAssembler 离线契约）；
- [x] Responses `tools` 正常存在且按 schema 稳定排序；Group Chat Plus 的完整“可用工具列表”线上缺失仍由 Azure payload 门禁复验；
- [x] tools hash 在插件未变更时保持稳定；
- [x] 用户名、好感度、当前时间和 L2 结果变化不改变稳定 cache family；
- [x] persona 或工具 schema 更新时 cache key 必须变化；
- [x] main/chat、decision、proactive、vision 使用不同 route cache family；
- [x] 图片 data URI 不在同一 payload 重复。

2026-09-02 离线验收：Codex Provider/transport 相关 67 项测试通过；Orchestrator 58 项中 57 通过、1 项因本机未安装 AstrBot 而明确跳过；仓库 `full-offline` 回放 43/43，通过的 7 个插件矩阵项无失败、无网络/写入违规。Astrometry、Iris、Stealer 和真实 AstrBot/QQ/SnowLuma 仍为 `NOT_RUN`，不由上述结果推断生产通过。

性能阶段目标：

- 动态块去重后 uncached input 下降至少 15%；
- P0 完成后加权缓存命中目标至少 35%；
- P1 完成后目标 50%，或 uncached input 相对基线下降至少 30%；
- 无工具主回复 Provider 调用固定为 1 次；
- 单工具任务除必要两轮外无额外请求；
- 重复最终回复、重复副作用和 400 自动双发均为 0。

### P1-7 Iris、场景和私聊记忆预算

- [x] 通过请求级 memo 为 L1 预留短近期上下文，避免同一 request 重复装配。
- [x] 为 MemoryQueryKey 提供 memory/provider 版本维度的短 TTL single-flight。
- [x] 单字、纯 @、戳一戳等低信息消息先标记为等待合并，再决定是否检索。
- [x] 每个 section 使用独立字符/token 预算，为空时不输出标签。
- [x] 主 Agent 工具续轮复用已有 context，不重新装配 provider 链。
- [x] 只有显式 memory_refresh 且每个 request 最多一次时才刷新。
- [x] 群聊和私聊使用不同预算；私聊以更大的字符预算为主。
- [ ] 私聊窗口淘汰的重要内容摘要或结构化写入 L2，而不是直接丢弃。
- [x] 画像、好感度和 L2 描述同一关系时保留 Iris 权威 section，移除泛化重复 section。
- [x] Iris 影子日志只保留 section 长度、耗时、hash 和命中 ID 等结构字段。

不得重新串行化 Iris 已有的 L1/Profile/L2/Learning 并行阶段；优化必须通过缓存、single-flight 和预算完成。

### P1-8 Route 推理与感知延迟 A/B

- [x] `decision/proactive` 从 `none` 或 `low` 开始，输出限制为 yes/no + 短 reason。
- [x] `vision` 使用短输出预算，不生成完整人格长回复。
- [x] 主聊天以 `low` 为延迟基线，复杂搜索和长分析再升到 `medium`。
- [x] 每个 route 记录质量通过率、Token 和 P95。
- [x] 暂时保持 `streaming_response=false`。
- [x] 本地状态策略在超过 3 秒时只生成按 `request_id` 关联的 show/update/retract 意图，不调用额外 LLM；真实平台状态 API 仍待接线。
- [ ] 星图解析显示“正在解算”，最终只发送 persona 化回复和按需图片。
- [x] 本地状态协调器与最终请求共享 `request_id`，完成时更新、取消时撤回；真实 SnowLuma/AstrBot 出站状态仍待验收。
- [ ] 可见 streaming 另建 A/B，必须同时验证 Smart Segmentation、Output Audit、Group Chat Plus 和 SnowLuma。

## 8. P2：拆分巨型插件与收敛钩子

### P2-1 拆分 Group Chat Plus 内部模块

按以下顺序迁移，每一项独立提交：

- [x] 抽出事件 fingerprint、消息去重和 debounce（Orchestrator P1 内核；Group Chat Plus 旧路径尚未切换）。
- [x] 抽出 cancellation 和 pending Turn 状态（Orchestrator P1 内核；真实 Hook 竞态仍待实例回放）。
- [x] 抽出 Context Manager 公共逻辑（统一 ContextAssembler/adapter 内核）。
- [x] 合并群聊/私聊共享的 context 基础实现，策略参数化（本地共享契约；真实旧 manager 尚未删除）。
- [x] 抽出主动聊天 policy，不直接构造虚拟事件调用整条钩子链（`p2/proactive.py` 纯决策内核；真实链路未切换）。
- [x] 合并群聊/私聊共享的 proactive 基础实现（同一 `ProactivePolicy`；真实行为等价性仍待实例验收）。
- [x] 抽出 route/reasoning/context budget policy（本地策略内核）。
- [x] 抽出图片、引用和合并转发解析（图片/引用已进入 MediaRef；合并转发真实适配仍待完成）。
- [x] 抽出工具提醒和工具权限策略（本地 ToolRegistry/SecurityBoundary；旧 Hook 尚未切换）。
- [x] 抽出 delivery 幂等和发送状态（本地 delivery owner/状态内核）。
- [x] Web 面板通过稳定 service API 读取状态，不导入内部 manager（已提供只读 `OrchestratorService`；实际页面接线另行验收）。
- [x] `main.py` 最终只保留注册、配置、生命周期和兼容入口（Orchestrator shadow shell 已满足；Group Chat Plus 旧 `main.py` 仍待迁移）。

模块限制：

- [x] 单个新 Python 模块原则上不超过 800 行；
- [x] 超过 800 行时必须解释职责为何不能继续拆分（本轮没有超过 800 行的新增模块）。
- [x] 业务代码不能反向导入 Web 层；
- [x] 群聊和私聊不得复制整套 manager（本地内核共享；旧 manager 尚未删除）。
- [x] 禁止新增运行时 monkey-patch。

### P2-2 收敛 `on_llm_request` 所有权

- [x] 列出每个插件当前钩子的读取字段、写入字段、priority 和副作用（声明式 `HookContract` 清单；真实运行时 trace 仍待完成）。
- [x] Context provider 改为注册接口，不再任意改写完整 req（本地 `ContextProviderRegistry`）。
- [x] ImageContextPool 不重复注入已有 ContextAware 图片摘要（隔离 registry 按 source/content hash 去重；真实插件交叉回放仍待完成）。
- [x] 本地 Context 契约将 Affection/Iris 关系信息限定为明确 section，避免同义重复；真实 Affection 数据仍待实例验收。
- [x] 本地 Shared Context adapter 默认关闭，并按 source/内容指纹去重；实例启用和场景不重复仍待真实回放。
- [x] Astrmetry 只作为工具/媒体 artifact provider，不直接输出最终 persona 文本（本地 contract 已固定；真实 Hook 尚未切换）。
- [x] Tool Use Cleaner 保持输出/协议清理职责，不参与主上下文竞争（本地 contract 已固定；真实 Hook 尚未切换）。
- [x] Output Audit 保持最终出站门，不参与普通上下文拼装（本地 contract/安全门已固定；真实发送链仍待验收）。
- [ ] Group Chat Plus 兼容钩子在迁移完成后关闭（当前仍保持旧路径，避免未经 canary 切换）。

目标：

- 主回复请求只有一个统一 assembler owner；
- 其他插件通过注册接口提供结构化数据；
- 独立安全门和最终分段仍保持插件隔离。

### P2-3 工具执行策略和幂等

- [x] 建立可扩展的本地工具 effect 清单和保守默认值（真实插件全部工具与运行 trace 仍待补齐）。
- [x] 标记 `send_meme`、发图片、改状态、精华消息为 `send/write`（本地 effect policy）。
- [x] 标记纯搜索、只读状态和元数据查询为 `read`（本地 effect policy）。
- [x] 对只读工具增加 request-local single-flight。
- [x] 对 read 工具实现最多 3 路的有界并发实验。
- [x] 对 send/write 工具保持串行。
- [x] 工具结果按原 call_id 顺序回填。
- [x] 同一 request 内相同 send 参数只执行一次。
- [x] 不同图片 ID 或不同 meme ID 的明确多发送仍允许执行。
- [x] 超长工具结果先由工具自己瘦身，Provider 统一截断只作为兜底。

Core 边界：

- 优先使用上游支持；
- 若必须修改 AstrBot Core，创建 `patches/astrbot/<verified-version>/`；
- 应用补丁前验证 AstrBot 版本和目标文件 hash；
- `latest` 版本不匹配时拒绝应用，而不是静默 monkey-patch；
- 补丁必须有单独回滚和上游兼容跟踪。

### P2-4 删除旧路径和清理目录

- [ ] canary/active 连续稳定后，删除已经没有调用方的旧 manager。
- [ ] 删除前使用 `rg`、导入测试和运行 trace 确认无引用。
- [x] 清理空的 `public/xiaotwen` 误拼写目录。
- [x] 保留 NapCat 仅在历史归档，不重新加入运行链路。
- [x] 更新私有 `plugins.lock.yaml`：新增 Orchestrator 且保持 `enabled: false`，NapCat 入口继续标记 `retired/enabled: false`。
- [x] 更新部署、测试、Hook 顺序和 P1/P2 实现文档（新增 `docs/P1-P2-IMPLEMENTATION.md`）。
- [x] 按当前真实迁移边界更新 `docs/ARCHITECTURE.md`：区分生产兼容链、默认关闭的新编排链和切换门禁。
- [x] 本轮只修改公共代码/文档和私有插件锁，不删除数据库、图片池、表情包库和 Iris 数据；完整私有工作树已在开工前归档。

### P2-5 修复并验证 Affection / 情绪更新链路

历史快照曾出现 Provider 回退不一致、真实 bot ID 与 `default_bot` 目录混淆等风险。不得根据旧快照直接改配置，先读取当前实例和日志。

- [ ] 确认 Affection 当前加载状态、Hook 注册和后台任务数量。
- [ ] 在同一测试消息上记录脱敏 bot ID、用户哈希、message ID、任务启动、Provider ID、解析和写入结果。
- [ ] 确认真实 bot 数据目录与 Web 面板/命令读取目录一致。
- [ ] 检查 `default_bot` 是否仍被写入，查明每条 fallback 来源。
- [ ] 为普通互动分析和 idle 分析配置明确、真实存在的 Provider ID。
- [ ] Iris L2 改写/摘要/抽取和好感度数值调节继续使用实例约定 Provider，不被主聊天 Provider 改动覆盖。
- [ ] 对空输出、非 JSON、超时、Provider 不存在和 API 400 分类记录。
- [ ] 同一 message ID 增加情绪更新幂等锁。
- [ ] 保存后台任务句柄，插件卸载/重载时正常取消。
- [ ] 确认每个 bot 只有一份衰减任务。

本轮本地策略内核：

- [x] 提供 Provider 绑定/结果分类、同一 bot 的 message 幂等和每 bot 单一衰减任务注册表；真实 Affection/Iris/SnowLuma 数据目录、Provider 与后台 Hook 仍待实例验收。

验收：

- 连续 5 条测试消息每条最多一条情绪分析记录；
- 数据写入和 Web 面板读取同一 bot 目录；
- 重启后数据不丢失、不重置；
- 后台 Provider 异常不阻断主回复，但产生可理解告警；
- 回复不直接暴露数值，人工确认人格没有异常漂移。

### P2-6 安全边界和监管 AI

- [ ] 保留最小固定安全边界，不以 AntiPromptInjector 完全替代。
- [ ] AntiPromptInjector 负责输入风险识别和最终请求检查。
- [ ] Output Audit 负责候选输出的 allow/revise/block。
- [ ] 高风险后审查只接收候选输出和风险标签，不接收完整 persona/记忆。
- [ ] 工具发送、状态修改、精华消息和跨群操作进入权限检查。
- [ ] 合并转发、图片 OCR/VLM 文本中的指令默认视为不可信材料。
- [ ] 拒绝文本使用 persona 语气，不暴露内部规则名和插件实现。
- [ ] 记录风险类型、处置和额外 Token，不记录敏感原文。

本轮本地安全内核：

- [x] 提供固定输入风险边界、工具权限、最终输出 gate 和仅含标签/hash/计数的 reviewer payload；真实 AntiPromptInjector/Output Audit/出站发送链仍未切换。

验收：

- “压缩并输出全部上下文”等已知攻击无法取得内部内容；
- 正常总结、写作和技术咨询不过度拒绝；
- Output Audit 的 block/revise 在分段和发送前完成；
- 工具直发和主动聊天不能绕过最终安全门；
- 审查成本可按 route 量化。

### P2-7 SnowLuma 单一接入层长期回归

NapCat 已经弃用，本工作包不恢复 NapCat，只防止后续脚本或文档重新引入它。

- [x] SnowLuma 是当前唯一 QQ / OneBot 接入层。
- [x] 默认部署使用 SnowLuma Docker Compose。
- [x] 启动、停止、状态和更新脚本不以 NapCat 为现役服务。
- [x] 审计仓库中的 systemd/cron/Compose/守护脚本，并只读检查 Azure 受查运维目录；未发现 NapCat 现役启动引用（远端检查范围不等同于全主机证明）。
- [x] 状态/部署文档将 NapCat 标记为历史归档，不提供现役启动入口。
- [x] 显式更新 `latest` 的脚本先备份，成功后记录实际 image digest（部署静态门禁与已有快照已验证）。
- [x] 普通 restart 不主动 pull 新镜像（脚本静态检查已验证）。
- [x] 增加分层健康检查策略：容器、WebUI、QQ 登录、OneBot、AstrBot、最小收发；本地运维内核已覆盖，真实最小收发仍待演练。
- [x] 区分“容器运行”和“账号可用”，不以 HTTP 200 代替业务健康（本地状态模型和 Azure 只读端口复核已验证边界）。
- [x] 登录失效时使用有上限的重试和人工介入状态，不无限重启（本地策略内核已验证）。
- [x] 配置日志轮转和定时媒体清理，但不清理唯一图片索引/数据库来源（部署/迁移文档和本地策略已覆盖；真实定时任务仍待实例核查）。
- [x] QQ 数据库备份包含 SQLite 主文件及对应 WAL/SHM（备份 manifest 策略和静态测试已验证；真实快照内容不在公共仓库）。

本轮本地运维内核：

- [x] 提供分层健康状态、容器运行/账号可用分离、有限重试、SQLite 主文件/WAL/SHM manifest 和 active adapter 审计；真实 SnowLuma 72 小时演练仍未完成。

故障演练：

- [x] Azure 只重启 AstrBot，随后 Dashboard/OneBot/SnowLuma/noVNC 分层验证通过；
- [x] Azure 只重启 SnowLuma，随后四层验证通过；
- [x] Azure 同时重启全部现役服务，随后四层验证通过；
- [ ] 主机重启后自动恢复；
- [ ] 网络断开 5 分钟后恢复；
- [ ] QQ 登录失效后通过 noVNC 重新登录；
- [x] 重启演练后 SnowLuma 重新连接 `ws://astrbot:8001/ws`，AstrBot 记录 HTTP 101 和 OneBot v11 已连接；
- [x] 在 Azure 对已占用的 8001 端口启动第二绑定探针，Docker 拒绝第二实例且现役容器不受影响。

验收：

- SnowLuma 连续运行 72 小时，无第二接入实例争抢登录或重复事件；
- 网络恢复后自动重连，超过上限进入明确告警；
- 常见故障不需要删除整个 QQ 数据目录；
- 备份可以在另一台 Linux 主机恢复到合法登录或重新扫码流程。

### P2-8 可复现部署与迁移回归

- [x] 源码、实例数据、secrets 和可重建 runtime 已分层。
- [x] 公共/私有仓库均有部署说明。
- [x] 新机部署支持 Ubuntu 24.04、Docker Compose、AstrBot 和 SnowLuma。
- [x] Codex CLI 可以安装到 AstrBot 持久化挂载。
- [ ] 重新执行一次从空白 VM 开始的完整部署演练。
- [ ] 仅提供仓库访问和必要主机 secrets，30 分钟内恢复服务。
- [ ] 验证路径整体迁移后不依赖旧绝对路径。
- [ ] 验证 persona、Iris、知识库、图片池、表情包和插件配置恢复。
- [ ] 验证 SnowLuma 进入合法登录恢复或重新扫码流程。
- [ ] 验证 WebUI、OneBot、主回复、主动聊天、图片和工具。
- [ ] 记录失败步骤、人工操作和未自动化依赖。

备份等级：

1. 配置备份：脚本、脱敏模板、版本锁和文档；
2. 数据一致性备份：停止服务或 SQLite checkpoint 后备份 AstrBot/Iris/知识库/Stealer/SnowLuma；
3. 灾难恢复归档：完整实例和必要 runtime，低频生成。

每个归档应包含 manifest、时间、来源主机、服务状态、文件大小和 SHA256；恢复默认写入新目录，不直接覆盖现有实例。

### P2-9 隔离实验：不直接上生产

显式 Prompt Caching：

- [ ] 以 feature flag 探测 transport 是否接受 `prompt_cache_options` 和断点。
- [ ] 对比 implicit 与 explicit + TTL。
- [ ] 断点只考虑稳定 instructions 和稳定 tools 末尾。
- [ ] 记录 cache write、cache read、延迟和成本。
- [ ] 400 时立即关闭实验，不自动发送第二份不同 payload。

`previous_response_id` / persisted reasoning：

- [ ] 生产继续关闭。
- [ ] 隔离验证 `store=false`、encrypted reasoning replay 和 previous response 的真实兼容性。
- [ ] 验证 persona/tools/记忆更新后的失效语义。
- [ ] 验证重启、迁移、过期和 fallback。
- [ ] 完成 100 条无丢上下文、无 400、无重复回复回归后才讨论生产。

Skills-like 工具 schema：

- [ ] 用同一 50 条工具任务比较 `full` 与 `skills_like`。
- [ ] 记录 schema bytes、模型调用数、Token、P50/P95 和参数正确率。
- [ ] 节省输入不足以抵消额外 requery 时继续使用 `full`。
- [ ] 优先评估按意图暴露工具子集。

Programmatic Tool Calling：

- [ ] 仅评估多个只读搜索/过滤/聚合任务。
- [ ] send/write/status/steal 不进入批处理。
- [ ] 验证 output item、caller 和 call_id 关联。
- [ ] 以任务成功率为主，不把轮次减少本身视为成功。

本轮隔离实验与性能内核：

- [x] 提供默认关闭且禁止生产的 `ExperimentLedger`，遇到 HTTP 400 立即中止且不自动双发。
- [x] 提供 P50/P95、10% 回归阻断、100-slot 发送关闭 canary 计划，以及 24 小时 shadow/72 小时 SnowLuma 观测模板；真实 transport/实例数据仍待验证。

所有实验必须使用独立分支、隔离会话、明确回滚和单变量 A/B。

## 9. 测试计划

### 9.0 P0 本地离线测试框架实现状态（2026-08-31）

- [x] 建立 `tests/harness`、`tests/selftests` 和 `tests/ui`，不连接生产服务。
- [x] 提供 Python CLI、PowerShell/Bash 入口、fixture schema、虚拟时钟、确定性 ID、sandbox 和 network guard。
- [x] 提供 Provider/VLM/Embedding/Tool/Delivery/Storage/Logger/Hook probes 与 versioned observation。
- [x] 提供 23 条功能回放、20 条注入回放、12 条故障注入和结构化 Markdown/JSON/JUnit/diff 报告。
- [x] 提供显式 approved baseline 读取/比较/审批流程；普通 run 不会自动覆盖 Golden。
- [x] 提供 loopback-only Local Test Console、Input Composer、Request Explorer、Logs/Timeline、Output Inspector 和 Compare。
- [x] 完成 Windows 中文路径下的 CLI、HTTP API 和本机浏览器核心流程验证。
- [x] 通过 `tests/VALIDATION-2026-08-31.md` 记录实际结果、脱敏边界和未验证层。
- [x] P1：在独立 Python 3.12 环境补齐 AstrBot 4.27.4 entity/adapter 契约测试；真实 Provider、ContextAware 和实际 Hook 仍未接入。
- [x] P1：建立隔离 Fake OneBot/Fake Provider 实例并验证请求 observation、取消、tool continuation、`NOT_CONNECTED` 和安全边界；真实 AstrBot 插件发现/Hook 顺序仍未验证。
- [x] P2：提供性能趋势计算、长时 Shadow/Canary 和 24/72 小时观测模板；实际长时观测尚未运行。

### 9.0.1 私有实例本地测试接线状态（2026-09-01）

- [x] 启动前完成私有实例数据备份；测试过程不覆盖 SnowLuma 数据库、知识库、插件数据、会话、使用记录、secrets 或 Git 历史。
- [x] 将原私有 AstrBot 实例作为本地测试对象启动，WebUI 使用 `127.0.0.1:6399`，测试 OneBot 反向连接使用 `127.0.0.1:6403`。
- [x] Local Test Console 通过 loopback-only OneBot bridge 连接真实 AstrBot，完成合成私聊事件发送、AstrBot action 捕获和 output 展示；该输出标记 `sent_to_qq=false`，未触达真实 QQ。
- [x] 将测试事件白名单收敛为完整 session ID，验证合成事件能够进入 AstrBot Provider 链路。
- [x] 确认 Codex 浏览器登录态文件已经写入私有实例的隔离 `CODEX_HOME`；未读取或记录 token、Cookie、OAuth 回调参数和其他登录凭据。
- [x] 验证 Codex 插件设置页可以通过 `#/plugin-page/astrbot_plugin_chatgpt_codex/settings` 正常打开；账号概览 iframe 曾出现空白，设置路由本身可正常渲染。
- [x] 定位当前 Provider 阻塞为出站网络问题：AstrBot 直连 `chatgpt.com` 超时并产生 `WinError 10060`；不再把该错误归类为 WebUI、OneBot 或登录密码故障。
- [x] 只读验证当前主机代理路径能够到达 Codex endpoint；无凭据探测返回 HTTP 401，证明网络路径可达但不构成已认证 Provider 验收。
- [ ] 为 AstrBot 配置可访问且不含凭据的显式 `transport_proxy`，保存后确认设置已热更新或按提示重启测试实例。
- [ ] 代理生效后验证账号状态、模型列表、5 小时配额和一次合成消息 Provider 输出；日志不得再出现 `WinError 10060` 或 `Codex transport 网络请求失败`。
- [ ] 复现并修复账号概览 iframe 的偶发空白；设置页可打开不能代替概览页加载验收。
- [ ] 真实 QQ、SnowLuma、群聊、图片、Iris、工具调用、Shadow 24 小时和 Canary 100 Turn 仍为 `NOT_RUN` / `NOT_VERIFIED`，不得由本次 loopback 测试推断通过。

P0 完整 profile 当前正确结论为 `NOT VERIFIED`：23 条功能回放与 20 条注入回放全部通过，
Astrmetry、Iris Memory、Stealer 按隔离策略报告 `NOT_RUN`；AstrBot 4.27.4 实体适配已在
独立环境单测，但不等同于真实生产 Provider/Hook 链路。这不是生产链路验收结论。

最低统一测试矩阵：

| 类别 | 最低场景数 | 关键检查 |
|---|---:|---|
| 私聊普通对话 | 10 | 人格、近期历史、L2/L3、分句 |
| 私聊连续消息 | 5 | 3 秒窗口、一次 Turn、一次回复 |
| 群聊被动回复 | 10 | 指代、@/引用、40 条限制 |
| 群聊主动聊天 | 10 | 轻量预判断、主动性、完整最终回复 |
| 图片与表情包 | 8 | 分类、描述复用、VLM 回退、多图顺序 |
| 合并转发 | 5 | 统一 persona/后处理、内部指令不执行 |
| 工具调用 | 10 | 合理轮次、幂等、单一最终发送 |
| 安全注入 | 20 | 无内部提示、人格、工具和他人记忆泄漏 |
| SnowLuma/QQ 故障 | 8 | 单实例、登录、OneBot、网络和重启 |
| 迁移恢复 | 2 | 新路径、新主机完整恢复 |

### 9.1 单元测试

- [x] TurnEnvelope 校验、序列化和 route 白名单。
- [x] Debounce 状态机和显式时间推进测试。
- [x] Cancellation 竞态测试（P1 state machine and late-output replay）。
- [x] ContextSection 排序、预算和去重。
- [x] MediaRef 图文消息顺序和 OneBot 段解析。
- [x] 工具 effect 默认保守策略。
- [x] Delivery 幂等键（P1 delivery coordinator and P2 tool executor tests）。
- [x] 日志/观察记录脱敏。

### 9.2 契约测试

- [x] AstrBot 4.27.x 的 ProviderRequest 适配（隔离 AstrBot 4.27.4 实体字段回归；真实网络 Provider 仍未验证）。
- [ ] SnowLuma OneBot 图文、引用、合并转发和表情事件。
- [ ] Iris adapter 不修改记忆数据库。
- [x] Codex Provider 保留 Agent tool call 和 usage（现有 Provider 测试；真实实例仍未验证）。
- [x] Output Audit 必须位于最终发送前（本地 delivery/audit 顺序回放；真实发送链仍未验证）。
- [x] Smart Segmentation 不创建额外 LLM 请求（静态/合成回放；真实 Hook 仍未验证）。

### 9.3 回放与集成测试

- [x] 使用 P0 回放样本运行新影子/隔离 fake 路径；旧路径与真实 canary 仍未接线。
- [x] 比较 request 数、route、context section、tool 序列和发送副作用（结构化离线 comparator）。
- [x] 对非确定文本只比较结构和安全约束。
- [x] 对发送、写入、状态修改进行 mock，默认不触达真实 QQ。
- [ ] 真实 QQ 验证只在测试群和明确授权账号执行。

### 9.4 性能测试

- [ ] 普通文字回复 P50/P95 首响应和总耗时。
- [ ] DecisionAI P50/P95 和输出 token。
- [ ] 主动聊天判断 P50/P95。
- [ ] 单工具、双工具和五个只读工具的 Agent Loop。
- [ ] 图片摘要首次/缓存命中耗时。
- [ ] Astrometry 首次/缓存命中耗时。
- [ ] Provider weighted cache hit rate。
- [ ] uncached input token / user intent。

## 10. 灰度与发布门禁

### 10.1 Shadow Gate

- [ ] 影子模式至少运行 24 小时。
- [ ] 不创建额外 LLM、embedding、VLM 或工具请求。
- [ ] Turn 分批与旧预期一致率达到 100%。
- [ ] 无敏感正文日志。

### 10.2 Canary Gate

- [ ] 仅测试私聊或测试群启用。
- [ ] 至少完成 100 个 Turn。
- [ ] 无双回复、吞消息、迟到回复和重复 send 工具。
- [ ] Persona、Iris、图片 ID、表情包、Astrometry 均通过功能矩阵。
- [ ] Output Audit 和 Smart Segmentation 未被绕过。
- [ ] P95 延迟不高于旧路径 10%；若功能等价，目标应下降。

### 10.3 Production Gate

- [x] Azure Public / Private 部署工作区干净；本机 Private 仍保留用户运行数据变更，不做广泛提交。
- [ ] 自动化测试全部通过。
- [x] 部署前一致性备份完成并记录大小/SHA-256。
- [x] 当前容器 image digest 已记录于远端 `runtime/deployed-images.env` 和 `tests/VALIDATION-2026-09-02.md`。
- [ ] 新旧路径开关和回滚命令经过演练。
- [ ] 在非高峰时间发布。
- [ ] 发布后观察 2 小时实时日志和 24 小时指标。

## 11. 成功指标

功能正确性：

- 同一用户意图主回复请求数：`1`；
- 图片 + 紧随文字重复回复率：`0`；
- 同一 request 内重复发送副作用：`0`；
- 消息突发吞消息率：`0`；
- 图片 ID/摘要回放成功率：不低于旧系统；
- Output Audit 绕过次数：`0`。

性能与成本：

- weighted cache hit rate 目标：`≥ 50%`，或 uncached input 相对基线下降 `≥ 30%`；
- 普通回复 P95 总耗时不得恶化超过 `10%`；
- DecisionAI/主动预判断不携带主回复专用动态上下文；
- 已有图片摘要命中时 VLM 调用数：`0`；
- 同一确定性只读工具在一个 request 内重复执行数：`0`。

可维护性：

- Group Chat Plus `main.py` 不再承载完整业务实现；
- 群聊/私聊共享同一套基础 Context/Proactive 模块；
- 关键新模块均有单元测试；
- 一个插件升级不会依赖未记录的 hook priority 偶然顺序；
- 新开发者可以从 contracts 和架构文档理解完整请求生命周期。

安全、情绪和运维：

- 安全注入测试泄漏次数：`0`；
- Affection 同一 message ID 重复写入次数：`0`；
- SnowLuma 连续运行目标：至少 `72h`，无第二接入实例；
- 部署恢复目标：空白 Ubuntu VM 在提供必要凭据后 `30min` 内恢复；
- 备份恢复不依赖删除 QQ、Iris、图片池或表情包数据库。

## 12. 回滚方案

每个阶段必须满足：

- 新功能由 feature flag 控制；
- 数据迁移先读兼容、后双写、最后切换，不直接破坏旧格式；
- 关闭 Orchestrator 后旧 Group Chat Plus 可以继续工作；
- 回滚代码不删除新产生的数据；
- 数据 schema 变更必须有向后读取兼容或离线转换副本；
- Azure 发布前制作一致性备份；
- 回滚优先使用 Git tag、Public/Private commit 和已记录 image digest；
- 不通过清空 Iris、图片池、表情包库或 QQ 数据来解决代码回归。

触发自动回滚的条件：

- WebSocket 或主回复链路不可用；
- 同一 event 出现重复主请求；
- Output Audit 被绕过；
- send/write 工具重复执行；
- 图片/表情包/星图主要功能不可用；
- persona 或记忆上下文大面积缺失；
- 容器持续重启或数据库出现新损坏。

## 13. 明确不做

- 本轮以下约束均已作为实施边界执行：

- [x] 不重写 AstrBot。
- [x] 不把 SnowLuma 代码合并进本仓库。
- [x] 不重写 Iris 的 L2/L3 数据模型。
- [x] 不把 Codex Provider 做成仅小天文可用的私有实现。
- [x] 不把所有插件合并成单个超级插件。
- [x] 不以删除记忆、图片、表情包或 QQ 数据作为迁移步骤。
- [x] 不在本阶段引入 Kubernetes、多节点、高可用或自动扩缩容。
- [x] 不为了代码整洁同时更换模型、prompt、数据库和部署方式。
- [x] 不在缺少回放测试时直接替换生产主回复链路。
- [x] 不重新把 L1/L2/L3、画像、好感度和实时状态塞进稳定 system 前缀。
- [x] 不因缓存命中率低而删除 persona、Iris 或 ContextAware 的核心能力。
- [x] 不直接启用 `previous_response_id`，当前 transport 有实际拒绝记录。
- [x] 不把全部工具无条件并发。
- [x] 不把 `max_agent_step` 降到 1～2，正常工具协议需要选择和读取结果轮次。
- [x] 不把 DecisionAI 独立调用误判为主 Agent 重复请求。
- [x] 不只看请求数判断防抖，必须关联 message_id/request_id/route。

## 14. 里程碑

### M0：可回退基线

- [x] 当前 P1 commit/push/tag 完成。
- [x] 合成回放样本、功能矩阵和结构指标基线完成；真实用户/Provider 指标基线仍未完成。
- [ ] Azure 回滚演练完成。

### M1：协议与影子模式

- [x] contracts 完成。
- [x] Turn Coordinator shadow 完成（尚未接入真实回放 Hook）。
- [x] Context Assembler shadow 完成（尚未接入真实 ProviderRequest）。
- [ ] 影子模式 24 小时门禁通过。

### M2：Canary 接管

- [ ] 测试私聊/测试群使用新 Turn Coordinator。
- [ ] 图片、表情包、星图和主动聊天通过。
- [ ] 100 Turn canary 门禁通过。

### M3：Group Chat Plus 拆分

- [ ] 消息聚合和取消迁移完成。
- [x] Context/Proactive 公共本地实现完成；真实旧路径迁移仍未完成。
- [x] Web 面板已有稳定只读 service API；实际生产页面接线仍未完成。
- [ ] 旧重复模块可关闭。

### M4：钩子收敛和生产切换

- [ ] 主上下文只有一个 assembler owner。
- [x] 工具 effect/幂等本地策略完成；真实工具链仍未接入。
- [ ] 生产观察 24 小时通过。
- [ ] 旧路径删除前完成最后一次回滚演练。

## 15. Definition of Done

只有同时满足以下条件，本轮重构才算完成：

- [ ] 一次用户意图稳定对应一个 Turn 和一个最终 delivery owner；
- [ ] 防抖、取消、图片文字合并和主动聊天使用统一状态机；
- [ ] 上下文由结构化 section 组装，不依赖多个插件任意拼接顺序；
- [ ] 图片、表情包、星图和标注图都使用稳定 MediaRef；
- [ ] Group Chat Plus 已拆出核心业务模块，Web 面板仍可用；
- [ ] Iris、Codex Provider、Output Audit、Smart Segmentation 保持独立边界；
- [ ] 全部功能矩阵、回放、性能和安全门禁通过；
- [ ] Azure 部署、备份、更新和回滚继续可用；
- [ ] Public / Private / secrets 边界未被破坏；
- [ ] 文档与实际代码一致，不再依赖未记录的 hook 顺序和临时约定。
- [ ] 安全测试无法取得内部提示、人格原文、工具内部说明和其他用户记忆；
- [ ] Affection 使用明确 Provider 和明确 bot 数据目录，更新可追踪且幂等；
- [ ] SnowLuma 是唯一接入层并通过 72 小时与故障恢复测试；
- [ ] 新 Ubuntu 主机可以依靠仓库、备份和必要 secrets 完整恢复；
- [ ] 仓库内只保留本文件作为 TODO 主计划，完成状态持续在这里更新。
