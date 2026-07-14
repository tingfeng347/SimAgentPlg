# SimAgentPlg

[English](README.md) | [简体中文](README_zh-CN.md)

SimAgentPlg is a lightweight core for building stateful, extensible agents on
OpenAI-compatible model APIs. It provides the runtime mechanism—state,
orchestration, context construction, tool dispatch, middleware, MCP, and
skills—while derived agents own concrete tools such as shell, file editing,
Git, or explicit completion.

Requires Python 3.12 or newer.

## Core capabilities

- Stateful `BaseAgent` with persistent conversation history and `reset()`
- Public `AgentOrchestrator` for the provider-tool loop
- Structured `AgentRunResult`, `RunStatus`, and `StopReason`
- Explicit `RuntimePolicy` for loop and completion behavior
- `AgentContextBuilder` for non-mutating per-turn context projection
- Composable `BaseHandler` and `MethodToolHandler` tool contracts
- `ToolRuntime` lifecycle, routing, middleware, and repeat-call protection
- Generic `ToolMiddleware` interception
- Optional MCP integration through `McpToolHandler`
- Local skill discovery and on-demand loading through `SkillManager`

The core intentionally does not provide Bash, Git, filesystem, approval UI, or
finish tools. Those belong to a derived agent such as a CodeAgent.

This core-boundary change removes the former `BashHandler`, `GitDiffHandler`,
`FinishHandler`, `HumanApproval`, and `BashApprovalMiddleware` public exports.
Derived agents should provide equivalent implementations when needed.

## Installation

```bash
uv sync
```

## Configuration

Copy `.env.example` to `.env` and provide model credentials:

```env
MODEL_API_KEY=sk-xxxxxxxx
MODEL_URL=https://api.deepseek.com
CHAT_MODEL=deepseek-v4-flash
LLM_TIMEOUT=60
LLM_TEMPERATURE=0.7
```

Configuration can also be supplied directly:

```python
from simagentplg import ModelConfig

config = ModelConfig(
    model="deepseek-v4-flash",
    api_key="sk-xxxxxxxx",
    base_url="https://api.deepseek.com",
)
```

## Plain agent

Conversation history is preserved across calls:

```python
from simagentplg import BaseAgent, ModelConfig

agent = BaseAgent(
    config=ModelConfig.from_env(),
    agent_id="tutor",
    system_prompt="You are a concise Python tutor.",
)

first = await agent.runtime(task="Remember that I prefer Python.")
second = await agent.runtime(task="Which language do I prefer?")

agent.reset()
await agent.shutdown()
```

Calls on the same agent are serialized to protect conversation state.

## Structured runs

`run()` exposes the core result protocol:

```python
result = await agent.run(task="Explain the repository architecture.")

print(result.status)
print(result.stop_reason)
print(result.turns)
print(result.output)
```

`runtime()` remains a compatibility wrapper. It returns `result.output` for a
completed run and raises `AgentRunError` for failed, rejected, or cancelled
runs.

## Runtime policy

Tool availability and completion policy are independent:

```python
from simagentplg import RuntimePolicy

policy = RuntimePolicy(
    max_steps=20,
    max_no_tool_responses=3,
    max_repeated_tool_calls=3,
    require_explicit_finish=False,
)
```

By default, an agent may call tools and later complete with ordinary text. A
derived autonomous agent can require a completion tool:

```python
policy = RuntimePolicy(require_explicit_finish=True)
```

That agent must register one of its own tools that returns
`ToolControl.COMPLETE`.

## Custom tools

Tools are grouped into handlers. `MethodToolHandler` maps a tool named `add` to
an async `do_add()` method:

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

Register it explicitly:

```python
agent = BaseAgent(
    config=ModelConfig.from_env(),
    agent_id="calculator",
    handlers=[MathHandler()],
)
```

Duplicate tool names fail during startup instead of being silently
overwritten.

Tool authors should follow the versioned
[SimAgentPlg Tool Protocol](TOOL_PROTOCOL.md).

### Tool control signals

Tool payload and runtime control are separate:

```python
from simagentplg import StepOutcome, ToolControl

StepOutcome(data)  # continue the provider-tool loop
StepOutcome(data, control=ToolControl.COMPLETE)
StepOutcome(data, control=ToolControl.REJECT)
StepOutcome(data, control=ToolControl.CANCEL)
```

This lets the runtime distinguish successful completion, policy rejection,
and cancellation.

## Tool middleware

`ToolMiddleware` decorates a tool execution without owning concrete tool
policy:

```python
from simagentplg import ToolMiddleware


class AuditMiddleware(ToolMiddleware):
    async def __call__(self, context, call_next):
        print("before", context.tool_name)
        result = await call_next(context)
        print("after", context.tool_name)
        return result
```

Approval UI and shell-specific risk policies should be implemented by the
derived agent, not by the core.

## MCP tools

MCP uses the same handler contract:

```python
from simagentplg import BaseAgent, McpToolHandler, ModelConfig

agent = BaseAgent(
    config=ModelConfig.from_env(),
    agent_id="browser",
    handlers=[McpToolHandler("examples/mcp_config.json")],
)
```

An MCP-enabled agent can execute MCP tools and then complete with plain text.
It does not need a separate finish tool unless its `RuntimePolicy` explicitly
requires one.

## Skills

Skills are prompt and resource extensions independent of handler tools:

```python
from pathlib import Path

from simagentplg import BaseAgent, ModelConfig

agent = BaseAgent(
    config=ModelConfig.from_env(),
    agent_id="skilled-agent",
    skills_dir=Path("examples/skills"),
)
```

`SkillManager` discovers child folders containing `SKILL.md`, injects compact
metadata, and exposes an internal `load_skill` tool for on-demand instruction
loading. Users can explicitly select a skill with `$skill_name` or
`skill:skill_name`.

```text
examples/skills/
  release_notes/
    SKILL.md
    template.md
    examples/
      sample.md
```

## Core boundary

SimAgentPlg core owns mechanisms:

```text
Orchestration + State + Context + Runtime Policy + Run Result
+ Tool Protocol + Middleware + MCP + Skills
```

Derived agents own concrete capabilities and policies:

```text
Shell + Filesystem + Git + Workspace + Approval UI
+ Sandbox + Completion Tool + Product Interface
```

See [the Pi Harness comparison](docs/pi-harness-gap-analysis.md) for the
architecture analysis and future roadmap.

## Examples

```bash
uv run python examples/01_stateful_chat.py
uv run python examples/02_custom_tool.py
uv run python examples/04_mcp_tools.py
uv run python examples/06_skill.py
```

## Tests

```bash
uv run python -m unittest discover -s tests -p 'test*.py' -q
```

## Public API

The package root exports:

- Agent: `BaseAgent`, `AgentOrchestrator`, `AgentState`, `AgentStatus`
- Runtime: `RuntimePolicy`, `AgentRunResult`, `AgentRunError`, `RunStatus`, `StopReason`
- Context: `AgentContextBuilder`, `ContextBuildResult`
- Tools: `StepOutcome`, `ToolControl`, `BaseHandler`, `MethodToolHandler`, `McpToolHandler`
- Middleware: `Middleware`, `ToolMiddleware`, `ToolCallContext`, `ToolNext`
- Extensions: `McpServerManager`, `SkillManager`

## License

MIT
