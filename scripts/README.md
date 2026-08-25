# 维护脚本

所有脚本均以自身位置解析项目根目录，可从任意工作目录调用。

| 命令 | 用途 |
|---|---|
| `bin/start [all\|astrbot\|snowluma]` | 启动并等待服务就绪 |
| `bin/stop [all\|astrbot\|snowluma]` | 优雅停止服务 |
| `bin/restart [all\|astrbot\|snowluma]` | 重启并执行就绪检查 |
| `bin/status [--quiet]` | 查看运行、端口、磁盘和最近备份状态 |
| `bin/doctor` | 执行依赖、配置、挂载和健康诊断 |
| `bin/logs [服务] [--tail N] [--follow]` | 查看 AstrBot 或 SnowLuma 日志 |
| `bin/backup [--output-dir DIR]` | 停机制作一致性备份并恢复原运行状态 |
| `bin/run-astrbot` | AstrBot 内部启动入口，通常无需直接调用 |

会改变服务状态的命令共用 `.runtime/run/maintenance.lock`，防止启动、停止、重启和备份并发执行。

可通过环境变量调整等待时间：

- `XTWBOT_START_TIMEOUT`：启动就绪等待秒数，默认 60。
- `XTWBOT_STOP_TIMEOUT`：优雅停止等待秒数，默认 30。
- `XTWBOT_ARCHIVE_ROOT`：备份归档根目录，默认 `/home/developer/xtw_bot_archive`。
