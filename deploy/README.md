# 自动部署脚本

这些脚本面向 Ubuntu 24.04。它们负责仓库拉取、依赖安装、实例数据恢复、插件源码布置，以及拉取和启动官方 AstrBot/SnowLuma `latest` 容器镜像。启动 latest 镜像后还会把 Codex CLI 安装/校验到 AstrBot 的持久化数据挂载中。版本更新和回退规则见 [`docs/VERSION_POLICY.md`](../docs/VERSION_POLICY.md)。

## 新机首次运行

在已拉取公共仓库的目录执行：

```bash
export PROJECT_ROOT="$HOME/xiaotianwen"
export PUBLIC_REPO_URL=https://github.com/<owner>/xiaotianwen.git
export PRIVATE_REPO_URL=https://github.com/<owner>/xiaotianwen-instance.git
export SECRET_FILE="$PROJECT_ROOT/.host-secrets/secrets.env"

# 不要把 Token 直接写进 shell 历史；也可以改用 SSH agent/credential helper。
read -rsp 'GitHub token: ' GITHUB_TOKEN; echo
export GITHUB_TOKEN

# 按 deploy/secrets.env.example 填入外部服务密钥；OneBot 的内部令牌可留空，
# bootstrap 会生成一对完全相同的随机值。
install -d -m 700 "$(dirname "$SECRET_FILE")"
[[ -e "$SECRET_FILE" ]] || install -m 600 deploy/secrets.env.example "$SECRET_FILE"
# 用编辑器填入模型和插件密钥后：
bash deploy/bootstrap.sh
```

OneBot 反向 WebSocket 的凭据也放在这个主机私有文件中（只写变量名，不要提交到仓库）：

```ini
SECRET_WS_REVERSE_TOKEN=由部署机生成的随机长令牌
SECRET_ACCESSTOKEN=与上面相同的令牌
```

默认使用 `$PROJECT_ROOT/.host-secrets/secrets.env`。如果运维规范要求集中存放，也可显式设置 `SECRET_FILE=/etc/xiaotianwen/secrets.env`。脚本严格按 `NAME=value` 读取，不执行 secret 文件里的 shell 内容，也不会在日志中打印值。

若目标机已经预装依赖，可显式设置 `INSTALL_SYSTEM_DEPS=0`。脚本不会把 `GITHUB_TOKEN` 写入 remote URL；克隆完成后临时 `ASKPASS` 文件会被清理。

Ubuntu 仓库的 Compose 包名会按 `docker-compose-v2`、`docker-compose-plugin`、
`docker-compose` 的顺序自动探测。若首次安装后提示当前 shell 无权访问 Docker，执行
一次 `newgrp docker`，然后以 `INSTALL_SYSTEM_DEPS=0` 重跑即可。

## 启动与更新

`bootstrap.sh` 默认会恢复实例数据并启动服务。后续更新使用：

```bash
bash deploy/update.sh
```

日常服务控制使用以下入口；参数可以是 `all`、`astrbot` 或 `snowluma`，省略时默认为 `all`：

```bash
bash deploy/start.sh all       # 启动两个服务
bash deploy/stop.sh snowluma   # 只停止 SnowLuma
bash deploy/restart.sh astrbot # 只重启 AstrBot
bash deploy/status.sh all      # 查看容器、重启次数和端口状态
```

`start.sh` 使用 Compose 恢复已有容器，不会主动拉取 latest 镜像；需要升级镜像时使用
`update.sh`。`stop.sh` 会给服务 30 秒优雅退出时间。

每次 `bootstrap.sh` 或 `update.sh` 完成容器启动后，`up-latest.sh` 会调用
`install-codex.sh`：在 AstrBot 容器内使用镜像自带的 Node/npm，将 `@openai/codex@latest`
安装到 `/AstrBot/data/codex`，并执行 `codex --version` 校验。宿主机对应目录是
`runtime/astrobot/data/codex`，因此容器重建不会删除 Codex 文件。该步骤不会自动登录
Codex，也不会输出或生成登录凭据；登录应由运维者在持久化的 `CODEX_HOME` 中单独完成。

可配置：

```bash
CODEX_PACKAGE=@openai/codex CODEX_VERSION=latest bash deploy/install-codex.sh
```

如果某个实例明确不使用 Codex，可以设置 `CODEX_INSTALL_REQUIRED=0` 跳过；默认值为 `1`，
安装失败会使部署失败并触发既有的镜像回滚逻辑，避免产生“部署成功但插件实际找不到 Codex”的半可用状态。

该命令会先拉取官方 `latest` 镜像，再重建两个容器并记录实际 image digest；普通 Docker/服务重启不执行镜像拉取。

实例恢复与日常更新已经分离：

- 首次部署且没有 `runtime/.deploy/instance-restored` 时，private 快照会恢复一次；
- 日常 `update.sh` 只更新仓库、插件源码和容器镜像，不覆盖实时数据库；
- 只有明确设置 `RESTORE_INSTANCE=1` 才会再次把 private 快照恢复到 runtime，且两个容器必须先停止。

更新执行顺序为：预检 → 拉镜像（旧服务继续运行）→ 停服 → 一致性运行时快照
→ 渲染配置与同步受锁文件管理的插件 → 启动新镜像 → HTTP/noVNC/OneBot 验证。验证失败时会尝试重新标记并启动旧镜像，运行时快照保留在 `backups/pre-update/`。

不要在正在运行的实例上直接执行 `install.sh`；脚本会主动拒绝这种用法，避免 Python 插件或 JSON 配置只更新一半。在线实例统一使用 `up-latest.sh`（或会调用它的 `update.sh`）。

可单独执行只读检查：

```bash
bash deploy/preflight.sh
bash deploy/verify.sh
```

默认要求项目所在分区至少有 4096 MiB 可用空间。可用 `MIN_FREE_MIB` 调整，但不建议在磁盘接近写满时拉取 `latest`。

若目标网络无法访问 Docker Hub，可在项目根目录创建 `host-images.env`，仅覆盖镜像的拉取路径而不改变 `latest` 更新策略：

```bash
cp deploy/host-images.env.example ../host-images.env
```

该文件是宿主网络配置，不含密钥，也不应提交到 public 仓库。

## 更新与备份

```bash
bash deploy/update.sh
bash deploy/backup.sh
bash deploy/restore.sh /path/to/xiaotianwen-instance-YYYYMMDD-HHMMSS.tar.gz
```

更新前要求两个仓库工作区干净。备份默认写到项目根目录外的 `backups/`，排除日志、缓存、缩略图和临时目录；备份文件权限为 600。

## 当前限制

- 脚本不会自动猜测或生成真实 API Key、QQ 登录数据和 Cloudflare 凭证；
- 脚本只会自动生成内部 OneBot WebSocket 令牌；模型、检索与插件密钥仍需由运维者提供；
- `restore.sh` 先解压到临时目录，再复制到私有实例目录，不使用 `--delete`；
- 生产环境恢复数据库前必须停止两个容器；恢复包会先检查绝对路径、`..` 和危险链接；
- 在真正承诺“30 分钟全自动恢复”之前，需要在干净 VM 上进行一次完整演练并记录版本、耗时和失败回滚路径。

## 每日自动同步到云端仓库

`autosync.sh` 用于把运行时中允许保存的 AstrBot/SnowLuma 数据同步回 private 仓库，
并把公共代码和私有实例分别提交、推送到各自的 `origin`。默认行为如下：

- 默认在同步前停止两个容器，保证 SQLite/WAL、Iris 记忆和插件配置的一致性，完成后自动启动；
- 默认不上传运行时 `config/`、`cmd_config.json`、`mcp_server.json`、`skills.json`、`qq-config`、`qq-data`、
  密钥、`.env`、私钥、日志和缓存；QQ 数据只有显式设置
  `AUTOSYNC_INCLUDE_QQ_DATA=1` 才会加入；
- 私有仓库有未提交修改时直接退出，不覆盖人工编辑；
- 只允许 public 仓库的代码、部署、脚本、配置模板和文档路径进入自动提交；
- 提交前检查常见 API Key、GitHub Token 和私钥模式，命中就中止 push；
- 通过部署锁避免与 update/backup 并发；push 依赖服务器上已经配置好的 SSH key 或 credential helper。

先在服务器上进行不落盘、不提交的检查：

```bash
AUTOSYNC_DRY_RUN=1 bash deploy/autosync.sh
```

确认仓库 remote、Git 身份和排除范围都正确后，再安装每日定时任务。例如服务器已设为上海时区时，
每天 03:30 执行：

```bash
bash deploy/install-autosync-cron.sh 03:30
```

定时任务日志写入 `runtime/logs/autosync.log`，锁文件为
`runtime/.deploy/autosync.cron.lock`。卸载时用 `crontab -e` 删除
`BEGIN/END XIAOTIANWEN AUTOSYNC` 标记之间的内容；不要直接清空整份 crontab。

自动 push 只适合私有仓库和已完成访问控制的公共仓库。它不会自动登录 GitHub、Codex 或其他服务，
也不会把 host secret 文件同步到云端。若不希望每日停机，可设置 `AUTOSYNC_QUIESCE=0`，但这时
数据库和记忆文件是在线快照，恢复一致性由运维者自行承担。
