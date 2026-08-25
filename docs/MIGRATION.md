# 小天文迁移与恢复指南

本文把公共代码仓库与私有实例仓库组合成一个可迁移的部署。示例路径均使用环境变量，不依赖旧 NUC 或灾备盘上的绝对路径。

## 仓库关系

```text
public/xiaotianwen              代码、插件、脚本、模板
private/xiaotianwen-instance    persona、知识库、数据库、实例配置
主机 secret 文件                API Key、Token、证书、私钥
```

公共仓库可以公开审阅；私有仓库只保存已经脱敏的实例数据。真实凭证应在部署机上单独注入，不要把 `.env`、`.pem`、`.key` 或 Cloudflare 配置复制进任一 Git 仓库。

## 新 Ubuntu 机器恢复

```bash
export PROJECT_ROOT=/srv/xiaotianwen
mkdir -p "$PROJECT_ROOT"

git clone <PUBLIC_REPO_URL> "$PROJECT_ROOT/public"
git clone <PRIVATE_REPO_URL> "$PROJECT_ROOT/private"

cd "$PROJECT_ROOT/public"
bash scripts/install-ubuntu.sh
```

然后按私有仓库中的 `README.md` 恢复顺序，将以下目录复制到对应的 AstrBot/SnowLuma 数据目录：

```text
private/instance/astrobot-data
private/instance/persona-source
private/instance/snowluma-data
private/knowledge
```

恢复前先停止服务并建立目标机快照；恢复后不要覆盖目标机中可能已经生成的新数据。数据库文件和 `-wal`/`-shm` 文件必须成组复制，若目标机正在运行则先停服务。

## 凭证注入

从 `config/.env.example` 和 `private/secrets/README.md` 生成部署机上的私有环境文件，逐项填入真实值。完成后检查：

```bash
chmod 600 /etc/xiaotianwen/secrets.env
grep -RInE '(^|[=:])[[:space:]]*(sk-|eyJ|-----BEGIN|[A-Za-z0-9]{24,})' \
  public private --exclude='*.db*' --exclude='train_data.json'
```

命令只用于发现误提交风险；QQ 图片文件 ID、测试用假值和已脱敏的 `${SECRET_*}` 占位符需要人工复核，不能把扫描结果直接当成泄露。

## 验收清单

- [ ] 公共仓库不含 `.runtime`、日志、数据库、知识库索引和真实凭证；
- [ ] 私有仓库中的配置只含 `${SECRET_*}` 占位符；
- [ ] AstrBot Provider ID 与目标机实际配置一致；
- [ ] Iris L2/L3、画像、好感度和知识库查询可读；
- [ ] ContextAware、图片上下文池、分段和主动聊天的职责没有重复启用；
- [ ] SnowLuma Web/VNC、AstrBot 面板和 SSH 隧道均能健康访问；
- [ ] 完成一次消息收发、图片上下文、知识库查询和服务重启演练。

## 回滚

迁移只应写入新目录或临时目录。验收失败时停止服务，保留目标机原目录和本次恢复目录，切换服务指向上一份已验证的快照；不要用 `git reset --hard` 覆盖实例数据。
