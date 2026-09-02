# XiaoTianWen Cognitive Runtime — P1 Foundation Checkpoint

状态：**ACCEPTED**
检查日期：2026-09-02
检查范围：公共仓库中的 P0/P1 认知基础与 Cognitive Observatory

## 项目身份

`XiaoTianWen Cognitive Runtime` 是面向长期 Agent 的宿主无关认知运行时。
当前实现仍位于 `astrbot_plugin_iris_memory` 插件中，这是兼容部署位置，不是
对 Python 包名、插件 identifier、AstrBot 路由、配置 key 或 Iris 数据结构的迁移。

- XiaoTianWen Cognitive Runtime：整体认知项目身份；
- Iris Memory：记忆子系统与当前兼容宿主位置；
- AstrBot：当前宿主适配与运行环境；
- OpenJiuwen：planned / experimental，尚未接线。

## P1 已接受范围

P0/P1 foundation 已通过独立验收，包含：

- Entity Registry、Alias/UID Resolution、SELF binding 与 Perspective projection；
- Iris L1-L3 兼容的 pre/post adapter；
- Situation、Trigger、ExitReason 等最小生命周期合同；
- Participation / Intent、BehaviorExecutionRecord 与 bounded runtime observability；
- Episode 生命周期、immutable EpisodeEventRef 与 OutcomeObservation；
- ReviewRun / ReviewFinding 合同、实现、存储与历史 replay fixture；
- factual attachment 的 fail-closed 校验（包括 HOST_OUTPUT 的 execution identity、revision、stage 链接）；
- 只读 Cognitive Observatory 与 request-local Preview Review；
- ReviewEvidence 合同和完整 Store integrity surface。

核心观测链路为：

```text
Raw Event
  → Identity / Perspective
  → Canonical Experience
  → Situation / Trigger
  → Participation / Intent
  → Behavior Execution
  → Episode
  → Outcome
  → ReviewRun
  → ReviewFinding
  → Promotion Gate [CLOSED]
  × ReviewEvidence
```

## ReviewEvidence 边界

P1 中 ReviewRun 与 ReviewFinding 的解释管线是 active；ReviewEvidence promotion
是 **DISABLED / FAIL-CLOSED**。正常 Review 执行不会产生或写入 ReviewEvidence，
无论候选来自 deterministic engine、structured model engine 还是调用方提供的
engine。ReviewFinding 不会驱动 BehavioralPrior、Persona、Affect、Relationship、
Iris writeback、Learning、Consolidation 或未来 Host 行为。

这一边界是有意的：ToolResult 的冻结事实合同、逐陈述 promotion predicate 以及
未来可接受的 promotion authority 尚未冻结，在此之前不把“看起来安全”的解释
提升为历史证据。

## 不属于 P1 的能力

本 checkpoint 不声称实现以下能力：

- autonomous learning、长期行为适应、reward/score/quality 学习；
- BehavioralPrior、Consolidation 或 Structure Evolution；
- ReviewEvidence production；
- Persona、Affect、Relationship 或 Iris 数据写回/迁移；
- scheduler、live QQ、live model caller 或 OpenJiuwen adapter。

## 已知债务

- ReviewEvidence production disabled，等待未来明确冻结的 promotion contract；
- ToolResult frozen factual contract 尚未提供，因此相关事实保持 fail-closed；
- ExecutionRecord observability 是 bounded、runtime-local registry，不是持久化审计库；
- 生产 ReviewStore 尚未接入运行链路，Preview 使用 request-local InMemoryReviewStore；
- authenticated Dashboard/manual showcase 可能仍需人工执行；
- Windows Chroma `metadata.db` lock（WinError 32）与本 P1 foundation 无关；
- 真实宿主、真实 Provider/QQ 和生产并发尚未在本地门禁中被宣称验证。

## 验收证据

在本 checkpoint 前使用项目测试环境完成：

```text
tests/cognitive/test_review_implementation.py       52 passed
tests/cognitive/test_execution_observatory.py        6 passed
tests/web/test_cognitive_observatory.py              7 passed
tests/cognitive                                     182 passed
tests/web                                             71 passed
compileall -q iris_memory                              PASS
npm run build:check                                    PASS
git diff --check                                       PASS
```

测试结果只证明公共代码、合同、隔离运行时、Observatory 和 Preview 的本地行为；
不把未执行的真实 QQ、Provider、服务器或长时运行验收折算为通过。

## 版本与兼容性

P1 checkpoint 只冻结公共代码、测试、必要的前端构建资产与项目文档。现有
Iris L1-L3 数据、实例目录、配置 key、插件 identifier 和 AstrBot 路由保持原样；
本版本不是 production memory migration。
