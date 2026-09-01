# 2026-09-02 Agent Loop 分批验收记录

## 结论

本轮完成 P0 Hook 字段审计、P1 Provider payload/cache/multimodal 边界、公共/私有锁同步和一次保留实例的 Azure 可回退部署。离线与真实 AstrBot 镜像测试通过；Azure 基础服务和 OneBot 重连通过。

发布门禁仍为 `NOT_VERIFIED`：真实 QQ 功能矩阵、24 小时 Shadow、100 Turn Canary、72 小时 SnowLuma、性能指标和空白 VM 恢复尚未完成。Orchestrator 在私有 `plugins.lock.yaml` 中保持 `enabled: false`，没有接管生产主回复。

## Git 与回退点

- Public：`ed2f3f8e5a28f192696cbf0083417becf48fbbe0`
- Private：`1201c661476a526df18ed6de1aaf31122aaa6955`
- 本地开工 tag：`pre-todo-completion-20260902-005637`
- 本地 Public bundle SHA-256：`F205FFA9C22021E09DC90843738ADF60012D3D0F79535BF28DCEB4594F916056`
- 本地 Private working-tree archive SHA-256：`3A7B77DEF77E1E8167B2F59B8E7A8DDE1B1AC1F930569566204465BEB6645258`
- Azure 停服一致性快照：`/home/developer/xiaotianwen/backups/pre-update/runtime-20260901-171716.tar.gz`
- Azure 快照大小：`1668559035` bytes
- Azure 快照 SHA-256：`677fabc714cad0a66955fe44cc1c42e940d01d23d7d5ed7dacbba7bbd88b9cc6`
- Azure 快照完整 `tar -tzf` 流校验：passed。
- Azure 部署前后记录：`/home/developer/xiaotianwen/backups/deploy-20260901-171711/`

快照在 AstrBot/SnowLuma 均停止后生成。主机用户遇到 root-owned Iris/Codex 文件时，脚本按设计改用只读 root 容器完成归档。部署使用 `RESTORE_INSTANCE=0`，没有用私有仓库旧快照覆盖现有 Iris、图片池、表情包、QQ 或数据库状态。

## 自动化证据

### Windows 离线

- Codex Provider/transport 定向套件：67 passed。
- Orchestrator 套件：58 passed、1 skipped；跳过项明确要求安装 AstrBot，随后已在 Azure AstrBot 镜像中执行。
- `full-offline`：43/43 replay passed；7 个可运行插件矩阵项 passed；Astrometry、Iris、Stealer 仍为 `NOT_RUN`；无网络/写入违规。
- `compileall` 和 `git diff --check` 通过。
- 本轮公共变更文件敏感凭据特征扫描无命中。

### Azure AstrBot 镜像

- 运行容器可导入 AstrBot `ProviderRequest`、`LLMResponse`、`TokenUsage`、`ToolSet` 和 `FunctionTool`。
- 使用当前 `soulter/astrbot:latest` 镜像、`--network none`、只读源码挂载和 tmpfs 工作区运行 Codex Provider + Orchestrator 套件，126 项通过，退出码 0。
- 启动日志未发现插件加载失败、HTTP 403 或 LLM Provider 初始化错误；FAISS AVX2 可选模块缺失由通用实现回退，不计为阻断。

## Azure 部署与故障演练

- AstrBot image：`soulter/astrbot@sha256:5d23f264ba9cb9b03a2bc1ef87f1ac87c03932aa99459b497afacb6a7c38aa8e`
- SnowLuma image：`motricseven7/snowluma@sha256:2f3596c8bc0ca6f7c773802d7f7b000f79a270403ef22db468b9861ce12c8a05`
- 分别重启 AstrBot、SnowLuma、全部服务后，Dashboard、OneBot 8001、SnowLuma WebUI 和 noVNC 均通过验证。
- SnowLuma 重新连接 `ws://astrbot:8001/ws`；AstrBot 记录 WebSocket HTTP 101 和 OneBot v11 已连接。
- 第二个 8001 端口绑定探针被 Docker 拒绝，现役容器没有被替换或中断。
- 最终 Public/Private 远端工作树均干净；AstrBot/SnowLuma 均 running，restart count 为 0。

## 仍未验收

- 未主动向真实群/私聊发送测试消息，未验证 persona、主动聊天、图片、表情包、Astrometry、Iris 写入和工具副作用。
- 未强制 QQ 登出、未断开宿主网络、未重启整台 Azure VM。
- 未运行 24 小时 Shadow、100 Turn Canary、72 小时 SnowLuma 和发布后 2/24 小时观察。
- 未执行破坏性“把快照覆盖回 live runtime”的恢复；当前证据证明一致性快照可生成并校验，不等同于完整回滚演练。
- 本机 Private 工作树仍含用户的运行数据变化；本轮只提交 `plugins.lock.yaml`，没有广泛暂存数据库、凭据、会话或 `CODEX_HOME`。
