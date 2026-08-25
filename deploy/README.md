# 自动部署脚本

这些脚本面向 Ubuntu 24.04。它们负责仓库拉取、依赖安装、实例数据恢复、插件源码布置，以及拉取和启动官方 AstrBot/SnowLuma `latest` 容器镜像。版本更新和回退规则见 [`docs/VERSION_POLICY.md`](../docs/VERSION_POLICY.md)。

## 新机首次运行

在已拉取公共仓库的目录执行：

```bash
export PROJECT_ROOT=/opt/xiaotianwen
export PUBLIC_REPO_URL=https://github.com/<owner>/xiaotianwen.git
export PRIVATE_REPO_URL=https://github.com/<owner>/xiaotianwen-instance.git
export SECRET_FILE=/etc/xiaotianwen/secrets.env

# 不要把 Token 直接写进 shell 历史；也可以改用 SSH agent/credential helper。
read -rsp 'GitHub token: ' GITHUB_TOKEN; echo
export GITHUB_TOKEN

sudo install -d -m 700 /etc/xiaotianwen
sudo install -m 600 /dev/null "$SECRET_FILE"
# 用安全方式填充 secrets.env 后：
bash deploy/bootstrap.sh
```

若目标机已经预装依赖，可显式设置 `INSTALL_SYSTEM_DEPS=0`。脚本不会把 `GITHUB_TOKEN` 写入 remote URL；克隆完成后临时 `ASKPASS` 文件会被清理。

## 启动与更新

`bootstrap.sh` 默认会恢复实例数据并启动服务。后续更新使用：

```bash
bash deploy/update.sh
```

该命令会先拉取官方 `latest` 镜像，再重建两个容器并记录实际 image digest；普通 Docker/服务重启不执行镜像拉取。

## 更新与备份

```bash
bash deploy/update.sh
bash deploy/backup.sh
bash deploy/restore.sh /path/to/xiaotianwen-instance-YYYYMMDD-HHMMSS.tar.gz
```

更新前要求两个仓库工作区干净。备份默认写到项目根目录外的 `backups/`，排除日志、缓存、缩略图和临时目录；备份文件权限为 600。

## 当前限制

- 脚本不会自动猜测或生成真实 API Key、QQ 登录数据和 Cloudflare 凭证；
- `restore.sh` 先解压到临时目录，再复制到私有实例目录，不使用 `--delete`；
- 生产环境仍应在停服后恢复数据库，并完成只读检查后再启动服务；
- 在真正承诺“30 分钟全自动恢复”之前，需要在干净 VM 上进行一次完整演练并记录版本、耗时和失败回滚路径。
