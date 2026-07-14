# SimAgentPlg 与 Pi Agent Harness 能力对照

> 更新日期：2026-07-15
> 对照范围：`pi/packages/agent`、`pi/packages/coding-agent` 与 SimAgentPlg 当前实现

## 1. 当前结论

SimAgentPlg 已完成通用 Agent Core 的第一阶段：一个边界明确、可以运行和扩展的
“执行内核”。目前已经具备稳定的模型—工具循环、结构化运行结果、工具运行时、
Provider 适配层、MCP 适配和 Skill 上下文资源。

它还不是完整的 Agent Harness。与 Pi 相比，主要缺少的是执行内核之上的控制面：

- 统一事件协议与 Hook
- 流式模型输出和工具进度
- 外部取消与等待空闲
- Session 持久化、恢复和分支
- Steering、Follow-up 等消息队列
- Context Budget 与 Compaction
- ExecutionEnv 与 Workspace 抽象

因此，当前 `AgentOrchestrator` 对应 Pi 的 Agent Loop；`BaseAgent` 已具备 Harness
组装根的雏形，但尚未承担完整 Harness 控制面。

## 2. 当前架构

```text
BaseAgent
  ├── ModelAdapter
  │     └── OpenAIModelAdapter
  ├── AgentOrchestrator
  ├── AgentState
  ├── AgentContextBuilder
  ├── ToolRuntime
  │     ├── BaseHandler / MethodToolHandler
  │     ├── McpToolHandler
  │     └── ToolMiddleware
  └── SkillManager
```

### 2.1 组件职责

| 组件 | 当前职责 |
|---|---|
| `BaseAgent` | 依赖组装、外部调用串行化、资源生命周期、兼容 API |
| `AgentOrchestrator` | 执行模型—工具循环并生成结构化终止结果 |
| `AgentState` | 保存消息、任务状态、Turn、结果和 Active Skill |
| `AgentContextBuilder` | 投影每轮上下文，注入 Skill 和临时控制消息 |
| `ModelAdapter` | 隔离 Provider Client、请求调用和响应归一化 |
| `ToolRuntime` | 工具生命周期、路由、Middleware、执行与重复调用保护 |
| `BaseHandler` | 一组可执行工具的最小协议 |
| `McpToolHandler` | 将 MCP Tool 转换为统一 Handler 工具 |
| `SkillManager` | Skill 发现、metadata、显式选择和上下文投影 |
| `RuntimePolicy` | 最大步数、无工具响应、重复调用和显式完成策略 |
| `AgentRunResult` | 描述运行状态、停止原因、轮数、输出和错误 |

### 2.2 Runtime 主链路

```text
BaseAgent.run(task)
  → startup ModelAdapter + ToolRuntime
  → AgentOrchestrator.run(task)
  → AgentState.begin_task(task)
  → AgentContextBuilder.build(...)
  → ModelAdapter.complete(context)
  → AssistantMessage
  → ToolRuntime.execute_tool_call(...)
  → AgentRunResult
```

`BaseAgent.runtime()` 是兼容包装：成功时返回文本输出，失败、拒绝或取消时抛出
`AgentRunError`。

## 3. 已完成的 Core 能力

### 3.1 稳定的运行与终止语义

`RuntimePolicy` 已集中管理：

- `max_steps`
- `max_no_tool_responses`
- `max_repeated_tool_calls`
- `require_explicit_finish`

`AgentRunResult` 与 `StopReason` 已能区分：

- 普通文本完成
- 工具显式完成
- 工具拒绝
- 工具取消
- 空响应
- 最大步数
- 连续无工具响应
- 重复工具调用
- Runtime 错误

工具是否存在与是否必须显式完成已经解耦。工具通过 `ToolControl` 返回
`CONTINUE`、`COMPLETE`、`REJECT` 或 `CANCEL`，不再使用一个布尔值混合多种终止
含义。

### 3.2 Provider 适配层

Core 不再直接持有 OpenAI Client。`BaseAgent` 只依赖 `ModelAdapter`：

```text
ContextBuildResult
  → ModelAdapter.complete()
  → AssistantMessage
      └── ModelToolCall[]
```

当前提供 `OpenAIModelAdapter` 和对应的 `ModelConfig`。Adapter 负责 Client
创建、可选 Client 注入、关闭以及 Provider 响应归一化。

目前只实现了 OpenAI-compatible Adapter；Provider 边界已经存在，但多 Provider
能力仍需通过第二个真实 Adapter 验证。

### 3.3 统一 ToolRuntime

所有模型 Tool Call 都进入 `ToolRuntime`。`AgentOrchestrator` 不再识别具体工具名，
也不再接收 `has_handler_tools`。

`ToolRuntime` 已具备：

- Handler 生命周期和确定性路由
- 重复工具名检查
- JSON 参数解析
- Tool Middleware 组合
- 工具异常转换为标准 Tool Message
- 重复调用检测
- 结构化控制信号
- 空工具运行时支持

Middleware 仅在启动后实际存在工具路由时激活。

### 3.4 Skill 是上下文资源

Skill 不属于 ToolRuntime，也不注册内部 `load_skill` 工具。

```text
SkillManager.discover()
  → name + description + absolute location
  → AgentContextBuilder 注入 metadata
```

当前支持两种使用方式：

1. 用户通过 `$skill_name` 或 `skill:skill_name` 显式选择，完整 Skill 指令直接注入
   当前模型上下文。
2. 未来 CodeAgent 拥有 `read` 工具后，模型可以根据 metadata 中的 `location`
   渐进读取 `SKILL.md`。

这种设计保留了无文件工具 Agent 的显式 Skill 能力，同时不会让 Orchestrator
出现 Skill Tool 特殊分支。

### 3.5 MCP 是工具适配器

MCP 通过 `McpToolHandler` 接入统一 Handler 协议：

```text
MCP Server
  → McpServerManager
  → McpToolHandler
  → ToolRuntime
```

Orchestrator 不知道工具是否来自 MCP。MCP Agent 可以执行工具后用普通文本完成，
除非 `RuntimePolicy.require_explicit_finish=True`。

Core 不携带默认 MCP Server 配置或默认 Skill。`McpToolHandler`、`McpServerManager`
和 `SkillManager` 都要求调用方显式提供配置路径，确保未配置能力时不会产生隐藏行为。

## 4. 与 Pi 的关键差异

### 4.1 Pi 的分层

```text
Agent Loop
  ↓
Agent Harness
  ├── Session
  ├── Events / Hooks
  ├── Abort / Queues
  ├── Compaction
  ├── ExecutionEnv
  ├── Runtime configuration
  └── Skills / Prompt Templates
        ↓
Coding Agent
  ├── File / Shell / Git Tools
  ├── Extensions
  ├── Trust / Workspace
  ├── TUI / Print / RPC
  └── Project resources
```

Pi 的 Harness 已经是完整控制面；SimAgentPlg 当前主要完成了 Agent Loop 和
Harness 的组装骨架。

### 4.2 Pi 的 Skill

Pi 将 Skill 定义为 Harness Resource，而不是 Tool：

- Harness 保存 `Skill[]`
- System Prompt 注入名称、描述和文件位置
- Coding Agent 使用普通 `read` 工具加载完整文件
- 用户可通过 `harness.skill()` 或 `/skill:name` 显式调用
- Agent Loop 不识别 `load_skill`

SimAgentPlg 采用相同的“Skill 是资源”边界，但保留 `$skill_name` 和
`skill:skill_name` 作为轻量显式选择语法。

### 4.3 Pi 的 MCP

Pi Coding Agent 明确不内置 MCP。MCP 通常由 Extension 或 Package 建立 Client，
再通过 `registerTool()` 注册为普通工具。

SimAgentPlg 选择提供可选的 `McpToolHandler`，但执行边界相同：MCP 必须先适配成
通用工具，Agent Loop 不感知 MCP 协议。

### 4.4 工具集合

Pi Harness 维护通用 Tools Map 和 Active Tool Names，并支持运行时切换。当前
SimAgentPlg 的工具集合主要由构造时 Handler 决定，MCP Schema 在启动时加载，尚无
公开的动态启用、禁用或替换工具协议。

## 5. 当前能力矩阵

| Harness 能力 | 当前实现 | 状态 |
|---|---|---|
| Agent Loop | `AgentOrchestrator` | 已具备 |
| 结构化终止 | `AgentRunResult`、`ToolControl` | 已具备 |
| Runtime Policy | `RuntimePolicy` | 已具备 |
| Provider 边界 | `ModelAdapter` | 已具备，待多 Provider 验证 |
| Tool Runtime | `ToolRuntime` + Handler | 已具备 |
| Tool Middleware | `ToolMiddleware` | 基础版 |
| MCP | `McpToolHandler` | 已具备，可选 |
| Skill Resource | `SkillManager` + ContextBuilder | 已具备 |
| Context Projection | `AgentContextBuilder` | 基础版 |
| Event Stream | 无 | 未实现 |
| Hook Protocol | Tool Middleware only | 部分具备 |
| Streaming Output | 无 | 未实现 |
| External Abort | 无；仅有 Tool `CANCEL` | 未实现 |
| Wait for Idle | 无 | 未实现 |
| Session Storage | 无 | 未实现 |
| Resume / Fork / Tree | 无 | 未实现 |
| Steering / Follow-up | 无 | 未实现 |
| Context Compaction | 无 | 未实现 |
| Token / Usage Budget | 无 | 未实现 |
| Parallel Tool Calls | 当前顺序执行 | 未实现 |
| Tool Progress | 无 | 未实现 |
| Dynamic Tool Set | 构造时为主 | 未实现 |
| ExecutionEnv | 无 | 未实现 |
| CodeAgent Tools | 不属于 Core | 待派生 Agent 实现 |

## 6. 仍需注意的 Core 边界

### 6.1 Context 类型仍是过渡设计

`ContextBuildResult` 同时保留：

```python
agent_messages: tuple[AgentMessage, ...]
llm_messages: tuple[AgentMessage, ...]
```

默认实现中两者内容相同，但为 Context Transform 预留了投影阶段。当前暂时保留，
等接入第二种 Provider、内部事件消息或 Compaction 后再决定是否合并为统一的
`ModelContext.messages`。

### 6.2 Tool Schema 仍偏 OpenAI-compatible

`ModelAdapter` 已隔离 Provider Client 和响应类型，但 Handler 暴露的 Tool Schema
仍使用 OpenAI function-calling 字典形状。未来接入非兼容 Provider 时，需要决定：

- 将当前形状定义为 Core Canonical Tool Schema，由 Adapter 转换；或
- 新增强类型的 `ToolDefinition`，彻底移除 Provider 风格字段。

这不阻塞当前 Harness 建设，但应在第二个 Provider Adapter 前解决。

### 6.3 Skill 显式选择仍由 Orchestrator 激活

Orchestrator 已不执行 Skill Tool，但 `_activate_explicit_skill()` 仍属于
Skill-specific 任务准备逻辑。事件与 Hook 协议建立后，可将它迁移到通用的
`before_task` 或 Context Transform Hook。

## 7. 后续建设顺序

### 阶段一：稳定执行内核——已完成

- `RuntimePolicy`
- `AgentRunResult`
- `ToolControl`
- `AgentOrchestrator`
- `ToolRuntime`
- Core 与 CodeAgent 工具边界拆分
- `ModelAdapter`
- Skill Resource 化
- 移除 `has_handler_tools`

### 阶段二：统一事件协议——下一步

建议新增 `agent/events.py`，第一版只提供不可变事件数据：

- `AgentStarted`
- `TurnStarted`
- `MessageCompleted`
- `ToolStarted`
- `ToolCompleted`
- `TurnCompleted`
- `AgentCompleted`
- `AgentFailed`

同时定义最小发布接口：

```python
class AgentEventSink(Protocol):
    async def emit(self, event: AgentEvent) -> None: ...
```

第一版只做观察事件，不允许 Hook 修改行为。这样可以先稳定事件顺序和错误语义，
避免事件系统一开始就同时承担拦截器职责。

### 阶段三：Session

```text
Session
SessionStorage
MemorySessionStorage
JsonlSessionStorage
```

先实现线性 Session、保存和恢复，再考虑 Fork、Tree 和 Branch Summary。

### 阶段四：取消与流式输出

- Cancellation Token
- `abort()`
- `wait_for_idle()`
- Provider Stream Adapter
- Text / Thinking Delta
- Tool Progress Event
- 取消向 Tool 和 subprocess 传播

### 阶段五：上下文管理

- Token Usage
- Context Budget
- Compaction Policy
- Summary Entry
- Context Transform Hook

### 阶段六：ExecutionEnv 与 CodeAgent

```text
CodeAgent
  ├── ExecutionEnv / Workspace
  ├── Read / Write / Edit
  ├── Grep / Find / List
  ├── Bash / Git
  ├── Sandbox / Approval Policy
  ├── Completion Tool（可选策略）
  └── CLI / RPC / TUI Adapter
```

## 8. 下一步任务建议

下一步应实现“只读事件协议”，暂时不增加 Session、流式输出或 CodeAgent 文件工具。

推荐第一轮改动范围：

```text
src/simagentplg/agent/events.py
src/simagentplg/agent/orchestrator.py
src/simagentplg/agent/tool_runtime.py
tests/test_agent_events.py
```

验收标准：

1. 普通文本完成产生确定的 Agent、Turn 和 Message 事件顺序。
2. Tool Call 产生 Tool Started/Completed 事件。
3. 失败、拒绝和取消产生不同终止事件或终止 payload。
4. Event Sink 异常策略明确，不得让观察者意外改变 Agent 运行结果。
5. Core 不依赖 CLI、UI、Session 或具体日志实现。
