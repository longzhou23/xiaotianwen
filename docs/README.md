# 小天文公共代码仓库

本目录只保留 SnowLuma + AstrBot 所需的公共代码、插件、脚本和模板。实例数据、NapCat 历史部署、备份和回滚快照不在这里。

## 当前组件

| 路径 | 用途 | 状态 |
|---|---|---|
| `plugins/` | 第三方、二次开发和自研插件源码 | 公共代码 |
| `components/snowluma-framework/` | SnowLuma 镜像构建来源 | 公共代码 |
| `deploy/` | 不含凭证的 Docker 部署模板 | 公共模板 |
| `config/` | 配置模板和版本基线 | 公共模板 |
| `scripts/` | 安装、启停、诊断和日志脚本 | 公共脚本 |
| `docs/` | 迁移、部署和设计文档 | 公共文档 |

## 文档入口

- [项目架构总览](ARCHITECTURE.md)：组件边界、消息管线、数据分层、插件职责、部署拓扑和恢复验收。
- [部署说明](DEPLOYMENT.md)：当前 Docker/服务部署细节。
- [迁移说明](MIGRATION.md)：公共代码、私有实例与新主机恢复流程。
- [版本策略](VERSION_POLICY.md)：AstrBot、SnowLuma 和插件的版本管理。
- [Agent Loop 优化 TODO](AGENT_LOOP_OPTIMIZATION_TODO.md)：当前 4.27.4 工具循环、缓存链路、性能基线、分阶段实现项与验收指标。

## 日常命令

```bash
cd "$PROJECT_ROOT/public"
./scripts/status
./scripts/doctor
./scripts/start
./scripts/stop
./scripts/restart all
./scripts/logs all --tail 100
./scripts/backup
```

各命令的服务选择、日志跟随、备份输出目录和超时参数见 [scripts/README.md](../scripts/README.md)。

私有实例的数据库、人格、知识库和 QQ 数据请按 [MIGRATION.md](MIGRATION.md) 从 private 仓库恢复。NapCat 已退休，不属于当前部署链路。
