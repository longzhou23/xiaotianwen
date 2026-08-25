# Iris Memory Plugin — 开发规范

请使用第一性原理思考。你不能总是假设我非常清楚自己想要什么和该怎么得到。请保持审慎，从原始需求和问题出发，如果动机和目标不清晰，停下来和我讨论。如果目标清晰但是路径不是最短，告诉我，并且建议更好的办法

Iris Memory 是一个面向 [AstrBot](https://github.com/Soulter/AstrBot) 的三层记忆插件，使用 Python 编写，集成 Chroma 向量数据库。

## 构建与测试

python虚拟环境在项目的.venv目录下，使用uv管理

```bash
# 安装依赖
uv pip install -r requirements.txt

# 运行全部测试
pytest

# 常用过滤选项
pytest -v                        # 详细输出
pytest -m asyncio                # 仅异步测试
pytest -m "not slow"             # 跳过慢速测试
pytest tests/modules/            # 指定目录
pytest --cov=iris_memory         # 覆盖率报告
```

所有异步测试用 `@pytest.mark.asyncio` 标注；全局 fixture 在 [tests/conftest.py](../tests/conftest.py) 中定义，每个测试函数前自动初始化配置并在结束后 `reset_store()`。

## 架构

### 入口与顶层结构

- **[main.py](../main.py)** — AstrBot 插件注册类 `IrisMemoryPlugin`，绑定所有 `@filter.command` 事件并委托给内部服务。
- **[iris_memory/services/memory_service.py](../iris_memory/services/memory_service.py)** — 核心门面（Facade），持有 8 个功能模块、`BusinessService`、`PersistenceService`、`SharedState`。
- **[iris_memory/services/initializer.py](../iris_memory/services/initializer.py)** — 三阶段初始化：核心组件（必须成功）→ 增强组件（可降级）→ 可选功能。

### 八大功能模块（`iris_memory/services/modules/`）

| 模块 | 职责 |
|------|------|
| `StorageModule` | Chroma 向量库、会话管理器 |
| `AnalysisModule` | 情感分析、RIF 评分、用户画像提取 |
| `LLMEnhancedModule` | LLM 驱动的检测器、路由、冲突解决 |
| `CaptureModule` | 消息捕获、分类、冲突检测 |
| `RetrievalModule` | 向量检索、重排序、上下文构建 |
| `ProactiveModule` | 信号生成、主动回复调度 |
| `KnowledgeGraphModule` | 实体提取、关系管理 |
| `CooldownModule` | 用户/群组速率限制 |

### 三层记忆模型

- **Working Memory**（工作记忆）：会话内 LRU 缓存
- **Episodic Memory**（情景记忆）：基于 RIF 评分动态管理，支持选择性遗忘
- **Semantic Memory**（语义记忆）：永久保存用户画像和核心特征

## 关键模式与规范

### 依赖注入

使用轻量级单例容器 [iris_memory/core/service_container.py](../iris_memory/core/service_container.py)：

```python
from iris_memory.core.service_container import ServiceContainer
container = ServiceContainer.instance()
container.register("my_service", my_service)
svc = container.get("my_service")
container.clear()  # 热重载时调用
```

不要在模块层级做隐式单例，组件应通过容器或构造函数传入。

### 组合优于继承

`MemoryService` 通过组合持有各子服务，不使用 Mixin。属性代理（`@property`）暴露内部组件，避免外部直接访问私有模块：

```python
@property
def chroma_manager(self): return self._deps.storage.chroma_manager
```

### 配置系统（双轨）

- **旧路径**：`ConfigManager`（直接文件 I/O），仅在遗留代码中使用
- **新路径**（推荐）：[iris_memory/config/store.py](../iris_memory/config/store.py) 中的 `ConfigStore`

```python
from iris_memory.config import get_store
cfg = get_store()
value = cfg.child("embedding").get("local_model")
```

新代码统一使用 `ConfigStore`；配置结构参见 [_conf_schema.json](../_conf_schema.json)。

### 共享状态（LRU）

[iris_memory/services/shared_state.py](../iris_memory/services/shared_state.py) 中的 `SharedState` 管理所有用户粒度的可变状态（情绪、画像、注入历史），默认上限 2000 条，LRU 淘汰。不要在模块内部保存用户级别的长期状态，应通过 `SharedState` 统一管理。

### 会话隔离键

用户会话以 `user_id + group_id` 为复合键，确保私聊与群聊完全隔离。多 Bot 人格时额外附加 persona_id。

## 测试规范

- 测试工具函数放在 [iris_memory/core/test_utils.py](../iris_memory/core/test_utils.py)
- 每个测试用 `setup_test_config()` + `reset_store()` 保持隔离（已在 `conftest.py` 的 `autouse` fixture 中处理）
- 异步测试：

```python
@pytest.mark.asyncio
async def test_something():
    result = await some_async_func()
    assert result is not None
```

- 慢速测试（如 Chroma、embedding 真实加载）标注 `@pytest.mark.slow`

## 常见陷阱

- **不要在初始化阶段之外直接实例化 `MemoryService`**——应通过 `ServiceInitializer` 走三阶段流程，否则模块依赖可能未就绪。
- **ChromaDB 集合名称**在同一进程内不能重复，测试中要使用唯一名称或在 teardown 阶段删除集合。
- **LLM 调用路径**默认在 `use_llm=False` 时走规则路径，测试时应保持 `use_llm: false` 以避免实际 API 调用。
- **Web UI** 运行在独立端口（默认 6185），与主插件进程共享同一 `ServiceContainer` 单例。
