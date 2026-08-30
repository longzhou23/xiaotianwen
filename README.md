# 小天文

> 基于 AstrBot 与 SnowLuma 的可迁移智能陪伴实例系统。

小天文不是绑定某一台 NUC、VM 或云服务器的一次性安装。项目把**公共代码**、**私有实例状态**、**主机密钥**和**可重建运行时**明确分开：在全新的 Ubuntu 24.04 主机上，只要提供公共仓库、私有实例仓库及必要凭据，就应能恢复一个完整、可维护的实例。

本仓库是小天文的 **Public Repository**，保存可公开维护的代码、插件、部署脚本、配置模板和文档。人格、记忆、知识库、QQ 登录状态以及真实凭据均不属于本仓库。

## 项目概览

| 项目 | 当前约定 |
|---|---|
| 运行形态 | 单机 Docker Compose；可运行于 Azure VM、NUC 或本地 Linux 主机 |
| 核心服务 | AstrBot（编排/模型/插件）+ SnowLuma（QQ/OneBot 接入） |
| 已弃用组件 | NapCat；其历史文件仅保留在归档中，不参与运行链路 |
| 镜像策略 | AstrBot、SnowLuma 默认使用 `latest`；更新时记录 image digest 并保留回退点 |
| 数据策略 | 公共代码与私有实例分仓；日志、缓存、临时媒体不作为唯一恢复依据 |
| 安全策略 | API Key、Token、私钥、账号状态均由主机 Secret 或私有备份管理，禁止进入 Git 历史 |

## 系统结构

```text
QQ 用户 / 群聊
        │
        │ QQ 协议、图片、表情包、合并转发
        ▼
┌──────────────────────────────────────────────┐
│ SnowLuma                                     │
│ QQ 接入、账号登录、OneBot 反向 WS、WebUI     │
└─────────────────────┬────────────────────────┘
                      │ Docker 内网
                      │ ws://astrbot:8001/ws
                      ▼
┌──────────────────────────────────────────────┐
│ AstrBot                                      │
│ 事件总线、会话编排、Provider、工具循环、面板 │
└───────┬─────────────────┬─────────────┬────────┘
        │                 │             │
        ▼                 ▼             ▼
  模型 / Embedding    插件与工具      私有持久化数据
  硅基流动、          Iris、图片、     persona、知识库、
  OpenAI 兼容服务等   天文、表情包     数据库、QQ 状态
```

一条消息的逻辑流程：

```text
SnowLuma 接收 QQ 消息
  → OneBot 事件进入 AstrBot
  → 防抖 / 群聊触发 / 场景与媒体识别
  → 图片摘要、Iris 记忆与必要上下文注入
  → Provider 生成与工具调用循环
  → 输出审核、工具残留清理、最终文本分段
  → SnowLuma 将文本、图片或文件发回 QQ
```

完整的处理顺序、端口边界、图片 ID 规范、数据策略和验收项见 [架构总览](docs/ARCHITECTURE.md)。

## 仓库与数据边界

```text
xiaotianwen ecosystem
│
├── Public Repository（本仓库）
│   ├── 通用源码、公开插件、组件、部署脚本
│   └── 配置模板、文档、测试与示例
│
├── Private Repository（单个实例）
│   ├── persona、知识库、记忆、数据库与用户状态
│   ├── 私有插件与实际插件配置
│   └── SnowLuma / AstrBot 的需持久化实例数据
│
└── Host Secret / Runtime（不进 Git）
    ├── API Key、Token、证书、SSH 私钥与面板密码
    ├── QQ 登录态、外部服务登录态
    ├── 容器挂载、缓存、日志、临时媒体
    └── 主机级备份和故障回退快照
```

### 禁止提交的内容

- `.env`、API Key、Token、Cookie、证书、私钥及 Cloudflare / GitHub / Codex 登录材料；
- QQ 登录目录、聊天记录、用户画像、好感度、Iris 数据库、向量索引；
- 私有 persona、私有知识库、临时媒体、日志、虚拟环境、模型缓存；
- 能够通过配置、解码或引用恢复出任何凭据的文件。

如果发生过凭据误提交，必须先撤销凭据，再清理 Git 历史；只删除当前文件并不能消除风险。

## 本仓库目录

```text
xiaotianwen/
├── plugins/
│   ├── upstream/       第三方原版插件
│   ├── modified/       二次开发或自研的公共插件
│   ├── retired/        已停用、但保留来源信息的插件
│   └── manifest/       来源、许可证、版本与本地修改清单
├── components/
│   └── snowluma-framework/
│                       SnowLuma 镜像构建所需的公共组件
├── deploy/
│   ├── astrbot/        AstrBot Compose 模板与兼容脚本
│   ├── snowluma-live/  SnowLuma Compose 模板与示例环境文件
│   └── *.sh            部署、更新、备份、恢复、验证与自动同步脚本
├── scripts/            日常启停、日志、状态、诊断入口
├── config/             不含实例值的配置与版本模板
├── examples/           示例文件与参考配置
├── docs/               架构、部署、迁移、演练、版本和设计文档
├── LICENSE
└── README.md           本文件
```

每个插件应通过 `plugin.meta.yaml` 或 `plugins/manifest/` 标记来源、许可证、版本、修改状态和维护者。上游插件在公开发布前必须完成许可证和本地差异审查。

## 核心组件边界

| 层级 | 组件 | 主要职责 | 不应重复负责 |
|---|---|---|---|
| QQ 接入 | SnowLuma | QQ 登录、协议、OneBot、WebUI / noVNC | LLM 编排、人格、长期记忆 |
| 编排核心 | AstrBot | 事件、会话、Provider、工具循环、Dashboard | QQ 协议实现 |
| 当前场景 | ContextAware、Group Chat Plus | 场景、群聊关系、触发、短期上下文、媒体识别和聚合 | L2/L3 长期记忆 |
| 记忆人格 | Iris Memory | L1 记录、L2/L3、画像、关系、好感度、人格学习、主动聊天 | 普通图片重复解析 |
| 图片上下文 | ImageContextPool | 图片 ID、摘要、原图/标注图关联、多图引用 | 独立创建主回复 |
| 天文能力 | Astrmetry | 星空解析、目标识别、标注图与下载资源 | 绕过 persona 直接输出工具结果 |
| 表情包 | Stealer | 收集、分类、检索和发送表情包 | 普通图片或星空图片主上下文注入 |
| 输出控制 | Output Audit、Tool Use Cleaner、Smart Segmentation | 审核、清理工具残留、最终文本分段 | 再次调用主 LLM |

### 消息管线不变量

1. 一次用户意图只应创建一条主回复链路；防抖、取消回复、图片事件和插件 Hook 不能各自产生独立主回复。
2. 普通图片只保留一条主解析路径。已有摘要时复用，不能再次调用 VLM。
3. 每张图片分配稳定 `image_id`，摘要、原图和天文标注图均关联到该 ID；后续“这张 / 第一张 / 标注图”应能可靠定位。
4. Iris L1 用于记录和后台整理，不能与 AstrBot 近期对话历史重复完整注入主回复。
5. 工具结果先以内部资料进入上下文；最终内容仍须经 persona、输出审核和分段处理。
6. 主回复、预判断、后台记忆任务和工具调用必须有明确会话键、幂等键和失败回退。

## 新机快速部署

目标环境：**Ubuntu 24.04 x64**。部署前请准备：

- 本仓库的访问权限；
- 私有实例仓库或经加密的实例备份；
- GitHub 访问权限；
- 模型、Embedding、插件所需密钥；
- QQ 登录和人工验证所需的管理访问方式。

在公共仓库根目录中创建仅属于这台主机的 Secret 文件，再运行首次部署：

```bash
export PROJECT_ROOT="$HOME/xiaotianwen"
export PUBLIC_REPO_URL="https://github.com/<owner>/xiaotianwen.git"
export PRIVATE_REPO_URL="https://github.com/<owner>/xiaotianwen-instance.git"
export SECRET_FILE="$PROJECT_ROOT/.host-secrets/secrets.env"

install -d -m 700 "$(dirname "$SECRET_FILE")"
install -m 600 deploy/secrets.env.example "$SECRET_FILE"
# 用编辑器填入真实模型 / 插件密钥；不要写进 shell 历史或 Git。

bash deploy/bootstrap.sh
```

脚本负责依赖检查、仓库准备、实例恢复、插件同步、Compose 启动及基础验证；它不会自动猜测 API Key、QQ 数据、Cloudflare 凭据或 Codex 登录态。

首次恢复的完整说明见 [部署脚本说明](deploy/README.md) 和 [部署文档](docs/DEPLOYMENT.md)。

## 日常运维

在 Linux 主机的公共仓库根目录执行：

```bash
# 状态与运行环境检查
./scripts/status
./scripts/doctor

# 服务控制：all、astrbot 或 snowluma
./scripts/start all
./scripts/stop snowluma
./scripts/restart astrbot

# 日志
./scripts/logs all --tail 100
./scripts/logs snowluma --follow

# 备份、更新与验证
./scripts/backup
bash deploy/update.sh
bash deploy/verify.sh
```

普通重启不会主动拉取新镜像。`deploy/update.sh` 才会拉取 `latest`、记录 image digest、创建运行时快照，并在验证失败时尝试回退。参数细节见 [脚本说明](scripts/README.md)。

### 默认端口与安全边界

| 服务 | 默认端口 | 用途 | 访问要求 |
|---|---:|---|---|
| AstrBot Dashboard | `6200` | AstrBot 管理面板 | 公网必须叠加 Access、VPN 或强认证 |
| AstrBot OneBot WS | `8001` | SnowLuma → AstrBot 反向 WebSocket | 仅 Docker 内网，禁止公开暴露 |
| SnowLuma WebUI | `5099` | QQ 接入与账户管理 | 独立认证并限制来源 |
| SnowLuma noVNC | `6081` | 图形化 QQ / 桌面维护 | 不以 VNC 密码作为唯一安全边界 |
| SSH / SFTP | `22` 或自定义 | 主机维护 | 密钥认证、最小权限、优先 VPN / Tunnel |

真实域名、绑定地址、端口和访问策略属于宿主机 / 私有实例配置，不写入公共代码。

## 备份、迁移与恢复

必须备份的私有资产：

- persona、系统设定、私有行为规则；
- 知识库原文、切片、embedding 与索引；
- Iris L1/L2/L3、用户画像、关系、好感度、任务数据；
- AstrBot 插件持久化数据、必要配置与数据库；
- SnowLuma / QQ 账号与持久化数据；
- 恢复清单、版本信息与镜像摘要。

通常可重建或按短周期清理的内容：日志、Python 缓存、容器层、模型缓存、缩略图、临时图片和短期响应缓存。清理媒体前，应先确认关联的图片元数据、原图与仍需引用的标注图已被保存。

恢复顺序：

```text
公共代码 → 私有实例 → 主机 Secret → AstrBot → SnowLuma → OneBot → Provider
→ 文本 / 图片 / 表情包 / 星空图 / 记忆 / 知识库验收
```

参见 [迁移说明](docs/MIGRATION.md)、[自动化部署需求](docs/Xiaotianwen_Deployment_Automation_Requirement.md) 和 [恢复演练记录](docs/DRILL-20260826.md)。

## 排障顺序

当出现“没有回复”“重复回复”“图片看不到”等问题时，按链路逐段检查，而不要只观察最终表现：

```text
SnowLuma / QQ 入口
  → OneBot WebSocket
  → AstrBot 事件与插件 Hook
  → Provider / Embedding
  → 工具调用与持久化数据
  → 输出审核与分段
  → SnowLuma 发送
```

| 现象 | 首先检查 |
|---|---|
| SnowLuma 持续 WS 403 | 共享网络、`ws://astrbot:8001/ws` 和两端 token；不要误用宿主机回环地址 |
| 同一消息多份回复 | 防抖、取消回复、群聊和媒体插件是否分别创建 Provider 请求；关联请求 ID / 会话键 |
| 图片未理解或重复理解 | 是否生成 `image_id`、是否复用已有摘要、是否开启了多个图片解析器 |
| 表情包上下文丢失 | 表情包插件是否只负责检索/发送，图片摘要是否来自唯一上下文路径 |
| 天文标注图发送失败 | 临时文件是否被清理、标注链接是否可达、私聊是否错误包含 `@` 元素 |
| `models.dev` 超时 | 将其视为外部元数据服务故障，单独检查 DNS、代理和超时，不等同于主模型不可用 |
| Iris 人格自迭代提示路由不存在 | 检查 Plugin Page 路由格式是否兼容 AstrBot，刷新前端并核对重启后的路由注册日志 |

## 文档导航

| 文档 | 内容 |
|---|---|
| [统一 TODO](Todo.md) | 编排层重构、稳定性、安全、SnowLuma、迁移、Agent Loop、缓存与性能的唯一主计划 |
| [架构总览](docs/ARCHITECTURE.md) | 组件边界、完整消息管线、数据策略、安全约束与验收 |
| [部署说明](docs/DEPLOYMENT.md) | Docker 拓扑、端口、管理入口、日志与当前部署约定 |
| [部署脚本说明](deploy/README.md) | bootstrap、更新、回退、备份、恢复与自动同步 |
| [迁移说明](docs/MIGRATION.md) | 公共代码、私有实例与新机器恢复流程 |
| [版本策略](docs/VERSION_POLICY.md) | latest 策略、镜像摘要、插件版本与回退规则 |
| [恢复演练记录](docs/DRILL-20260826.md) | 新机恢复验证与待补全事项 |
| [运维网关设计](docs/UNIFIED_OPERATIONS_GATEWAY_DESIGN.md) | 统一管理入口设计；不属于消息链路运行依赖 |
| [部署自动化需求](docs/Xiaotianwen_Deployment_Automation_Requirement.md) | 可复现部署目标、验收标准与后续路线图 |
| [变更记录](docs/CHANGELOG.md) | 面向版本的变更历史 |

## 变更与贡献约定

- 公共代码使用 `feat:`、`fix:`、`refactor:`、`docs:` 等清晰的提交前缀。
- 人格、知识库、数据库和实例配置只进入 private repository，建议使用 `instance:`、`data:`、`config:` 前缀。
- 修改消息、上下文、Provider 或图片链路时，必须说明它是否会产生新的 LLM 请求，以及会话键、幂等键和失败回退。
- 部署前先备份；完成测试后重启并读取新日志验证，不用旧日志推断新版本已生效。
- 不得新增第二个普通图片解析器、第二个主回复分段器，或职责不明的全局上下文注入器。

## 新机验收清单

- [ ] AstrBot 与 SnowLuma 容器正常运行，且共享网络存在。
- [ ] SnowLuma 成功连接 `ws://astrbot:8001/ws`，无持续 403 / 重连。
- [ ] Dashboard、WebUI、noVNC 按计划暴露并受认证保护。
- [ ] 文本、普通图片、表情包、星空图片、合并转发分别完成端到端测试。
- [ ] Iris L1、L2/L3、知识库检索及人格自迭代接口均可用。
- [ ] 图片 ID、首次摘要、多图关联、原图和天文标注图可在后续消息中引用。
- [ ] 单次用户意图仅产生预期的一条主回复链路。
- [ ] 输出审核和文本分段各只执行一次，工具内部资料不泄露。
- [ ] 备份、恢复、回退、健康检查脚本在目标主机实测通过。
- [ ] 已记录实际镜像摘要、配置来源、恢复耗时和遗留问题。

## 许可证与隐私

本仓库自身适用 [LICENSE](LICENSE)。仓库内第三方插件、组件和依赖仍各自受其上游许可证约束；分发或公开修改前，请依据插件元数据和 `plugins/manifest/` 完成审查。

小天文会处理 QQ 消息、图片、表情包、记忆和用户状态。部署者应遵守适用法律、平台规则、群聊约定和数据保留政策，并为访问控制、日志、备份和删除请求建立明确流程。
