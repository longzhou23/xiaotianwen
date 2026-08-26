# 小天文项目架构文档

> 文档版本：v1.0（2026-08-26）
> 适用范围：`xiaotianwen` 公共代码仓库、私有实例数据、Ubuntu/Docker 运行环境以及 QQ-AstrBot 处理链路
> 文档性质：当前实现的架构基线，同时记录迁移和演进约束

## 1. 文档目的

小天文不是某一台 NUC 或某一台云服务器上的临时安装，而是由代码、实例数据、运行时和外部服务共同组成的可迁移实例。本文件回答以下问题：

1. 一条 QQ 消息从哪里进入，如何经过 SnowLuma、AstrBot 和插件，最终返回 QQ；
2. 哪些文件属于公共代码，哪些属于私有实例、运行缓存或密钥；
3. 记忆、图片、表情包、星空分析、工具调用和输出审核分别由谁负责；
4. 新机器如何恢复、如何更新、如何检查健康状态以及如何回滚；
5. 当前架构有哪些已知限制，后续修改应遵守哪些边界。

本文件是架构总览，不替代具体操作手册。部署细节见 [`DEPLOYMENT.md`](DEPLOYMENT.md)，迁移细节见 [`MIGRATION.md`](MIGRATION.md)，版本策略见 [`VERSION_POLICY.md`](VERSION_POLICY.md)，统一运维面板设计见 [`UNIFIED_OPERATIONS_GATEWAY_DESIGN.md`](UNIFIED_OPERATIONS_GATEWAY_DESIGN.md)。

## 2. 设计目标与非目标

### 2.1 目标

- **可复现**：干净的 Ubuntu 24.04 主机能够从公共仓库、私有实例仓库和主机密钥恢复一个可运行实例。
- **可迁移**：QQ 数据、人格、知识库、记忆和配置不绑定某个容器层或某台机器；机器损坏时可以更换宿主机。
- **可维护**：服务、插件、数据和凭证有清晰边界，能够单独备份、升级、诊断和回滚。
- **可审计**：公共仓库不包含真实凭证、QQ 登录数据、聊天隐私或实例知识；插件来源和修改状态以清单为准。
- **低耦合**：QQ 接入层、LLM 编排层、记忆层、工具层和输出层通过 AstrBot 事件/Provider 接口连接，避免插件相互重复处理同一数据。
- **隐私优先**：日志默认记录长度、数量、哈希和来源，不记录完整 persona、原始记忆或密钥；调试原文必须临时开启并在问题结束后关闭。

### 2.2 非目标

当前版本不承诺 Kubernetes、多节点高可用、自动扩缩容或跨区域一致性。运行形态是单实例 Docker Compose；“latest”更新策略提高了恢复速度，但不能代替正式版本和镜像摘要验证。

## 3. 系统上下文

```text
┌──────────────┐
│ QQ 用户/群聊 │
└──────┬───────┘
       │ QQ 协议、图片、表情包、合并转发
       v
┌──────────────────────────────────────────────┐
│ SnowLuma                                     │
│ QQ 客户端/协议、登录状态、WebUI、noVNC        │
│ OneBot 反向 WebSocket 客户端                  │
└──────┬───────────────────────────────────────┘
       │ Docker 内网：ws://astrbot:8001/ws
       v
┌──────────────────────────────────────────────┐
│ AstrBot                                      │
│ OneBot 适配、事件总线、Provider、工具循环      │
│ 对话编排、插件生命周期、响应管线、管理面板      │
└──────┬─────────────┬────────────┬────────────┘
       │             │            │
       v             v            v
  LLM/Embedding   外部工具      持久化数据
  硅基流动、      Astrometry、   AstrBot data、
  OpenAI/Codex    表情包服务等   QQ/插件数据
```

### 3.1 访问平面

管理访问与消息链路分开：

- AstrBot Dashboard：默认容器端口 `6200`；仅在确有需要时对外绑定，公网访问应置于 Cloudflare Access、VPN 或其他认证层之后。
- AstrBot OneBot：默认容器端口 `8001`，供 SnowLuma 反向 WebSocket 使用，不应直接暴露公网。
- SnowLuma WebUI：`5099`，用于登录、协议状态和配置管理。
- SnowLuma noVNC：`6081`，用于图形化 QQ/桌面维护；必须使用独立认证、Access 或 VPN 保护，不能把 VNC 密码当作唯一安全边界。
- SSH/SFTP：宿主机维护通道；优先使用密钥和 Cloudflare Tunnel/VPN，禁止在仓库保存私钥。

实际域名、Cloudflare Tunnel、Access 策略和端口映射属于实例/主机配置，不写入公共代码。

## 4. 代码、实例与运行时分层

### 4.1 公共仓库

公共仓库根目录为 `bot/public/xiaotianwen`，内容可公开维护：

```text
xiaotianwen/
├── plugins/
│   ├── upstream/       第三方原版插件
│   ├── modified/       二次开发或本地自研插件
│   ├── retired/        已停用但保留来源的插件
│   └── manifest/       来源、许可证、修改状态清单
├── components/         可公开构建的通用组件（SnowLuma framework）
├── deploy/             Docker Compose、安装、更新、备份、恢复脚本
├── scripts/             主机侧启停、诊断、日志和 AstrBot 辅助命令
├── config/              不含实例值的配置/版本模板
├── docs/                架构、部署、迁移、演练和版本文档
├── LICENSE
└── README.md
```

公共仓库禁止出现：`.env`、API Key、Token、证书、私钥、QQ 登录目录、聊天记录、用户画像、好感度数据、Iris 数据库、知识库向量索引、`.runtime`、虚拟环境、日志和临时媒体。

### 4.2 私有实例仓库

私有仓库存放“某一个小天文实例”的状态，而不是通用程序：

```text
instance/
├── persona/             人格、系统设定、行为边界
├── knowledge/           私有知识库原文/切片/索引
├── database/            Iris、用户状态、好感度和任务数据
├── memory/              L1/L2/L3 记忆与摘要
├── user_state/          用户画像及群聊关系数据
├── config/              实例配置、插件配置、Provider 选择
└── plugins-private/     不适合公开的插件或本地补丁
```

私有仓库也不应保存未加密的主机私钥；可保存部署所需的非敏感模板，真实密钥由主机 Secret 文件或外部密钥管理系统注入。

### 4.3 主机 Secret、运行时和备份

```text
主机 Secret（不进 Git）
├── API keys / Provider 凭据
├── OneBot token
├── Cloudflare、GitHub、Codex 等登录凭据
└── SSH 私钥、VNC/面板密码

运行时（可重建，按策略备份）
├── runtime/astrobot/data       AstrBot 数据、插件配置、插件 KV
├── runtime/snowluma/data       SnowLuma 数据
├── runtime/snowluma/qq-config  QQ/NapCat 兼容配置目录（当前由 SnowLuma 使用）
├── runtime/snowluma/qq-data    QQ 登录/协议数据
├── AstrBot data/codex/         Codex CLI 与 CODEX_HOME
└── logs、cache、临时媒体      默认可清理，不作为恢复依据

备份
├── 数据备份（数据库、知识库、persona、QQ 数据）
├── 运行时快照（用于故障回退）
└── 日志归档（按保留期单独管理）
```

## 5. 部署拓扑

当前生产形态由两个 Compose 项目组成。AstrBot 先创建默认网络 `xiaotianwen-astrbot_default`；SnowLuma 同时加入自己的默认网络和该外部网络，从而用稳定的服务名访问 AstrBot。

```text
Docker host
├── Compose: astrbot
│   └── container: astrbot
│       ├── 6200 -> Dashboard
│       ├── 8001 -> OneBot reverse WS
│       └── /AstrBot/data <- runtime/astrobot/data
│
├── Compose: snowluma-live
│   └── container: snowluma
│       ├── 5099 -> SnowLuma WebUI
│       ├── 6081 -> noVNC
│       ├── /app/data <- runtime/snowluma/data
│       ├── /app/.config/QQ <- runtime/snowluma/qq-config
│       └── /app/.local/share <- runtime/snowluma/qq-data
│
└── Network: xiaotianwen-astrbot_default
    └── snowluma -> ws://astrbot:8001/ws
```

AstrBot Compose 使用 `soulter/astrbot:latest`，SnowLuma Compose 使用 `motricseven7/snowluma:latest` 作为默认镜像；实际版本和镜像摘要由 `config/versions.lock`、`deploy/versions.env.example` 以及实例环境文件决定。更新 latest 前必须先备份、拉取、检查镜像、启动并执行健康验证。

在已经完成 Codex 安装的运行实例中，Codex CLI 位于 AstrBot 持久化数据挂载中：

```text
/AstrBot/data/codex/bin   可执行文件
/AstrBot/data/codex/home  CODEX_HOME、登录和配置
```

这样容器重建不会丢失 CLI 文件；凭据仍不进入镜像和 Git。当前 `bootstrap.sh`/`update.sh` 在 AstrBot 容器启动后调用 `deploy/install-codex.sh`：默认使用容器内的 Node/npm 将 `@openai/codex@latest` 安装或校验到该持久化目录，并执行版本检查。若实例恢复包已经包含 `data/codex`，脚本会先复用并校验现有安装；若镜像没有 Node/npm，默认部署会失败并提示修复，而不是生成一个表面成功但 Codex 不可用的实例。Codex 登录仍由运维者单独完成，凭据不能进入公共仓库或镜像。

## 6. AstrBot 消息处理管线

以下是逻辑顺序。具体执行顺序由 AstrBot 事件类型、插件优先级和当前配置共同决定，不能仅凭日志中出现顺序推断所有调用细节。

```text
1. QQ 消息到达 SnowLuma
2. SnowLuma 转为 OneBot 事件，经反向 WS 发送到 AstrBot
3. AstrBot 适配器建立会话与 ProviderRequest
4. 记录/缓存阶段
   - Iris L1 记录原始消息
   - ContextAware 识别群聊场景、发言关系和媒体
   - ImageContextPool 保存图片 ID、摘要和可引用链接
   - Stealer 按配置收集表情包（不负责主回复图片理解）
5. 聚合与触发阶段
   - Debounce/智能分段合并短时间消息
   - Group Chat Plus 进行群聊触发、概率、上下文窗口和工具提醒
   - Recall Cancel/取消回复逻辑处理撤回或新消息
6. 请求上下文阶段
   - ContextAware 注入当前场景和必要图片描述
   - ImageContextPool 注入图片 ID/描述/原图或标注图引用
   - Iris Memory 查询 L2/L3、画像、好感度和人格学习结果
   - Shared Context 按配置提供跨会话上下文；当前忽略文件组件和多模态主动解析
   - AntiPromptInjector 做输入边界与提示注入防护
7. Provider/LLM 阶段
   - 主文本/视觉请求发送给实例配置的硅基流动或其他 Provider
   - Iris 的 L2 改写、摘要、抽取等使用指定实例 Provider
   - chatgpt_codex/Codex CLI 仅在启用相应 Provider/工具时参与
8. 工具循环阶段
   - Astrmetry 处理星空/天文图片分析、标注图和下载链接
   - 表情包检索/发送、QQ 操作和其他工具按权限执行
9. 出站审核阶段
   - Output Audit 对待发送内容执行 allow/revise/block
   - Tool Use Cleaner 清理残留工具协议
   - Smart Segmentation 将最终文本按平台限制和配置分段
10. SnowLuma 发送消息、图片或文件回 QQ
```

### 6.1 防重复请求原则

一次用户意图应只产生一个主回复请求。防抖负责聚合短时间到达的消息；取消回复负责在新消息到达时终止旧请求；图片/文字事件不能各自独立触发一份主回复。任何新增插件若会调用 LLM，必须明确它是“主回复”“预判断”“记忆后台任务”还是“工具调用”，并设置独立会话键和幂等标识。

## 7. 插件职责边界

### 7.1 核心编排与场景

| 组件 | 责任 | 明确不负责 |
|---|---|---|
| AstrBot | 事件总线、Provider、工具循环、管理面板、响应管线 | QQ 协议细节和实例人格内容 |
| ContextAware（modified） | 当前群聊场景、发言关系、触发判断、图片媒体识别、按需 VLM 描述 | L2/L3 长期记忆和主动聊天人格学习 |
| Group Chat Plus（modified） | 群聊触发概率、消息聚合、40 条主上下文限制、工具提醒、私聊跨群意图转发 | 作为唯一图片理解器；其内置图片处理已关闭 |
| Shared Context | 跨会话共享上下文 | 当前不主动做文件/多模态解析，不替代 Iris |
| AtTool | QQ `@` 相关的触发/辅助操作 | 不负责会话聚合、模型调用或权限绕过 |

### 7.2 记忆与人格

| 组件 | 责任 |
|---|---|
| Iris Memory | L2 改写/摘要/抽取、L3 画像与关系、好感度、人格学习、主动聊天；主回复需要时注入动态记忆 |
| Iris L1 | 记录消息供后台整理；不把完整近期消息重复注入主回复 |
| Affection | 维护约定的四类情感/好感度数值及变化规则；数值来自实例记忆与默认值，不从公开仓库读取真实用户状态 |

Iris 的文本、embedding、视觉 Provider 以实例配置为准；公共架构只约定“硅基流动或兼容 OpenAI API 的 Provider”，不写入真实模型名、API Key 或账户信息。

### 7.3 媒体、天文与表情包

| 组件 | 责任 |
|---|---|
| ImageContextPool（modified） | 为每张图片生成稳定 ID，持久化摘要、路径、链接和多图映射；让后续消息按 ID 引用首次 VLM 结果 |
| Astrmetry | 星空解析、目标识别、标注图生成/下载；结果必须作为内部资料进入上下文，不能未经 persona 组织直接输出 |
| Stealer | 偷取、分类、索引和发送表情包；保留表情包库，但不承担普通图片/星空图片的主上下文注入 |
| ContextAware image path | 普通图片按需懒加载 VLM；若无摘要才发起一次解析 |
| Group Chat Plus image path | 负责识别消息类型和引用已有摘要；配置上不启用其独立图片处理，避免多插件重复注入 |

图片上下文的规范表示为：`[图片: img-<id>，摘要：...，原图/标注图：<url-or-local-reference>]`。一条消息包含多张图片时，每张图片单独建立 ID，并保留顺序、发送者和消息关联；后续“这张/第一张/标注图”通过 ID 或消息关联解析，禁止只依赖模糊的自然语言占位符。

### 7.4 输出、分段与安全

| 组件 | 责任 |
|---|---|
| Smart Segmentation | 最终文本分段、换行和平台长度适配；不得再次调用主 LLM |
| Output Audit | 出站前审核、改写或阻断不当内容；默认不记录完整 prompt/persona |
| AntiPromptInjector | 识别用户输入中的提示注入、边界突破和伪系统指令 |
| Tool Use Cleaner | 清理工具协议残留，避免工具 JSON 或内部提示泄露 |
| Debounce | 合并短时间输入；不能与 Group Chat Plus 的聚合逻辑形成两个独立主请求 |
| Recall Cancel | 撤回/新消息触发取消待发送回复 |

## 8. 数据流与存储策略

### 8.1 消息与记忆

```text
OneBot event
   ├─> L1 append-only 记录（原始事件，私有）
   ├─> 场景/触发判断（短期、可丢失）
   ├─> 主回复上下文（按 40 条窗口和 token 预算裁剪）
   └─> 后台 L2/L3 作业
           ├─ L2 查询改写/摘要/抽取
           └─ L3 画像/好感度/人格学习
```

L1/L2/L3 的数据库、向量索引和用户状态属于私有实例数据。缓存命中率只能由实际 Provider 请求日志和缓存统计确认，不能用启动日志推断；缓存 key 应包含模型、系统提示版本、工具清单版本和上下文策略版本，避免“看似命中但语义不一致”。

### 8.2 图片

```text
QQ 图片
  -> SnowLuma 下载/转发
  -> ContextAware 识别媒体类型
  -> ImageContextPool 分配 image_id 并持久化元数据
  -> 已有摘要则复用；没有摘要且需要理解时调用 VLM
  -> 主回复只注入 ID + 摘要 + 可用链接
  -> Astrmetry 需要时生成 annotated image，并将结果关联到同一图片记录
```

二进制媒体缓存是可重建数据，不能替代原图/标注图的长期归档。远程 URL 可能过期，需在需要长期引用时下载到私有存储；缓存清理必须先备份元数据和仍需使用的标注图。

### 8.3 知识库

知识库原文、切片、embedding 和索引属于私有实例。导入流程应记录来源、切片版本、embedding Provider 和更新时间；更换 embedding 模型后必须重建索引，不能混用不同维度或不同模型的向量。

## 9. 配置与版本

配置按三层管理：

1. `config/*.example`：公共模板，只写键名、默认值和说明；
2. 私有实例 `config/`：实际插件开关、Provider、人格和知识库设置；
3. 主机 Secret 环境：API Key、Token、登录凭据和外部服务地址中的敏感部分。

版本策略：

- AstrBot、SnowLuma 默认跟随 latest 以便快速恢复，但每次更新都应记录镜像摘要并保留上一版本回退点；
- 自研/修改插件以 Git 提交和 `plugins/manifest/plugins.yaml` 为基线；
- 上游插件的许可证、来源和本地差异在发布前审查；
- 运行时插件配置不应依赖容器层临时文件；必须写入持久挂载。

## 10. 部署、更新、备份与恢复

主机侧脚本位于 `deploy/` 和 `scripts/`。在 Linux 工作区中，常用入口为：

```bash
cd "$PROJECT_ROOT/public"
./scripts/status
./scripts/doctor
./scripts/start
./scripts/stop
./scripts/restart all
./scripts/logs all --tail 100
./deploy/preflight.sh
./deploy/bootstrap.sh
./deploy/update.sh
./deploy/backup.sh
./deploy/restore.sh
./deploy/verify.sh
```

`README.md` 中历史上的 `bin/*` 命令名属于早期兼容文档；以当前 checkout 中实际存在的 `scripts/*` 和 `deploy/*` 为准，若重新提供 `bin` 兼容层，必须保持参数语义一致。

### 10.1 新机恢复顺序

```text
预检主机与磁盘
  -> 安装 Docker/Compose、Git、必要系统包
  -> clone 公共仓库
  -> clone 私有实例仓库或准备加密备份
  -> 创建目录与宿主用户权限
  -> 注入主机 Secret
  -> 启动 AstrBot Compose，创建共享网络
  -> 启动 SnowLuma Compose
  -> 恢复插件、人格、知识库、数据库、QQ 数据
  -> 检查 OneBot WS、Dashboard、WebUI、LLM Provider
  -> 发送最小测试消息并核对单次请求
  -> 记录版本、镜像摘要和恢复结果
```

### 10.2 备份边界

必须备份：私有数据库、人格、知识库原文和索引、L1/L2/L3、用户状态、QQ 登录数据、实例配置以及 Codex/AstrBot 所需的持久化配置。可重建或按短保留期处理：日志、Python 缓存、LLM 响应缓存、临时图片和容器层。删除或清理前先生成带时间戳的压缩备份，并记录校验和。

### 10.3 每日云端同步

`deploy/autosync.sh` 是可选的服务器端夜间任务。它在获得部署锁后，默认短暂停止 AstrBot 和 SnowLuma，
把允许保存的运行时数据同步回 private 仓库，再分别提交并 push public/private 两个仓库；完成后自动启动服务。
定时器由 `deploy/install-autosync-cron.sh HH:MM` 显式安装，默认示例为主机上海时区的 `03:30`，公共仓库
不会在 clone 或 bootstrap 时偷偷创建定时任务。

自动同步的不变量：

- private 工作区有人工未提交修改时直接退出，不覆盖人工编辑；
- 默认不复制运行时 `config/`、`cmd_config.json`、`mcp_server.json`、`skills.json`、`qq-config`、`qq-data`、
  host secrets、`.env`、私钥、日志和缓存；这些文件可能含有已渲染的 API Key/Token；
- public 只 stage 代码、组件、部署脚本、配置模板和文档；
- 提交前扫描常见 API Key、GitHub Token 和私钥特征，命中即禁止 push；
- 服务器必须预先配置 Git SSH key 或 credential helper，任务不负责登录 GitHub；
- `AUTOSYNC_QUIESCE=0` 可避免停机，但得到的是在线快照，数据库一致性由运维者承担；
- `AUTOSYNC_DRY_RUN=1` 只执行检查和 rsync 预览，不停止服务、不写入仓库、不 push。

## 11. 安全与隐私不变量

- 公共 Git 历史也必须扫描，不能只检查当前工作树；发现泄露时撤销凭证并使用历史重写。
- OneBot `8001` 只允许 Docker 内网访问；Dashboard/WebUI/noVNC 对公网开放时必须叠加 Access/VPN/强认证。
- 日志、诊断和架构文档不得输出 token、cookie、AUTH_CODE、私钥、完整系统 prompt、persona 或用户画像。
- 插件工具调用按最小权限配置；停止/重启、发消息、修改状态、写精华等管理动作应设置明确的授权来源和审计记录。
- 图片原图、标注图、表情包和用户上传文件属于用户数据，备份、清理和对外链接都要遵守实例保留策略。

## 12. 运维与可观测性

健康检查至少覆盖：

1. Docker daemon、容器运行状态和重启次数；
2. AstrBot `6200`、OneBot `8001`、SnowLuma `5099/6081` 的监听与访问策略；
3. SnowLuma 到 `ws://astrbot:8001/ws` 的握手；
4. Provider 连通性、模型返回是否有可用文本/工具调用；
5. Iris 数据库/embedding 索引读写；
6. 图片缓存目录、持久化 KV 和标注图链接；
7. 磁盘、内存、日志轮转和备份空间。

出现问题时按“入口 → WS → AstrBot 事件 → Provider → 工具 → 出站”分段定位。典型现象：

| 现象 | 首要检查 |
|---|---|
| 403 WebSocket | URL、token、网络是否误用 `host.docker.internal`；优先使用共享网络服务名 `astrbot` |
| 图片未触发主回复 | Group Chat Plus 的概率/触发策略是否将纯图片缓存而未放行；与 VLM 缓存问题分开判断 |
| 图片重复分析 | ContextAware、Iris 图片解析和 Group Chat Plus 内置图片处理是否同时开启；检查 image_id 幂等键 |
| 发出两份回复 | Debounce、取消回复、插件 Hook 是否各自产生 Provider 请求；按 request/session/idempotency 关联日志 |
| `faiss.swigfaiss_avx2` 缺失 | 先确认通用 faiss 是否成功回退；若能正常加载通常不是阻断故障 |
| `models.dev` 超时 | 视为外部元数据服务故障，不能直接等同于主 Provider 不可用；检查代理、DNS 和超时策略 |
| 标注图发送失败 | 检查本地临时文件是否已被清理、远程链接是否可达、私聊是否错误加入 `At` 元素 |

## 13. 当前已知限制与演进事项

1. 尚需在真正全新的 Ubuntu 主机上完成一次从零恢复演练，并保存耗时、命令、镜像摘要和验证结果。
2. latest 镜像便于更新但带来上游破坏性变更风险；长期运行应在恢复成功后记录摘要，必要时切换到摘要锁定。
3. Group Chat Plus 的群聊概率策略可能缓存少量纯图片消息；若产品要求“任何星空图都主动看图”，应增加明确的天文图片旁路规则，而不是重新打开多个图片解析器。
4. ImageContextPool 持久化的是图片元数据/摘要/引用；二进制缓存仍需独立保留策略，不能假设永久可用。
5. 上游插件的版本、许可证和依赖仍需定期审查；安装失败、ONNX/FAISS 可选依赖缺失应在 `doctor` 中分级为阻断或警告。
6. 私聊声明后在群聊执行属于跨会话授权能力，应继续使用独立会话键、来源校验和过期时间，不能使用全局共享上下文绕过隔离。
7. 统一公网面板不是核心运行依赖；各 WebUI 可独立暴露和保护，面板故障不应影响 QQ 消息链路。

## 14. 变更约束与验收清单

任何涉及消息、上下文或 Provider 的修改，至少满足：

- 说明它位于上述哪一层以及是否会产生 LLM 请求；
- 明确输入、输出、会话键、幂等键和失败回退；
- 不增加第二个普通图片解析器或第二个主回复分段器；
- 在本地运行单元测试/静态检查；
- 备份实例配置后再部署到服务器；
- 重启后读取新日志，验证一次消息只产生预期数量的请求；
- 对隐私字段做脱敏检查；
- 更新插件清单、版本记录和必要的迁移说明。

新机恢复验收：

- [ ] AstrBot 和 SnowLuma 容器健康、网络互通；
- [ ] OneBot 反向 WS 建立且无持续 403/重连；
- [ ] Dashboard/WebUI/noVNC 按预期认证和暴露；
- [ ] 文本、普通图片、表情包、星空图片、合并转发各完成一次测试；
- [ ] Iris L1 记录、L2/L3 查询和知识库检索可用；
- [ ] 图片 ID、摘要、多图映射和标注图链接可以在后续消息中引用；
- [ ] 主回复符合 persona，工具内部资料未直接泄露；
- [ ] 输出审核和分段只执行一次；
- [ ] 备份、恢复、回滚脚本在目标主机可执行；
- [ ] 版本、镜像摘要、配置来源和遗留问题已记录。

## 15. 术语表

- **L1**：消息原始记录层，主要供后续整理使用。
- **L2**：对话改写、摘要和结构化抽取层。
- **L3**：长期画像、关系、好感度和人格学习层。
- **Provider**：AstrBot 使用的模型后端，可为硅基流动、OpenAI 兼容服务或 Codex。
- **主回复**：面向用户最终发送的单次 LLM 生成流程。
- **预判断**：触发概率、主动聊天等轻量决策流程，不应携带主回复的全部动态上下文。
- **Image ID**：图片在 ImageContextPool 中的稳定引用标识，用于跨消息复用首次摘要和标注结果。
- **实例数据**：使某一个小天文人格、记忆、知识和用户状态与其他部署区分开的私有数据。
- **运行时数据**：容器挂载、插件 KV、QQ 数据和可重建缓存的总称。
