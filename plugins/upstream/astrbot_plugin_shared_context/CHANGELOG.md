# 更新日志

## 0.2.2

- 新增**缓存优化**（激进模式，默认关闭，`enable_cache_optimization`）：修复开启共享后服务端前缀缓存命中率大幅下降的问题
  - `incremental_injection`：增量注入，会话首次请求注入全量，之后只注入新增记录 + 最近 `keep_recent` 条保底
  - `cache_ratio`：注入块大小受可缓存上下文体积比约束，保证命中率下限
  - `include_timestamps`：可单独关闭记录行时间戳，进一步压缩注入量
- 消息记录增加自增序号（`seq`），增量注入依赖其判定新增；老数据无 `seq` 时按全量注入处理，无需迁移

## 0.2.1

- 新增 `file_component_mode`：消息中非文本组件（图片/文件/语音等）的处理方式（ignore / placeholder / caption / full）
- `caption` 与 `full` 模式支持图片 AI 转述：`caption_prompt`（与 AstrBot 内置一致）、`caption_use_multimodal` 多模转述开关、`caption_text_provider_id`（纯文本转述模型，始终启用）、`caption_multimodal_provider_id`（多模态转述模型，仅转述图片内容）；提供商选择使用 WebUI 选择器
- `full` 模式：文件读取文本内容转发（`max_file_chars` 独立上限，默认 2000），图片同样转述，整行不再受单条消息截断限制
- 所有文件转述设置归入「文件与图片转述」设置组
- 更新描述：跨机器人共享为可选项而非禁止

## 0.2.0

- 新增**跨机器人共享**（`cross_bot_share`，默认关闭，开启后仅显式分组的会话互通）
- 新增 `out_of_group_mode`：组外会话的处理方式（isolate 完全独立 / bot 同机器人组外互见 / global 全机器组外互见，组内只见组内、组外只见组外，互不可见）
- 组条目支持通配符：`*`（本机器人全部会话）、`bot:*`（指定机器人全部会话）
- `share_groups` 改为 JSON 编辑器（键为组名、值为 umo 数组），兼容旧格式
- 完善 debug 日志：记录/会话命中/各池贡献条数可观测
- 规避 AstrBot `check_config_integrity` 清空 dict 类型配置内容的缺陷（改用 text 存储）

## 0.1.1

- 新增插件 Logo（`logo.png`）
- README 补充：本插件不修改任何会话的上下文

## 0.1.0

- 首个版本：同一机器人的不同会话共享 LLM 上下文
- 支持多共享组、时间窗与长度上限
- 跨机器人（self_id）上下文永不共享
- 消息流水 KV 持久化，重载不丢失
