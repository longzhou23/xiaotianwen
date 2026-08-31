# P1 分批测试说明

这些测试只覆盖 P1 的纯协议和影子逻辑，不连接 AstrBot、SnowLuma、QQ、Iris 数据库、模型 Provider 或网络；当前完整集合为 34 项。

| 批次 | 文件 | 覆盖内容 | 不代表 |
|---|---|---|---|
| A | `test_contracts.py` | 序列化、非法输入、route 白名单、保守工具策略 | AstrBot 插件加载成功 |
| B | `test_turn_coordinator.py` | OneBot 归一化、TTL 去重、3 秒窗口、取消记录、旧路径结构对比 | 实际防抖已接管或实际请求被取消 |
| C | `test_context_assembler.py` | 只读 snapshots、排序、预算、去重、Decision 精简、脱敏 diff | 线上 Iris/ContextAware 已切换或缓存指标提升 |
| D | `test_media_registry.py` | ImageContextPool 映射、ID/order、图片引用、标注 artifact、metadata-only、VLM key | 真实图片文件、VLM 或 URL 恢复 |
| E | `test_ownership_delivery.py`、`test_memory_route_plan.py` | 主回复所有权、幂等 delivery、memory single-flight、route 策略、请求 context memo | 线上 hook priority、真实 provider 调用或 24 小时指标 |

推荐按 A → B → C → D → E 顺序运行。全部通过后，下一步应先导出不含正文的真实回放结构，再进行 24 小时 shadow gate；不要直接把本目录加入生产插件锁或切换主回复入口。
