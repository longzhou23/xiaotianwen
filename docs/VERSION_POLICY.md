# 小天文版本策略

## 新机器基线

新机器不复用灾备目录里旧的 AstrBot 虚拟环境，也不继续使用旧的
`snowluma-local:v1.14.8` 镜像。当前基线（2026-08-26 解析）为：

| 组件 | 稳定标签 | 上游提交 | 运行策略 |
|---|---|---|---|
| AstrBot | `v4.27.4` | `4fe29759758255a35ad01ea6177a91c2293bfcd3` | 从该稳定标签建立全新的 Python 环境 |
| SnowLuma | `v1.14.13` | `6d08b8c5cf233d6a0204f642fe2bfc7720f1b256` | 用对应 Release 的 Linux `lite` 产物重建本地镜像 |

AstrBot 官方的源码部署说明区分了“最新提交”和“最新稳定发行标签”；新机采用稳定标签，避免直接跟随开发分支。SnowLuma Docker 框架同样消费主项目 Release 中与架构匹配的 `linux-x64-lite` 或 `linux-arm64-lite` 产物。

## 解析并锁定最新稳定版

在新机联网、依赖已安装后执行：

```bash
cd /opt/xiaotianwen/public
VERSION_FILE=/opt/xiaotianwen/private/deployment/versions.env \
  bash deploy/resolve-latest-versions.sh
```

脚本只读取公开仓库的 Git 标签，不读取或写入任何 API Key、QQ 登录数据或 Cloudflare 凭证。输出文件属于私有实例仓库，里面的提交哈希用于审计和回滚。

## SnowLuma 镜像

```bash
cd /opt/xiaotianwen/public/components/snowluma-framework
SNOWLUMA_TAG=v1.14.13 \
  IMAGE=snowluma-local:v1.14.13 \
  ./scripts/build-image.sh
```

构建完成后，Compose 使用具体镜像标签；不把 `latest` 作为生产运行时标签。升级时先停机备份，再构建新标签，健康检查通过后才替换 Compose 中的镜像。

## AstrBot 环境

AstrBot 源码和 Python 虚拟环境应在新机上重新创建，实例仓库只恢复 `data/`、插件、人格、知识库和配置。安装时使用 `versions.env` 中的 `ASTRBOT_TAG`，不要把旧机 `.runtime/astrbot` 直接复制过来。

升级顺序固定为：

1. 停止 AstrBot 和 SnowLuma；
2. 创建实例备份并记录当前版本；
3. 拉取新的稳定标签并创建新的虚拟环境/镜像；
4. 恢复实例数据和插件锁；
5. 先做 AstrBot Dashboard、OneBot、SnowLuma WebUI 和消息链路检查；
6. 检查通过后再切换服务；失败则恢复旧环境和旧镜像标签。

## 为什么不用漂移的 `latest`

“新机使用最新版本”和“生产环境永远跟随 latest”是两件事。新机部署时可以解析最新稳定版；解析结果必须马上写入私有部署锁，并在运行时使用具体标签和提交哈希。这样既能避免带着旧版本迁移，也能在上游更新导致插件或 QQ 接入不兼容时快速回滚。
