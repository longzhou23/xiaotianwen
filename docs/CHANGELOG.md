# Changelog

本文件记录 AstrBot 工作区级别的重要变更。

## [P1 Foundation] - 2026-09-02

### XiaoTianWen Cognitive Runtime checkpoint

- 正式项目身份整理为 **XiaoTianWen Cognitive Runtime**；`Iris Memory` 保持为记忆子系统与兼容宿主位置，`AstrBot` 保持为当前宿主适配环境。
- P0/P1 foundation 标记为 **ACCEPTED**：identity/perspective、Episode/Outcome、BehaviorExecutionRecord、ReviewRun/ReviewFinding 和 Cognitive Observatory 均纳入公共 checkpoint。
- ReviewEvidence 合同与 Store integrity surface 保留，但 P1 正常运行中的 promotion gate 继续 **DISABLED / FAIL-CLOSED**；Finding 不会改变未来行为或任何 Iris/Persona/Affect/Relationship 状态。
- 增加 bounded ExecutionRecord observability、request-local Preview Review、历史 replay fixture，以及 HOST_OUTPUT execution identity/revision/stage 的 immutable linkage 校验。
- 不修改既有插件 identifier、Python import path、AstrBot 路由、配置 key、存储目录或 Iris 数据；本版本不是生产记忆迁移。

### 已知债务

- ToolResult frozen factual contract 与未来逐陈述 promotion contract 尚未冻结，因此 ToolResult 证据保持 fail-closed。
- ExecutionRecord registry 仍是 bounded/runtime-local；生产 ReviewStore 尚未接入运行链路。
- Dashboard/manual showcase、真实 Provider/QQ、生产并发和长时运行仍需独立验收；Windows Chroma `metadata.db` WinError 32 与本 checkpoint 无关。

## [Unreleased] - 2026-08-20

### 变更：活动目录与历史归档分离

- 以现场运行状态为准，确认 SnowLuma 已取代 NapCat 作为当前 QQ 接入层。
- 将 NapCat、旧 SnowLuma 实验、历史备份、回滚快照、迁移文件和无关 npm 安装移动到独立的归档根目录。
- 停止残留的 NapCat 看门狗和日志转发进程，保留运行中的 SnowLuma 与 AstrBot。
- 重写 `bin/start`、`bin/stop`、`bin/status` 和 `bin/backup`，使其管理 SnowLuma Compose；新备份写入外部归档目录。
- 为活动目录和归档目录分别增加 README，明确当前文件与历史文件边界。
- 统一更新 `bin/` 维护脚本：增加 PID 身份校验、维护锁、启动就绪等待、健康状态退出码、备份失败恢复和半成品标记。
- 新增 `bin/restart`、`bin/doctor`、`bin/logs` 与 `bin/README.md`，支持按 AstrBot/SnowLuma 单独维护。

## [Unreleased] - 2026-08-18

### 变更：NapCat 切换为 Docker 部署

- NapCat 从 AppImage（`.runtime` 内 4.18.18）切换为 Docker（`mlikiowa/napcat-docker:latest`，
  容器内 NapCat 4.18.19，host 网络）。AppImage 保留为 Docker 不可用时的回退
  （`NAPCAT_MODE=appimage` 可强制）。
  - 背景：AppImage 模式在本机反复出现 Worker 段错误（139）崩溃（登录态损坏的
    `login.db` 已清理修复，但容器化可彻底隔离此类环境问题），且 WebUI/端口管理不便。
  - `napcat-docker/compose.yml`：复用原配置（host 网络、QQ 数据目录挂载、代理清除），
    新增 `napcat/cache` 卷挂载（二维码落在宿主机原路径）。
  - `bin/common.sh` 新增 `napcat_use_docker()` / `napcat_docker_running()` /
    `napcat_docker_exists()` / `docker_compose()` 辅助。
  - `bin/start`：Docker 优先；检测到 AppImage 残留时先停止再拉起容器。
  - `bin/stop` / `bin/restart-napcat`：Docker 容器 down/up，AppImage 清扫保留为兜底。
  - `bin/status`：分别报告 Docker 容器与 AppImage 实例状态。
  - 容器 `restart: unless-stopped`：Docker 服务随系统启动后 NapCat 自动拉起。

### 修复

- 修复 NapCat 反复被重复拉起导致登录冲突与网络连接异常的问题。
  - 背景：08-17 22:28 ~ 08-18 00:47 期间 NapCat 被重复启动最多 7 次，新实例均报
    “当前账号(<BOT_ID>)已登录,无法重复登录”，WebUi 端口从 7000 漂移到 7004，
    反向 WebSocket 反复断开，QQ 发送消息出现 `1006514 网络连接异常!`。
  - `bin/common.sh` 新增 `napcat_pids()` / `napcat_running()` 进程级检测
    （按 cmdline 匹配 `NapCat.AppImage` 与 `/tmp/.mount_NapCat*` 子进程）。
  - `bin/run-napcat` 增加单实例守护：检测到已有实例时拒绝启动（可设
    `NAPCAT_ALLOW_DUPLICATE=1` 强制多开，仅调试用）。
  - `bin/start` 启动 NapCat 前做进程级兜底检查：已有实例存活时复用并刷新
    pid 文件，不再重复拉起。
  - `bin/stop` 增加残留 NapCat 进程清扫，可清理 pid 文件未记录的并存实例。
  - `bin/status` 报告 NapCat 存活实例数，多实例并存时给出告警。
  - 新增 `bin/restart-napcat`：一键干净重启 NapCat（终止全部实例 → 等待
    AstrBot 8001 → 重新启动，保留 QQ 登录态）。
  - `bin/stop` / `bin/restart-napcat` 增加孤儿 Xvfb :99 清理：崩溃实例不会
    自行回收显示服务器，残留 Xvfb 会令新实例复用旧显示并堆积内存
    （08-18 00:57 崩溃后 xvfb.log 已出现 10 次 “Server is already active
    for display 99”）。
  - 修复登录崩溃：`global/nt_db/login.db` 因强杀登录中实例而损坏，登录流程
    解析时 Worker 段错误连崩 3 次导致主进程退出；清除损坏登录状态（备份至
    `backups/login-db-reset-20260818/`，保留聊天历史）后恢复正常。

## [Unreleased] - 2026-08-17

### 移除

- 手动移除 `astrbot_plugin_forward_reader`。
- 手动移除 `astrbot_plugin_html_render`。
- 清理上述插件残留的配置内容。

### 修复

- 将 OpenAI SDK 日志级别设为 `info`，避免 DEBUG 日志写入完整的模型请求正文。
- 统一 Iris Chat Memory 与 Stealer 的 Embedding Provider ID 为 `ollama-bge-m3`。
- 将 Stealer 的 Embedding 初始化延后至 AstrBot Provider 加载完成后，并增加启动重试机制，修复启动竞态。
- 修复 Stealer 启动时的历史向量回填流程，已完成 `93/93` 条向量回填。
- 修复 QZoneTools 直接调用 `playwright` 命令失败的问题，改为通过当前 Python 运行时执行 `python -m playwright`。

### 验证

- AstrBot 已重启并正常运行。
- 重启后完整 OpenAI 请求正文 DEBUG 记录数为 `0`。
- 运行时 Playwright 版本为 `1.62.0`。

### 备注

- 历史日志文件未清理，其中可能仍保留修复前的请求正文记录。
