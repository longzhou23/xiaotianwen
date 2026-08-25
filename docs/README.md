# xtw_bot 当前运行目录

本目录只保留当前 SnowLuma + AstrBot 架构所需内容。NapCat、旧实验部署、历史备份和回滚快照已移至：

```text
/home/developer/xtw_bot_archive
```

## 当前组件

| 路径 | 用途 | 状态 |
|---|---|---|
| `astrobot/` | AstrBot 配置、插件、数据库和业务数据 | 正在使用 |
| `.runtime/astrbot/` | AstrBot Python 运行环境 | 正在使用 |
| `.runtime/home/.config/QQ/` | SnowLuma 容器挂载的 QQ 登录数据 | 正在使用 |
| `snowluma/data/` | SnowLuma 配置和运行数据 | 正在使用 |
| `snowluma/qq-data/` | SnowLuma 容器的 QQ 客户端数据 | 正在使用 |
| `snowluma-live/` | 当前 Docker Compose 配置和私有环境文件 | 正在使用 |
| `snowluma-framework/` | 当前 `snowluma-local:v1.14.8` 镜像的构建来源 | 保留用于重建 |
| `<bot-instance-id>/` | 离线人格、知识和记忆生成工具及产物 | 私有实例数据，不随公共仓库发布 |
| `bin/` | 当前启动、停止、状态和备份命令 | 正在使用 |
| `logs/` | 当前 AstrBot 日志 | 正在使用 |

## 日常命令

```bash
cd /home/developer/xtw_bot
./bin/status
./bin/doctor
./bin/start
./bin/stop
./bin/restart
./bin/logs all --tail 100
./bin/backup
```

各命令的服务选择、日志跟随、备份输出目录和超时参数见 [bin/README.md](bin/README.md)。

`bin/backup` 会把新的活动项目备份写入 `/home/developer/xtw_bot_archive/backups/current/`，不会再把备份塞回运行目录。

NapCat 时期的历史说明文档已归档到 `/home/developer/xtw_bot_archive/deprecated/historical-docs/`；当前部署信息以本文件和 `DEPLOYMENT.md` 为准。
