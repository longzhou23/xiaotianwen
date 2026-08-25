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

1. 停止 AstrBot 和 SnowLuma；
2. 创建实例备份并记录当前版本；
3. 显式拉取两个官方 latest 镜像；
4. 恢复实例数据和插件锁；
5. 先做 AstrBot Dashboard、OneBot、SnowLuma WebUI 和消息链路检查；
6. 检查通过后再切换服务；失败则恢复旧环境和旧镜像标签。

## 回退原则

如果 latest 与插件或 QQ 接入出现兼容问题，以最近一次成功部署生成的
`deployed-images.env` 为准，通过镜像 digest 启动上一版；修复后再回到
latest 更新路径。
