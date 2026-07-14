# Handoff：Agent Core 与 Tool Middleware 重构进展

## 1. 当前目标与已达成的设计共识

项目定位已经明确为**单 Agent 的 Core / Harness**，不是多 Agent 协作框架。

核心原则：

```text
Core 负责：状态机、执行顺序、数据契约、工具路由
扩展层负责：审批、审计、checkpoint、上下文增强等横切能力
```

当前不做多 Agent、Planner 或 RAG；先将单 Agent 的状态、上下文、工具调用和
扩展机制打牢。

## 2. 已完成的核心重构

### 2.1 AgentState

已新增 `src/simagentplg/agent/state.py`：

```python
class AgentStatus(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass(slots=True)
class AgentState:
    messages: list[AgentMessage]
    task: str | None
    status: AgentStatus
    turn: int
    no_tool_response_count: int
    active_skill_name: str | None
    result: str | None
    error: str | None
```

`AgentState` 不拆分 `TaskState`；它直接保存持久消息历史与当前/上一次任务的
状态。其方法包括：

- `reset()`
- `begin_task()`
- `advance_turn()`
- `add_message()` / `add_messages()`
- `complete()` / `fail()`
- `snapshot()`

注意：不要把 client、Handler、锁、路由表或 MCP 连接放入 State。

### 2.2 AgentContextBuilder

旧的 `agent/context.py` 已删除，替换为：

```text
src/simagentplg/agent/context_builder.py
```

核心类型是 `AgentContextBuilder` 和 `ContextBuildResult`。

`ContextBuildResult` 已不只是消息上下文，而是完整模型请求：

```python
@dataclass(frozen=True, slots=True)
class ContextBuildResult:
    agent_messages: tuple[AgentMessage, ...]
    llm_messages: tuple[AgentMessage, ...]
    tools: tuple[dict[str, Any], ...]
```

它的构建顺序：

```text
state.messages 的副本
  → 在开头 system messages 后插入 Skill 索引与激活 Skill
  → 添加本轮 transient messages
  → provider-compatible llm_messages
  → 复制结构化 tools
```

Skill 内容、工具完成重试提示等只进入本轮 `ContextBuildResult`，不会写回
`state.messages`。这避免了临时 system prompt 污染会话历史。

### 2.3 BaseAgent 运行链路

`BaseAgent` 当前链路：

```text
runtime(task)
  → operation_lock
  → startup（Skill discovery、Handler/ToolRuntime startup）
  → state.begin_task(task)
  → ToolRuntime.on_task_start()
  → ReAct loop
      → state.advance_turn()
      → ContextBuilder.build(state, tools, transient_messages)
      → chat_text(ContextBuildResult)
      → assistant message 写入 state.messages
      → 工具结果写入 state.messages
  → state.complete(result)

任务异常
  → state.fail(error)
  → 异常继续抛出
```

`chat_text()` 不再接收零散 `messages, tools` 参数，只接收完整
`ContextBuildResult`，再通过 OpenAI-compatible API 的原生 `tools=` 传递工具。

## 3. Tool Middleware：当前正在进行的重构

### 3.1 目录位置

Middleware 已从 `agent/middleware.py` 迁移到根级：

```text
src/simagentplg/middleware/
  __init__.py
  base.py
  approval.py
  bash_approval.py
```

这是正确的位置：Middleware 是整个 Core 的横切扩展，不应归属在 `agent/`
子目录。

### 3.2 已实现的装饰器链

当前使用**装饰器模式（Decorator Pattern）**，不是 Python 静态 `@` 语法。

`base.py` 当前定义：

```python
@dataclass(frozen=True, slots=True)
class ToolCallContext:
    state: AgentState
    tool_name: str
    arguments: dict[str, Any]
    tool_call_id: str | None = None

ToolNext = Callable[[ToolCallContext], Awaitable[StepOutcome]]

class ToolMiddleware(Middleware):
    async def __call__(
        self,
        context: ToolCallContext,
        call_next: ToolNext,
    ) -> StepOutcome:
        return await call_next(context)
```

`compose_tool_middlewares()` 按声明顺序构建链：

```text
middlewares=[first, second]

first.before
  → second.before
    → Handler.dispatch
  ← second.after
← first.after
```

`BashApprovalMiddleware` 已迁移为 `__call__(context, call_next)`：

- 非 `bash_run`：直接 `await call_next(context)`；
- 无需审批：直接继续；
- 用户拒绝：返回 `StepOutcome(..., should_exit=True)`，短路后续装饰器和 Handler；
- 用户同意：继续执行下一层。

### 3.3 ToolRuntime 当前行为

`ToolRuntime` 已改为：

1. 启动时固定筛选 `enabled=True` 的 ToolMiddleware；
2. 启动这些 Middleware，并用它们构建 `_tool_chain`；
3. `dispatch()` 创建 `ToolCallContext(state, tool_name, arguments, tool_call_id)`；
4. 调用 `_tool_chain(context)`；
5. terminal 最终调用对应 Handler。

这修复了旧实现的生命周期问题：以前 `_enabled_middlewares()` 每次动态筛选，
Middleware 在 startup 后被设置为 `enabled=False` 时，会在 shutdown 阶段被跳过。
现在关闭时始终逆序关闭实际启动过的 Middleware。

## 4. 为什么没有直接使用 Python `@` 语法

`@decorator` 在函数定义/模块导入时就把包装关系固定下来；而本项目的
Middleware 是每个 Agent 实例在运行时传入的：

```python
BaseAgent(
    ...,
    middlewares=[BashApprovalMiddleware(), AuditMiddleware()],
)
```

所以 Core 内部必须以运行时链组合。未来可添加 `@tool_decorator` 作为扩展作者
编写 Middleware 的语法糖，但它不应取代 `middlewares=[...]` 的运行时装配。

推荐保持：

```text
Core API：BaseAgent(..., middlewares=[...])
场景 Agent：子类在 __init__ 中预设 middleware 列表
@tool_decorator：未来可选的插件作者语法糖
```

## 5. Tool Middleware 当前仍存在的边界/待办

这一轮只改造了 Tool Middleware。不要误以为已经有完整 Agent Middleware。

### 待办 A：完整测试尚未在本轮装饰器改造后执行

本轮最后执行过：

```bash
python -m compileall -q src tests examples
git diff --check
```

均未报错。

但装饰器链改造后，`uv run python -m unittest` 的一次尝试被环境阻塞：

```text
Could not acquire lock
Read-only file system at /home/arch/.cache/uv/...
```

此前（根级 Middleware 目录迁移后、装饰器链改造前）完整套件为：

```text
Ran 70 tests
OK
```

装饰器改造新增了两项测试，因此回来后必须优先运行：

```bash
uv run python -m unittest
```

如果 uv cache 再次只读，需使用允许访问用户 uv cache 的执行权限，或配置可写的
`UV_CACHE_DIR`。

### 待办 B：检查 ToolMiddleware 装饰器测试

已修改 `tests/test_agent.py`：

- `RecordingToolMiddleware` 与 `ApprovalToolMiddleware` 改为 `__call__`；
- 新增嵌套顺序测试，期望：

  ```text
  first:before → second:before → second:after → first:after
  ```

- 新增生命周期测试：启动后的 Middleware 即使再设为 `enabled=False`，仍必须
  `shutdown()`；
- 断言来自模型的 tool call 能将 `tool_call_id` 和 `agent.state` 传给 Middleware。

若测试失败，优先检查：

- `compose_tool_middlewares()` 的闭包绑定；
- `ToolRuntime.startup()` 中 `_active_middlewares` 与 `_tool_chain` 的初始化；
- `ToolRuntime.shutdown()` 是否逆序关闭 `_active_middlewares`；
- `BashApprovalMiddleware` 是否所有放行路径都 `await call_next(context)`。

### 待办 C：下一阶段不要直接继续往 ToolRuntime 塞 Hook

ToolRuntime 现在只应负责：

```text
工具路由、参数解析、工具装饰器链、Handler 执行、工具结果序列化
```

后续需要单独设计 Agent / Model 层装饰器：

```text
TaskDecorator
  - on task start/end/error
  - checkpoint、TaskResult 丰富化

ModelDecorator
  - context budget、请求审计、模型重试、流式事件
```

当前已知限制：

1. Middleware 生命周期仍由 ToolRuntime 启动，因此 plain-chat / Skill-only Agent
   不会运行 ToolMiddleware。这对“工具 Middleware”是合理的；未来的
   TaskDecorator/ModelDecorator 必须由 BaseAgent 管理。
2. `ToolCallContext` 是 frozen dataclass，但 `arguments` 是可变 dict；如果后续
   需要严格不可变，可改为 `Mapping` + `MappingProxyType`，或规定参数改写必须
   创建新 Context。
3. Tool Middleware 只适用于 `ToolMiddleware`；不要再把通用 `Middleware` 实例
   混入 `BaseAgent(middlewares=...)`。当前 public type 已更新为
   `Iterable[ToolMiddleware]`。

## 6. 文件与工作区状态

本次未提交改动涉及：

```text
README.md
README_zh-CN.md
src/simagentplg/__init__.py
src/simagentplg/agent/__init__.py
src/simagentplg/agent/base.py
src/simagentplg/agent/tool_runtime.py
src/simagentplg/middleware/__init__.py
src/simagentplg/middleware/base.py
src/simagentplg/middleware/bash_approval.py
tests/test_agent.py
```

另请在开始前执行：

```bash
git status --short
git diff --check
```

确认没有新的外部改动与当前工作重叠。

## 7. 建议的续接顺序

1. 运行完整 unittest，先确保 ToolMiddleware 装饰器链测试通过；
2. 审阅 `ToolCallContext.arguments` 的可变性，决定是否允许参数转换；
3. 为 ToolDecorator 补一个异常观察/审计测试，确认外层 `try/finally` 能覆盖
   Handler 异常与内层短路；
4. 再设计 `ModelDecorator`，不要先实现；先确定它是围绕完整
   `ContextBuildResult` / ModelRequest 包装，还是拆分 context build 与 provider call；
5. 最后设计 TaskDecorator 和事件流。

当前最重要的原则：先把单 Agent 的 Core 执行边界和扩展链稳定下来，再扩展功能。
