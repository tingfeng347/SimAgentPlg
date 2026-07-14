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
- 通过 `McpToolHandler` 提供可选 MCP 集成
- 通过 `SkillManager` 发现并按需加载本地 Skill

Core 刻意不再内置 Bash、Git、文件系统、审批 UI 或 Finish 工具。这些能力属于
CodeAgent 等派生 Agent。

本次 Core 边界调整移除了原有的 `BashHandler`、`GitDiffHandler`、
`FinishHandler`、`HumanApproval` 和 `BashApprovalMiddleware` 公共导出；需要
这些能力的派生 Agent 应自行提供对应实现。

## 安装

```bash
uv sync
```

## 配置

复制 `.env.example` 为 `.env` 并填写模型配置：

```env
MODEL_API_KEY=sk-xxxxxxxx
MODEL_URL=https://api.deepseek.com
CHAT_MODEL=deepseek-v4-flash
LLM_TIMEOUT=60
LLM_TEMPERATURE=0.7
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
```

`runtime()` 继续作为兼容接口。任务完成时返回 `result.output`；运行失败、被拒绝或
取消时抛出 `AgentRunError`。

## RuntimePolicy

工具是否存在和任务是否必须显式完成已经解耦：

```python
from simagentplg import RuntimePolicy

policy = RuntimePolicy(
    max_steps=20,
    max_no_tool_responses=3,
    max_repeated_tool_calls=3,
    require_explicit_finish=False,
)
```

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

`SkillManager` 会发现包含 `SKILL.md` 的子目录，注入紧凑 metadata，并提供内部
`load_skill` 工具按需加载完整指令。用户也可以用 `$skill_name` 或
`skill:skill_name` 显式选择 Skill。

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
```

## 测试

```bash
uv run python -m unittest discover -s tests -p 'test*.py' -q
```

## 公共 API

包根目录导出：

- Agent：`BaseAgent`、`AgentOrchestrator`、`AgentState`、`AgentStatus`
- Provider：`ModelAdapter`、`OpenAIModelAdapter`、`ModelConfig`、`AssistantMessage`、`ModelToolCall`
- Runtime：`RuntimePolicy`、`AgentRunResult`、`AgentRunError`、`RunStatus`、`StopReason`
- Context：`AgentContextBuilder`、`ContextBuildResult`
- Tool：`StepOutcome`、`ToolControl`、`BaseHandler`、`MethodToolHandler`、`McpToolHandler`
- Middleware：`Middleware`、`ToolMiddleware`、`ToolCallContext`、`ToolNext`
- 扩展：`McpServerManager`、`SkillManager`

## License

MIT
