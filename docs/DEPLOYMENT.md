# 小天文当前部署说明

最后核对：2026-08-20。

本文只记录架构和运行约定；所有宿主机路径应通过部署环境注入，不应照抄旧机器的绝对路径。

## 当前架构

```text
QQ / QQ 群
  -> SnowLuma 容器（QQ 接入与 OneBot）
  -> AstrBot（消息编排、插件、模型与记忆）
```

- 公共代码目录：`${PROJECT_ROOT}/public`
- 私有实例目录：`${PROJECT_ROOT}/private`
- SnowLuma Compose 项目：`snowluma-live`
- SnowLuma 容器：`snowluma`
- SnowLuma 镜像：`snowluma-local:v1.14.8`
- AstrBot 工作目录：`${PROJECT_ROOT}/private/instance/astrobot-data`

NapCat 已停用。其程序、Compose、AppImage 运行时、看门狗、日志转发脚本和历史日志均在归档目录中，不属于当前运行链路。

## 当前挂载

SnowLuma 容器使用以下宿主机路径：

| 宿主机 | 容器 | 用途 |
|---|---|---|
| `snowluma/data/` | `/app/data` | SnowLuma 配置和运行数据 |
| `.runtime/home/.config/QQ/` | `/app/.config/QQ` | QQ 配置、账号与登录数据 |
| `snowluma/qq-data/` | `/app/.local/share` | QQ 客户端数据 |

实际 Compose 文件和私有环境变量位于 `snowluma-live/`。`snowluma-framework/` 保留当前本地镜像的 Dockerfile、启动文件和 Framework 包，供重建镜像使用。

## 管理命令

```bash
cd "$PROJECT_ROOT/public"

# 查看 AstrBot、SnowLuma 和关键监听端口
./bin/status

# 检查依赖、配置、容器挂载与健康状态
./bin/doctor

# 启动 AstrBot 和 SnowLuma
./bin/start

# 停止 SnowLuma，再停止 AstrBot
./bin/stop

# 重启全部服务，也可指定 astrbot 或 snowluma
./bin/restart all

# 查看最近日志；跟随日志时需指定单个服务
./bin/logs all --tail 100
./bin/logs snowluma --follow

# 停机制作活动目录备份，完成后恢复原运行状态
./bin/backup
```

启动、停止和重启命令均可接受 `all`、`astrbot` 或 `snowluma`。所有会改变服务状态的维护命令共用维护锁，避免与备份并发执行。

备份写入：

```text
${BACKUP_ROOT:-$PROJECT_ROOT/backups}/current/
```

## 管理入口与日志

- AstrBot Dashboard：`http://服务器地址:6200`
- SnowLuma WebUI：宿主机回环地址 `127.0.0.1:5099`
- SnowLuma noVNC：宿主机回环地址 `127.0.0.1:6081`
- AstrBot OneBot 反向 WebSocket：端口 `8001`
- AstrBot 日志：`logs/astrbot.log`
- SnowLuma 日志：`docker logs -f snowluma`

SnowLuma 的 5099 和 6081 端口当前只绑定在宿主机回环地址。需要从其他机器访问时，应使用 SSH 端口转发，不建议直接暴露到局域网或公网。

## 迁移要点

迁移时分别复制公共仓库、私有实例仓库和主机 secret 文件；不要把运行时、日志或凭证重新塞回公共仓库。

目标主机需要 Docker Compose，并需重新构建或导入 `snowluma-local:v1.14.8` 镜像。迁移后检查 `snowluma-live/compose.yml` 中的绝对路径、目标用户 UID/GID，以及 `.env` 文件权限。`.env`、QQ 数据、AstrBot 配置和日志可能包含凭据或隐私数据，不应公开。
