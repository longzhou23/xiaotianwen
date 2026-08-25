# 小天文版本策略

## 新机器和更新策略

小天文的 AstrBot 与 SnowLuma 都跟随官方发布镜像的 `latest`：

| 组件 | 运行镜像 | 拉取时机 |
|---|---|---|
| AstrBot | `soulter/astrbot:latest` | 新机部署和显式执行 `deploy/update.sh` |
| SnowLuma | `motricseven7/snowluma:latest` | 新机部署和显式执行 `deploy/update.sh` |

新机器不复用灾备目录里的 AstrBot 虚拟环境，也不构建旧的
`snowluma-local:v1.14.8` 镜像。目标机只安装 Docker，然后拉取两个
官方 `latest` 镜像并挂载恢复后的实例数据。

普通容器重启不会自动执行 `docker compose pull`；只有部署或显式更新
才会拉取 latest。因此 QQ/SnowLuma 更新能被及时纳入，而临时重启不会
意外换版本。

## 记录实际运行版本

每次 `deploy/up-latest.sh` 成功后都会运行
`deploy/record-image-digests.sh`，把镜像名、image ID 和 RepoDigest 写入：

```text
${PROJECT_ROOT}/runtime/deployed-images.env
```

这个文件不含凭据，记录的是本次 `latest` 实际解析到的不可变镜像版本。
它用于故障回退和排查，而不是阻止下一次显式更新继续跟随 latest。

升级顺序固定为：

1. 检查 secret、JSON、Compose、Docker 权限和剩余磁盘；
2. 记录当前镜像 ID，并在旧服务仍运行时显式拉取两个 latest 镜像；
3. 停止 AstrBot 和 SnowLuma，创建一致性 runtime 快照；
4. 只同步锁定插件和 secret，不从 private 快照覆盖实时数据库；
5. 启动新镜像，检查 AstrBot Dashboard、OneBot 403、SnowLuma WebUI 和 noVNC；
6. 检查失败时尝试重新标记旧镜像并重建容器，同时保留更新前 runtime 快照供人工恢复。

## 回退原则

如果 latest 与插件或 QQ 接入出现兼容问题，以最近一次成功部署生成的
`deployed-images.env` 为准，通过镜像 digest 启动上一版；修复后再回到
latest 更新路径。
