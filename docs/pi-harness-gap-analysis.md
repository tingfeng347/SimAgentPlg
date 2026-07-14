# SimAgentPlg 与 Pi Agent Harness 能力对照分析

> 分析日期：2026-07-14  
> 对照范围：`pi/packages/agent`、`pi/packages/coding-agent` 与 SimAgentPlg 当前实现

> 实施更新（2026-07-15）：本文建议的第一阶段已经完成。Core 已加入
> `RuntimePolicy`、`AgentRunResult`、`RunStatus`、`StopReason` 和
> `ToolControl`；工具可用性与显式完成策略已经解耦；Bash、GitDiff、Finish
> 以及具体审批实现已移出 Core。下文第 3—6 节保留为改造前的分析快照，当前公共
> API 以项目 README 为准。
>
> 后续更新（2026-07-15）：Core 已增加 `ModelAdapter` Provider 边界；Skill
> 定位为上下文资源，不再注册内部 `load_skill` 工具；Orchestrator 也不再接收
> `has_handler_tools`，所有模型工具调用统一进入 `ToolRuntime`。

## 1. 结论

SimAgentPlg 已经实现了一个最小可用的 Agent Runtime，具备模型—工具运行循环、状态管理、上下文构建、工具路由、中间件、Skill 和 MCP 等基础能力。

但按照 Pi 的分层定义，SimAgentPlg 目前完成的是 Harness 的“执行内核”，尚未形成完整的“控制面”。主要缺失包括：统一事件协议、流式输出、运行取消、消息队列、Session 持久化、上下文压缩、运行环境抽象和 Provider 抽象。

当前的 `AgentOrchestrator` 更接近 Pi 的 `agent-loop.ts`，而不是完整的 `AgentHarness`。

## 2. Pi 的 Harness 分层

Pi 没有把所有能力都放进一个 Agent 类，而是分为三个主要层次：

```text
Agent Loop
    ↓
Agent Harness
    ├── Session
    ├── ExecutionEnv
    ├── Events / Hooks
    ├── Queues / Abort
    ├── Compaction
    └── Runtime configuration
            ↓
Coding Agent
    ├── File tools
    ├── Shell tools
    ├── Extension system
    ├── TUI / RPC
    └── Project resources
```

### 2.1 Agent Loop

`pi/packages/agent/src/agent-loop.ts` 负责最底层的模型—工具循环：

- 将 AgentMessage 转换为 LLM Message
- 流式调用模型
- 识别和执行工具调用
- 顺序或并行执行工具
- 处理 steering 与 follow-up 消息
- 发出细粒度运行事件
- 根据工具结果、模型停止原因或外部策略结束循环

### 2.2 Agent Harness

`pi/packages/agent/src/harness/agent-harness.ts` 位于 Agent Loop 之上，负责：

- Session 和消息持久化
- 运行阶段与并发保护
- 事件订阅和可修改行为的 Hook
- 模型、Thinking Level 和工具的动态切换
- Steering、Follow-up、Next-turn 队列
- Abort 与 Wait-for-idle
- Context Compaction
- Session Tree 与 Branch Summary
- Skill 和 Prompt Template 显式调用
- Provider 请求参数及生命周期 Hook

### 2.3 Coding Agent

`pi/packages/coding-agent` 在 Harness 之上提供具体应用能力：

- read、write、edit、grep、find、ls、bash 等工具
- Extension 加载和运行系统
- 项目配置与信任策略
- Session 管理界面
- TUI、Print、RPC 等交互模式
- 模型注册、认证和 Provider 配置

这说明文件工具、CLI 和 UI 不属于通用 Agent Core，而属于基于 Harness 构建的具体 Agent 产品层。

## 3. SimAgentPlg 当前分层

```text
BaseAgent
    ├── AgentOrchestrator
    ├── AgentState
    ├── AgentContextBuilder
    ├── ToolRuntime
    │     ├── Handler
    │     └── Middleware
    ├── SkillManager
    └── OpenAI-compatible client
```

主要组件职责如下：

| 组件 | 当前职责 |
|---|---|
| `BaseAgent` | 组装依赖、管理资源生命周期、串行化外部调用、提供兼容 API |
| `AgentOrchestrator` | 准备任务并执行模型—工具循环 |
| `AgentState` | 保存历史消息和当前任务状态 |
| `AgentContextBuilder` | 构造每轮模型上下文并注入 Skill/临时消息 |
| `ToolRuntime` | 工具注册、路由、中间件、执行和错误包装 |
| `BaseHandler` | 可复用工具组接口 |
| `ToolMiddleware` | 工具调用装饰与审批扩展点 |
| `SkillManager` | Skill 发现、索引、显式选择和上下文投影 |

## 4. 能力映射矩阵

| Pi 能力 | SimAgentPlg 对应实现 | 状态 |
|---|---|---|
| Agent Loop | `AgentOrchestrator` | 基本具备 |
| Agent State | `AgentState` | 基础版 |
| Tool Runtime | `ToolRuntime` + Handler | 基本具备 |
| Tool Hooks | `ToolMiddleware` | 部分具备 |
| Context Conversion | `AgentContextBuilder` | 部分具备 |
| Agent Harness | `BaseAgent` + `AgentOrchestrator` | 仅有骨架 |
| Skill | `SkillManager` | 基本具备 |
| Prompt Template | Skill 内可选模板 | 未形成独立资源协议 |
| MCP 扩展 | `McpToolHandler` | 已具备 |
| Approval | `BashApprovalMiddleware` | 基础版 |
| Event Stream | 无 | 未实现 |
| Streaming Model Output | 无 | 未实现 |
| Session Repository | 无 | 未实现 |
| JSONL/Memory Storage | 无 | 未实现 |
| Session Resume/Fork/Tree | 无 | 未实现 |
| Context Compaction | 无 | 未实现 |
| Steering/Follow-up | 无 | 未实现 |
| Abort/Wait-for-idle | 无 | 未实现 |
| ExecutionEnv | 零散的 `cwd` 和 subprocess | 未抽象 |
| Multi-provider Model Runtime | OpenAI-compatible client | 部分具备 |
| Parallel Tool Calls | 无 | 未实现 |
| Tool Progress Stream | 无 | 未实现 |
| Dynamic Model/Tool Configuration | 构造时静态配置 | 未实现 |
| Coding Agent File Tools | Bash、GitDiff | 少量具备 |
| Extension System | Handler/Middleware/MCP/Skill | 早期雏形 |

## 5. 已经实现的 Harness 能力

### 5.1 模型—工具运行循环

`AgentOrchestrator` 已经实现：

- 创建并初始化任务
- 为每轮调用构造上下文
- 调用模型并保存 Assistant Message
- 执行一个 Assistant Message 中的多个 Tool Call
- 保存 Tool Result
- 根据工具结果判断任务结束
- 限制最大轮数
- 检测连续无工具响应
- 将异常写入 AgentState

这是 Harness 最底层的执行发动机。

### 5.2 工具运行时

`ToolRuntime` 已经实现：

- Handler 和 Middleware 生命周期
- 工具 Schema 收集
- 工具名到 Handler 的确定性路由
- 重复工具名检查
- JSON 参数解析
- 工具异常转换为 Tool Message
- Middleware 组合链
- 重复工具调用检测
- 工具主动终止任务

这是当前最完整、最接近通用 Harness 组件的一部分。

### 5.3 上下文投影

`AgentContextBuilder` 已经区分：

- Agent 持久历史
- Provider 消息
- 临时运行控制消息
- Skill 索引和完整 Skill 内容
- Tool Schema

它与 Pi 的以下模型方向一致：

```text
AgentMessage[]
  → transformContext()
  → convertToLlm()
  → Provider Messages
```

目前默认转换仍是复制消息，尚未形成通用 Transform/Hook 管道。

### 5.4 状态与任务生命周期

`AgentState` 已记录：

- 消息历史
- 当前任务
- `idle/running/completed/failed` 状态
- 当前 Turn
- 连续无工具响应次数
- Active Skill
- Result 和 Error

`snapshot()` 可以生成独立副本，适合观察或未来接入持久化。

### 5.5 扩展机制雏形

当前已经存在四种扩展原语：

- Handler：添加工具能力
- Middleware：拦截和装饰工具执行
- MCP：加载外部工具服务
- Skill：加载专用提示词与资源

因此 SimAgentPlg 已具备从通用 Core 派生 CodeAgent 的基本可组合性。

## 6. 尚未实现或只部分实现的能力

### 6.1 Event Stream 与 Hook 协议

Pi 会发出以下运行事件：

- `agent_start` / `agent_end`
- `turn_start` / `turn_end`
- `message_start` / `message_update` / `message_end`
- `tool_execution_start` / `tool_execution_update` / `tool_execution_end`
- `save_point`
- `settled`
- `queue_update`

同时提供可以改变行为的 Hook：

- `before_agent_start`
- `context`
- `before_provider_request`
- `before_provider_payload`
- `tool_call`
- `tool_result`
- `session_before_compact`
- `session_before_tree`

SimAgentPlg 目前只有日志与 ToolMiddleware。缺少统一事件协议会使 CLI、UI、RPC、存储和遥测不得不侵入 Orchestrator。

### 6.2 流式模型输出

当前 `chat_text()` 等待完整 ChatCompletion，缺少：

- Text Delta
- Thinking Delta
- Tool Call 参数流
- Partial Assistant Message
- 首 Token 延迟
- 流中断和取消

这会直接限制未来 CodeAgent 的交互体验和可观测性。

### 6.3 Session 与持久化

当前消息仅保存在进程内的平面数组中，缺少：

- `Session` 领域对象
- `SessionStorage` 接口
- Memory/JSONL Storage
- Session Repository
- Create/Open/List/Delete
- Resume 和 Fork
- 消息树和 Branch
- Label 与 Save Point

`AgentState.snapshot()` 只是内存复制，不等于可恢复的运行持久化。

### 6.4 Context Compaction

消息历史目前会持续增长，缺少：

- Token 估算
- Context Window 检测
- 最近消息保留策略
- 历史摘要
- 文件操作摘要
- Compaction Entry
- Compaction 前后 Hook

长时间运行的 CodeAgent 会很快受到上下文长度限制。

### 6.5 Abort、Steering 与 Follow-up

`BaseAgent` 的 `_operation_lock` 只能保证调用串行，不能控制正在执行的任务。

缺少：

- `abort()`
- `wait_for_idle()`
- `steer()`
- `follow_up()`
- `next_turn()`
- 队列消费模式
- Provider 和 subprocess 取消传播

### 6.6 ExecutionEnv 抽象

Pi Harness 依赖统一的：

```text
ExecutionEnv
  ├── FileSystem
  └── Shell
```

SimAgentPlg 的 `BashHandler` 和 `GitDiffHandler` 直接调用本地 subprocess，并分别维护 `cwd`。因此当前工具只能自然地运行在本机环境，难以无侵入切换到容器、SSH、远程代理或测试环境。

### 6.7 Provider/Model Adapter

当前 Runtime 直接依赖：

- `AsyncOpenAI`
- `ChatCompletionMessage`
- `message.model_dump()`
- OpenAI Tool Schema

这意味着可以连接 OpenAI-compatible 服务，但 Core 本身并非 Provider-neutral。

后续需要统一抽象：

- 内部 AssistantMessage
- ModelAdapter
- Stop Reason
- Usage 和 Cost
- Thinking Level
- Provider Retry
- Dynamic API Key
- Timeout 和 Transport 策略

### 6.8 工具执行协议

相较 Pi，当前工具协议缺少：

- JSON Schema 参数验证
- 并行工具执行
- 每工具顺序/并行策略
- 工具进度流
- Cancellation Token
- 图片和多段内容结果
- 独立的 `is_error`
- Batch 终止语义
- Tool Result 后处理协议

此外，`StepOutcome.should_exit` 同时承担完成、拒绝和停止等不同语义。例如人工审批拒绝会以 `should_exit=True` 结束任务，并被 AgentState 标记为 Completed。这需要通过结构化终止原因修正。

### 6.9 Runtime Policy

当前以下策略分散在多个类和常量中：

- 最大 Turn 数
- 最大连续无工具响应次数
- 最大重复工具调用次数
- 是否必须调用 Finish Tool
- 普通文本是否允许结束任务

特别是当前隐含了：

```text
has_handler_tools == require_explicit_finish
```

“存在可执行工具”和“必须通过 Finish Tool 结束”应是两个相互独立的概念。它们应由 RuntimePolicy 显式控制。

### 6.10 CodeAgent 应用层

未来 CodeAgent 还需要：

- Read/Write/Edit 文件工具
- Grep/Find/List 工具
- 文件修改串行队列
- Workspace 边界
- Shell 完整输出落盘和截断提示
- Git Diff 与 Artifact Collector
- Sandbox/Approval Policy
- Extension Loader
- Project Instructions
- Prompt Templates
- CLI、RPC 或 TUI Adapter

这些能力应建立在通用 Harness 之上，不应全部放回 `BaseAgent`。

## 7. 推荐建设顺序

### 阶段一：稳定运行语义

新增 `RuntimePolicy`：

```python
@dataclass(frozen=True, slots=True)
class RuntimePolicy:
    max_steps: int = 20
    max_no_tool_responses: int = 3
    max_repeated_tool_calls: int = 3
    require_explicit_finish: bool = False
```

新增结构化 `AgentRunResult`：

```python
@dataclass(frozen=True, slots=True)
class AgentRunResult:
    output: str | None
    status: RunStatus
    stop_reason: StopReason
    turns: int
    error: str | None = None
```

终止原因至少应区分：

- `text_response`
- `finish_tool`
- `rejected`
- `cancelled`
- `max_steps`
- `max_no_tool_responses`
- `repeated_tool_call`
- `model_error`

`BaseAgent.runtime() -> str | None` 可以作为兼容包装继续保留。

### 阶段二：建立事件协议

新增 `agent/events.py`，首先支持：

- `AgentStarted`
- `TurnStarted`
- `MessageCompleted`
- `ToolStarted`
- `ToolCompleted`
- `TurnCompleted`
- `AgentCompleted`
- `AgentFailed`

Orchestrator 只负责发布事件，不直接依赖 CLI、UI 或持久化实现。

### 阶段三：引入 Session

定义：

```text
Session
SessionStorage
MemorySessionStorage
JsonlSessionStorage
```

第一版只实现线性 Session 和恢复运行，不必立即实现完整消息树。稳定后再增加 Fork、Tree Navigation 和 Branch Summary。

### 阶段四：取消与流式输出

- Cancellation Token
- `abort()`
- `wait_for_idle()`
- Provider Stream Adapter
- Tool Progress Event
- subprocess 取消传播

### 阶段五：上下文管理

- Token Usage
- Context Budget
- Compaction Policy
- Summary Entry
- Context Transform Hook

### 阶段六：ExecutionEnv 与 CodeAgent

```text
CodeAgent
  ├── LocalExecutionEnv
  ├── Workspace
  ├── File Handlers
  ├── Bash Handler
  ├── Git Handler
  └── Approval Policy
```

## 8. 下一步建议

下一步优先实现 `RuntimePolicy + AgentRunResult`，暂时不要直接增加更多 CodeAgent 文件工具。

原因是事件、Session、取消、Compaction 和 CodeAgent 都依赖稳定的运行终止协议。如果继续使用 `str + should_exit`，后续模块会分别解释“任务为什么结束”，最终产生重复逻辑和语义冲突。

建议第一轮改动限制在：

```text
src/simagentplg/agent/runtime_policy.py
src/simagentplg/agent/result.py
src/simagentplg/agent/orchestrator.py
```

由 `AgentOrchestrator.run()` 返回结构化 `AgentRunResult`，再由 `BaseAgent.runtime()` 提供原字符串 API 的兼容层。

## 9. 总结

SimAgentPlg 已经完成了 Harness 的执行内核：

```text
Loop + State + Context + Tool Runtime + Lifecycle + Extension Primitives
```

接下来需要补齐 Harness 的控制面：

```text
Policy + Result + Events + Session + Cancellation
+ Persistence + Compaction + Execution Environment
```

完成这些能力后，SimAgentPlg 才能作为稳定 Core 派生 CodeAgent、ResearchAgent 或其他长时间运行、可恢复、可观察的 Agent 产品。
