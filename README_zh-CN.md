# SimAgentPlg

[English](README.md) | [简体中文](README_zh-CN.md)

SimAgentPlg 是一个用于构建有状态、可扩展 Agent 的轻量级 Core，面向
OpenAI-compatible 模型 API。Core 提供状态、编排、上下文、工具调度、
Middleware、MCP 和 Skill 等运行机制；Shell、文件编辑、Git、审批界面和完成工具
由具体派生 Agent 自行实现。

需要 Python 3.12 或更高版本。

## 核心能力

- 有状态的 `BaseAgent`，支持持久对话历史和 `reset()`
- Provider 无关的 `ModelAdapter` 边界，以及 OpenAI-compatible 适配器
- 公开的 `AgentOrchestrator`，负责模型—工具运行循环
- 结构化的 `AgentRunResult`、`RunStatus` 和 `StopReason`
- 显式的 `RuntimePolicy`，控制循环和完成策略
- `AgentContextBuilder`，构造不修改历史的每轮上下文
- 可组合的 `BaseHandler` 和 `MethodToolHandler` 工具协议
- `ToolRuntime` 生命周期、路由、Middleware 和重复调用保护
- 通用 `ToolMiddleware` 拦截机制
- Provider-neutral Token Usage 与单次 Run 预算保护
- 上下文压力估算、独立窗口预算和非变异压缩准备
- 通过可插拔 `Compactor`、标准 `SummaryEntry` 和 Session 快照提供显式可取消压缩
- 可选的阈值自动压缩，以及 Provider 上下文溢出后的单次安全恢复
- 版本化 Session 序列化、原子 JSON 持久化和显式跨进程恢复
- 通过 `McpToolHandler` 提供可选 MCP 集成
- 通过 `SkillManager` 发现本地 Skill、投影 metadata 并显式激活上下文

Core 刻意不再内置 Bash、Git、文件系统、审批 UI 或 Finish 工具。这些能力属于
CodeAgent 等派生 Agent。

本次 Core 边界调整移除了原有的 `BashHandler`、`GitDiffHandler`、
`FinishHandler`、`HumanApproval` 和 `BashApprovalMiddleware` 公共导出；需要
这些能力的派生 Agent 应自行提供对应实现。

## 安装

```bash
uv sync
```

MCP 支持是可选能力。只有使用 MCP 的 Agent 才需要安装额外依赖：

```bash
uv sync --extra mcp
# 或：pip install "SimAgentPlg[mcp]"
```

## 配置

复制 `.env.example` 为 `.env` 并填写模型配置：

```env
MODEL_API_KEY=sk-xxxxxxxx
MODEL_URL=https://api.deepseek.com
CHAT_MODEL=deepseek-v4-flash
LLM_TIMEOUT=60
LLM_TEMPERATURE=0.7
LLM_INCLUDE_USAGE=true
```

`ModelConfig` 属于 `OpenAIModelAdapter`，不再属于 `BaseAgent`。也可以直接构造：

```python
from simagentplg import ModelConfig

config = ModelConfig(
    model="deepseek-v4-flash",
    api_key="sk-xxxxxxxx",
    base_url="https://api.deepseek.com",
)
```

接入其他模型 Provider 时，只需实现 `ModelAdapter.complete()`。适配器负责 Provider
Client 的创建、响应归一化以及可选的启动/关闭资源；`BaseAgent` 只消费归一化后的
`AssistantMessage` 协议。

## 普通 Agent

多次调用之间会保留对话历史：

```python
from simagentplg import BaseAgent, ModelConfig, OpenAIModelAdapter

agent = BaseAgent(
    OpenAIModelAdapter(ModelConfig.from_env()),
    agent_id="tutor",
    system_prompt="你是一名回答简洁的 Python 导师。",
)

first = await agent.runtime(task="请记住我更喜欢 Python。")
second = await agent.runtime(task="我更喜欢哪种编程语言？")

agent.reset()
await agent.shutdown()
```

同一个 Agent 的调用会串行执行，以保护对话状态。

## 结构化运行结果

`run()` 暴露 Core 的运行结果协议：

```python
result = await agent.run(task="解释这个仓库的架构。")

print(result.status)
print(result.stop_reason)
print(result.turns)
print(result.output)
print(result.usage.total_tokens)
print(result.usage.complete)
```

`runtime()` 继续作为兼容接口。任务完成时返回 `result.output`；运行失败、被拒绝或
取消时抛出 `AgentRunError`。

`ModelResponseCompleted` 可以携带标准化 `ModelUsage`。Usage 会保存在 Agent 内部消息
和 Session 中，但 `AgentContextBuilder` 会在构造 `llm_messages` 时移除，不会发送给
Provider。`AgentRunResult.usage` 聚合一次 Run 的所有模型请求；`complete=False` 表示
至少一次请求没有报告 Usage，不能把它当成零消耗。

## 上下文压力与压缩准备

Context Window 容量和累计 Run 消耗是两个独立概念。可以为 Agent 配置可选的
`CompactionPolicy`，在每次模型请求前评估完整 Provider 上下文：

```python
from simagentplg import CompactionPolicy, ContextBudget

context_policy = CompactionPolicy(
    ContextBudget(
        context_window=128_000,
        reserve_tokens=16_000,
        keep_recent_tokens=20_000,
    )
)

agent = BaseAgent(
    model,
    agent_id="context-aware",
    compaction_policy=context_policy,
)
```

评估会组合最近一次 Assistant `ModelUsage`、它之后新增的消息，以及包含当前 Tool Schema
的 UTF-8 感知保守估算。配置策略后，每轮都会发布 `ContextPressureEvaluated`；达到阈值
时，事件中的 `CompactionPreparation` 会分离受保护消息、待摘要的旧完整
User/Assistant/Tool Turn，以及需要原文保留的最近 Turn。Tool Call 和对应 Tool Result
不会被切开。

只配置 `CompactionPolicy` 时，压力评估仍是只读观察。应用也可以直接调用
`estimate_context_usage()` 和 `prepare_compaction()`，并通过
`MessageTokenEstimator` 替换默认估算器。

## 自动压缩与 Overflow 恢复

自动行为默认关闭；启用时复用同一个 `CompactionPolicy` 和 `Compactor`：

```python
from simagentplg import AutoCompactionPolicy

agent = BaseAgent(
    model,
    agent_id="context-aware",
    compaction_policy=context_policy,
    compactor=my_compactor,
    auto_compaction_policy=AutoCompactionPolicy(),
)
```

达到压力阈值后，Core 会在同一次 Agent Run 内压缩旧完整 Turn、重建上下文，再请求模型。
Provider Adapter 抛出 `ContextOverflowError` 时，Core 最多执行一次“压缩—重建—重试”。
第二次溢出返回 `StopReason.CONTEXT_OVERFLOW`；Compactor 失败返回
`StopReason.COMPACTION_FAILED`。一旦 Text 或 Thinking Delta 已对外发布，Core 不会重试，
从而避免重复的流式输出。

`AutoCompactionPolicy(compact_on_pressure=False)` 可以只保留 Overflow 恢复；省略该策略或
设置 `enabled=False` 会关闭全部自动行为。Provider Adapter 通过 `ModelProviderError` 和
`ModelErrorKind` 统一区分上下文溢出、限流、超时、认证及普通 Provider 错误。

## 显式上下文压缩

派生 Agent 通过可取消的 `Compactor` 协议提供摘要行为，然后显式调用：

```python
agent = BaseAgent(
    model,
    agent_id="context-aware",
    compaction_policy=context_policy,
    compactor=my_compactor,
)

compaction = await agent.compact()
print(compaction.status)
print(compaction.summary)
```

`ModelCompactor` 可以把借用的 `ModelAdapter` 接入该协议，同时由应用继续拥有摘要 Prompt：

```python
compactor = ModelCompactor(
    summary_model,
    context_builder=build_summary_context,
    source="summary-model:v1",
)
```

注入的 Builder 接收 `CompactionRequest`，返回完整 `ContextBuildResult`。调用方负责借用模型
的生命周期，因此 Core 不会静默创建另一个 Provider Client，也不会替应用选择 Prompt。

Core 将 `CompactionRequest` 交给 Compactor，由 Core 在 `SummaryEntry` 中写入可信的范围和
Token metadata，最后原子替换成“受保护消息 + Summary + 最近 Turn”。失败或取消返回
结构化 `CompactionResult`，历史保持不变。重复压缩时，旧 Summary 会传给 Compactor
合并，并由新 Summary 消息替换。

生命周期通过 `CompactionStarted`、`CompactionCompleted` 和 `CompactionFailed` 发布。
`abort()`、`wait_for_idle()` 同时适用于普通 Run 和压缩。`SessionRecorder` 保存紧凑恢复
快照，同时保留原始 `SessionMessage` 审计条目。每次压缩都有独立 `operation_id` 和
`CompactionTrigger`。Core 不会替派生 Agent 选择摘要模型或 Prompt。

## 持久化 Session

`SessionRecorder` 可以使用 `JsonFileSessionStorage` 原子保存版本化 Session 文档：

```python
from simagentplg import JsonFileSessionStorage, SessionRecorder

storage = JsonFileSessionStorage("./sessions")
recorder = SessionRecorder(session_id="project-42", storage=storage)
agent = BaseAgent(model, agent_id="core-agent", event_sink=recorder)
await agent.run(task="remember this decision")
```

另一个进程可以读取完成快照并显式恢复新的 Agent：

```python
saved = await storage.load("project-42")
if saved is not None:
    resumed = BaseAgent(model, agent_id="core-agent", event_sink=recorder)
    resumed.restore_session(saved)
```

JSON 格式包含 `SESSION_SCHEMA_VERSION`、Run Result、Usage、消息和 Compaction 快照。
Session ID 会映射为哈希文件名；写入通过临时文件和原子替换完成，因此失败写入不会覆盖
上一个有效快照。损坏 JSON 和未知 Schema 会抛出 `SessionSerializationError`，不会被
误认为 Session 不存在。

`restore_session()` 会校验 Agent 身份，并拒绝包含未完成 Run 的 Session。Core 不会重放
中断的 Tool Call，因为它可能已经产生外部副作用。不同进程可以读取已完成快照，但文件
实现不协调同一 Session 的并发写入；最后一次成功的原子替换生效。

## RuntimePolicy

工具是否存在和任务是否必须显式完成已经解耦：

```python
from simagentplg import RuntimePolicy

policy = RuntimePolicy(
    max_steps=20,
    max_no_tool_responses=3,
    max_repeated_tool_calls=3,
    max_run_tokens=None,
    require_explicit_finish=False,
)
```

可选的 `max_run_tokens` 在轮次边界阻止下一次模型请求。当前响应及其请求的工具会先完整
收尾；达到预算时返回 `TOKEN_BUDGET_EXCEEDED`，需要继续但 Provider 未报告 Usage 时
返回 `USAGE_UNAVAILABLE`。

默认情况下，Agent 可以调用工具，之后用普通文本完成任务。自主型派生 Agent 可以要求
必须调用完成工具：

```python
policy = RuntimePolicy(require_explicit_finish=True)
```

此时派生 Agent 必须自行注册一个返回 `ToolControl.COMPLETE` 的工具。

## 自定义工具

工具通过 Handler 组织。`MethodToolHandler` 会把名为 `add` 的工具映射到异步
`do_add()` 方法：

```python
from collections.abc import Mapping
from typing import Any

from simagentplg import MethodToolHandler, StepOutcome

ADD_TOOL = {
    "type": "function",
    "function": {
        "name": "add",
        "description": "Add two numbers.",
        "parameters": {
            "type": "object",
            "properties": {
                "left": {"type": "number"},
                "right": {"type": "number"},
            },
            "required": ["left", "right"],
        },
    },
}


class MathHandler(MethodToolHandler):
    def __init__(self) -> None:
        super().__init__((ADD_TOOL,))

    async def do_add(self, arguments: Mapping[str, Any]) -> StepOutcome:
        return StepOutcome(
            {"value": arguments["left"] + arguments["right"]}
        )
```

显式注册到 Agent：

```python
agent = BaseAgent(
    OpenAIModelAdapter(ModelConfig.from_env()),
    agent_id="calculator",
    handlers=[MathHandler()],
)
```

重复工具名会在启动阶段报错，不会静默覆盖。

### 工具执行进度

长时间运行的工具可以选择声明一个作用域限定的 `progress` Reporter。没有声明该参数的
现有 `do_*` 方法仍然兼容：

```python
from simagentplg import ToolProgressReporter, ToolProgressUpdate


async def do_index(
    self,
    arguments,
    *,
    cancellation,
    progress: ToolProgressReporter | None = None,
) -> StepOutcome:
    if progress is not None:
        await progress.report(
            ToolProgressUpdate(
                "正在建立文件索引",
                {"completed": 12, "total": 40},
            )
        )
    return StepOutcome({"indexed": 40})
```

每条有效更新都会生成关联当前 run、turn 和 tool call 的 `ToolProgressed` 事件。
Progress 保持顺序，在取消或 `ToolCompleted` 后停止接收；它不会改变 `StepOutcome`、
`ToolControl`，也不会写入 Agent State、Session 或模型上下文。

### 工具控制信号

工具结果数据与运行控制已经分离：

```python
from simagentplg import StepOutcome, ToolControl

StepOutcome(data)  # 继续模型—工具循环
StepOutcome(data, control=ToolControl.COMPLETE)
StepOutcome(data, control=ToolControl.REJECT)
StepOutcome(data, control=ToolControl.CANCEL)
```

运行时由此可以区分正常完成、策略拒绝和取消。

## Tool Middleware

`ToolMiddleware` 用于装饰工具执行，但 Core 不包含具体工具策略：

```python
from simagentplg import ToolMiddleware


class AuditMiddleware(ToolMiddleware):
    async def __call__(self, context, call_next):
        print("before", context.tool_name)
        result = await call_next(context)
        print("after", context.tool_name)
        return result
```

审批 UI 和 Shell 风险规则应由派生 Agent 实现，而不是放在 Core 中。

## MCP 工具

MCP 使用相同的 Handler 协议：

```python
from simagentplg import (
    BaseAgent,
    McpToolHandler,
    ModelConfig,
    OpenAIModelAdapter,
)

agent = BaseAgent(
    OpenAIModelAdapter(ModelConfig.from_env()),
    agent_id="browser",
    handlers=[McpToolHandler("examples/mcp_config.json")],
)
```

启用 MCP 的 Agent 可以执行 MCP 工具，然后直接用普通文本完成任务。只有
`RuntimePolicy` 明确要求时，才需要额外的完成工具。

## Skill

Skill 是独立于 Handler 工具的提示词和资源扩展：

```python
from pathlib import Path

from simagentplg import BaseAgent, ModelConfig, OpenAIModelAdapter

agent = BaseAgent(
    OpenAIModelAdapter(ModelConfig.from_env()),
    agent_id="skilled-agent",
    skills_dir=Path("examples/skills"),
)
```

`SkillManager` 会发现包含 `SKILL.md` 的子目录，并注入包含名称、描述和文件位置的
紧凑 metadata。用户可以用 `$skill_name` 或 `skill:skill_name` 显式选择 Skill，
将其完整指令注入当前上下文。Core 不注册特殊的 Skill 工具；未来带文件读取工具的
派生 Agent 可以根据 metadata 中的位置渐进加载 Skill。

```text
examples/skills/
  release_notes/
    SKILL.md
    template.md
    examples/
      sample.md
```

## Core 边界

SimAgentPlg Core 负责机制：

```text
Orchestration + State + Context + Runtime Policy + Run Result
+ Model Adapter + Tool Protocol + Middleware + MCP + Skills
+ Lifecycle Events + Session + Streaming + Tool Progress + Usage Budget
+ Context Pressure + Compaction Preparation
+ Model Compactor + Summary Entry + Durable Session Snapshot
```

派生 Agent 负责具体能力与策略：

```text
Shell + Filesystem + Git + Workspace + Approval UI
+ Sandbox + Completion Tool + Product Interface
```

架构分析和后续路线参见
[Pi Harness 对照分析](docs/pi-harness-gap-analysis.md)。

## 示例

```bash
uv run python examples/01_stateful_chat.py
uv run python examples/02_custom_tool.py
uv run python examples/04_mcp_tools.py
uv run python examples/06_skill.py
uv run python examples/13_usage_budget.py
uv run python examples/14_context_pressure.py
uv run python examples/15_explicit_compaction.py
uv run python examples/16_durable_session.py record
uv run python examples/16_durable_session.py resume
```

## 测试

```bash
uv run python -m unittest discover -s tests -p 'test*.py' -q
```

提交变更前运行完整的本地质量门：

```bash
uv sync --locked --all-extras --group dev
uv run ruff check src tests examples
uv run ruff format --check src tests examples
uv run mypy
uv build
```

## 公共 API

包根目录导出：

- Agent：`BaseAgent`、`AgentOrchestrator`、`AgentState`、`AgentStatus`
- Provider：`ModelAdapter`、`OpenAIModelAdapter`、`ModelConfig`、`AssistantMessage`、`ModelToolCall`、`ModelUsage`、`ModelErrorKind`、`ModelProviderError`、`ContextOverflowError`、`ModelRateLimitError`、`ModelTimeoutError`、`ModelAuthenticationError`
- Runtime：`RuntimePolicy`、`AgentRunResult`、`RunUsage`、`AgentRunError`、`RunStatus`、`StopReason`
- Session：`AgentSession`、`SessionRecorder`、`SessionStorage`、`MemorySessionStorage`、`JsonFileSessionStorage`、`SessionCompaction`、`SESSION_SCHEMA_VERSION`、`session_to_dict`、`session_from_dict`、`SessionError`、`SessionSerializationError`、`SessionStorageError`
- Context：`AgentContextBuilder`、`ContextBuildResult`、`ContextBudget`、`ContextUsageEstimate`、`CompactionPolicy`、`AutoCompactionPolicy`、`CompactionDecision`、`CompactionPreparation`、`MessageTokenEstimator`
- Compaction：`CompactionRuntime`、`Compactor`、`ModelCompactor`、`CompactionContextBuilder`、`CompactorOutput`、`CompactionRequest`、`CompactionResult`、`CompactionStatus`、`CompactionTrigger`、`SummaryEntry`
- Tool：`StepOutcome`、`ToolControl`、`BaseHandler`、`MethodToolHandler`、`McpToolHandler`
- Middleware：`Middleware`、`ToolMiddleware`、`ToolCallContext`、`ToolNext`
- 扩展：`McpServerManager`、`SkillManager`

## License

MIT
