# 小天文可复现部署与自动化管理需求

## 1. 目标

小天文应成为一个可复现部署的实例系统：在一台全新的 Ubuntu 24.04 x64 服务器上，只提供仓库访问权限和必要的运行时密钥，即可恢复完整运行环境。

目标不是把某台机器完整打包，而是把以下内容分层管理：

```text
公共仓库       代码、插件、框架、脚本、模板
私有仓库       persona、知识库、数据库、用户状态、实例配置
主机 secret    API Key、Token、证书、私钥
运行时缓存     日志、临时文件、Python 缓存、模型缓存（可重建）
```

## 2. 仓库架构

### Public：`xiaotianwen`

保存可公开维护的软件资产：自研通用代码、第三方插件源码、二次开发插件、SnowLuma framework、部署脚本和文档。

禁止提交 API Key、Token、QQ 登录数据、用户数据、私人人格、私有知识库、实例数据库和真实网关凭证。

### Private：`xiaotianwen-instance`

保存一个具体实例的状态：

```text
instance/
  persona-source/
  astrobot-data/
  snowluma-data/
knowledge/
plugins/             # 仅私有插件
plugins.lock.yaml    # 实例插件启用/版本状态
deployment/
secrets/             # 只放说明和模板，不放真实值
```

## 3. 插件管理

插件分为 `upstream/`、`modified/`、`custom/` 和 `retired/`。每个插件必须包含 `plugin.meta.yaml`，记录名称、来源仓库、版本、许可证、类型、是否修改和维护者。

私有实例的 `plugins.lock.yaml` 是部署时的插件状态来源，用于固定版本、启用状态和归属仓库。部署器不得根据目录猜测是否启用插件。

## 4. 自动化入口

公共仓库提供：

```text
deploy/bootstrap.sh   新机器首次部署
deploy/install.sh     安装依赖并恢复实例
deploy/update.sh      拉取两个仓库并滚动更新
deploy/backup.sh      备份可恢复数据，排除日志和缓存
deploy/restore.sh     从备份恢复到临时目录后验收
```

脚本必须：

- 要求显式的 `PROJECT_ROOT`、仓库地址和 secret 文件；
- 支持已认证的 SSH/credential helper，也支持通过 `GITHUB_TOKEN` 临时 `ASKPASS` 拉取私有仓库；
- 不把 Token 写入 remote URL、日志或提交历史；
- 默认不删除目标目录中的未知文件；
- 在启动服务前完成配置、数据库和插件检查；
- 失败时保留中间目录，便于审计和回滚。

## 5. 密钥管理

Git 中只能保存 `.env.example`、`secrets/README.md` 和变量名。真实值通过部署主机上的 root-only secret 文件或外部 secret manager 注入。日志和错误信息不得输出密钥内容。

## 6. 数据策略

必须保存：persona、knowledge、数据库、memory、实例配置和插件持久化数据。

可重建：logs、cache、temporary files、Python cache、模型缓存、图片缩略图和插件临时目录。

数据库的 `db`、`db-wal`、`db-shm` 文件必须停服后成组备份；恢复前先做校验，恢复后先只读验收再启动服务。

## 7. 验收标准

在干净 Ubuntu 24.04 x64 虚拟机中：

```bash
./deploy/bootstrap.sh
```

应完成依赖安装、仓库拉取、插件恢复、数据恢复、服务注册和启动检查。最终至少验证 AstrBot、SnowLuma、插件加载、persona、知识库查询、API 调用和一次服务重启。

第一版脚本只提供安全的可执行骨架；AstrBot 上游版本、系统服务名称、Provider 映射和真实 secret 仍需在目标机上显式确认后才能达到“30 分钟完全恢复”的承诺。

## 8. 非目标

当前阶段不实现 Kubernetes、多节点、高可用或自动扩缩容，只保证单实例可恢复、可迁移和可回滚。
