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
- Provider-neutral `ModelAdapter` boundary with an OpenAI-compatible adapter
- Public `AgentOrchestrator` for the provider-tool loop
- Structured `AgentRunResult`, `RunStatus`, and `StopReason`
- Explicit `RuntimePolicy` for loop and completion behavior
- `AgentContextBuilder` for non-mutating per-turn context projection
- Composable `BaseHandler` and `MethodToolHandler` tool contracts
- `ToolRuntime` lifecycle, routing, middleware, and repeat-call protection
- Generic `ToolMiddleware` interception
- Structured, cancellable Tool Progress events
- Optional MCP integration through `McpToolHandler`
- Local skill discovery, metadata projection, and explicit context activation

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

`ModelConfig` belongs to `OpenAIModelAdapter`, rather than to `BaseAgent`.
Configuration can also be supplied directly:

```python
from simagentplg import ModelConfig

config = ModelConfig(
    model="deepseek-v4-flash",
    api_key="sk-xxxxxxxx",
    base_url="https://api.deepseek.com",
)
```

Other model providers can integrate with the core by implementing
`ModelAdapter.complete()` and optionally overriding `ModelAdapter.stream()`.
The adapter owns provider client creation, response normalization, streaming,
and optional startup/shutdown resources; `BaseAgent` only consumes
provider-neutral stream events and the normalized `AssistantMessage` contract.

## Plain agent

Conversation history is preserved across calls:

```python
from simagentplg import BaseAgent, ModelConfig, OpenAIModelAdapter

agent = BaseAgent(
    OpenAIModelAdapter(ModelConfig.from_env()),
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

### Cancelling a run

Each run owns an independent cancellation token. `abort()` requests
cancellation without waiting, while `wait_for_idle()` settles only after the
terminal event and all awaited event sinks have completed:

```python
import asyncio

run = asyncio.create_task(agent.run(task="Perform a long operation."))

agent.abort("stopped by user")
await agent.wait_for_idle()
result = await run
```

An externally aborted run returns `RunStatus.CANCELLED` with
`StopReason.EXTERNAL_ABORT`. The same agent can be reused for another run.
Model adapters, tool middleware, and tool handlers receive the run's
`CancellationToken`; long-running handlers should also use `try/finally` to
release resources such as subprocesses.

### Streaming responses

`BaseAgent.run()` still returns one final `AgentRunResult`, while provisional
text and provisional reasoning are observed through typed Delta events:

```python
from simagentplg import AssistantThinkingDelta, AssistantTextDelta


class ConsoleSink:
    async def emit(self, event):
        if isinstance(event.payload, AssistantThinkingDelta):
            print("[thinking]", event.payload.delta, end="")
        elif isinstance(event.payload, AssistantTextDelta):
            print(event.payload.delta, end="", flush=True)
```

`OpenAIModelAdapter` uses a real streaming request. Tool-call fragments are
assembled inside the provider adapter and only complete `AssistantMessage`
objects enter Agent state. Thinking Delta remains observation-only and is not
mixed into normal text or persisted to Session. Existing complete-only adapters
remain compatible through the default `ModelAdapter.stream()` implementation.
Session recording ignores provisional deltas and persists only
`MessageCompleted`.

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

from simagentplg import CancellationToken, MethodToolHandler, StepOutcome

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

    async def do_add(
        self,
        arguments: Mapping[str, Any],
        *,
        cancellation: CancellationToken | None = None,
    ) -> StepOutcome:
        return StepOutcome(
            {"value": arguments["left"] + arguments["right"]}
        )
```

Register it explicitly:

```python
agent = BaseAgent(
    OpenAIModelAdapter(ModelConfig.from_env()),
    agent_id="calculator",
    handlers=[MathHandler()],
)
```

Duplicate tool names fail during startup instead of being silently
overwritten.

### Tool progress

Long-running tools can optionally accept a scoped `progress` reporter. Existing
`do_*` methods that do not declare this keyword remain compatible:

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
                "indexing files",
                {"completed": 12, "total": 40},
            )
        )
    return StepOutcome({"indexed": 40})
```

Each accepted update becomes a `ToolProgressed` event correlated with the
current run, turn, and tool call. Updates are ordered, stop after cancellation,
and are ignored after `ToolCompleted`. They never change `StepOutcome` or
`ToolControl`, and are not persisted to Agent state or Session.

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
and tool-requested cancellation. `ToolControl.CANCEL` is a tool's business
decision; external `agent.abort()` uses the separate run cancellation
protocol.

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

An MCP-enabled agent can execute MCP tools and then complete with plain text.
It does not need a separate finish tool unless its `RuntimePolicy` explicitly
requires one.

## Skills

Skills are prompt and resource extensions independent of handler tools:

```python
from pathlib import Path

from simagentplg import BaseAgent, ModelConfig, OpenAIModelAdapter

agent = BaseAgent(
    OpenAIModelAdapter(ModelConfig.from_env()),
    agent_id="skilled-agent",
    skills_dir=Path("examples/skills"),
)
```

`SkillManager` discovers child folders containing `SKILL.md` and injects compact
metadata containing each skill's name, description, and file location. Users
can explicitly select a skill with `$skill_name` or `skill:skill_name`, which
injects its full instructions into the current context. The core does not
register a special skill tool; a derived agent with a file-reading tool can use
the advertised location for progressive loading.

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
+ Model Adapter + Tool Protocol + Middleware + MCP + Skills
+ Lifecycle Events + Linear Session + Runtime Cancellation
+ Provider Streaming + Tool Progress
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
# Provider-backed examples
uv run python examples/01_stateful_chat.py
uv run python examples/02_custom_tool.py
uv run python examples/04_mcp_tools.py
uv run python examples/06_skill.py

# Harness examples using the configured real provider
uv run python examples/07_event_observers.py
uv run python examples/08_session_resume.py
uv run python examples/09_runtime_control.py
uv run python examples/10_composed_harness.py
uv run python examples/11_streaming_events.py
uv run python examples/12_tool_progress.py
```

See [the examples guide](examples/README.md) for the capability demonstrated by
each file.

## Tests

```bash
uv run python -m unittest discover -s tests -p 'test*.py' -q
```

## Public API

The package root exports:

- Agent: `BaseAgent`, `AgentOrchestrator`, `AgentState`, `AgentStatus`
- Providers: `ModelAdapter`, `OpenAIModelAdapter`, `ModelConfig`, `AssistantMessage`, `ModelToolCall`, `ModelStreamEvent`, `ModelTextDelta`, `ModelThinkingDelta`, `ModelResponseCompleted`
- Runtime: `RuntimePolicy`, `AgentRunResult`, `AgentRunError`, `RunStatus`, `StopReason`
- Cancellation: `CancellationToken`, `CancellationSource`, `AgentCancelledError`
- Events: `AgentEvent`, `AgentEventSink`, `CompositeAgentEventSink`, `AssistantTextDelta`, `AssistantThinkingDelta`, `ToolProgressed`
- Session: `AgentSession`, `SessionRecorder`, `SessionStorage`, `MemorySessionStorage`
- Context: `AgentContextBuilder`, `ContextBuildResult`
- Tools: `StepOutcome`, `ToolControl`, `ToolProgressUpdate`, `ToolProgressReporter`, `BaseHandler`, `MethodToolHandler`, `McpToolHandler`
- Middleware: `Middleware`, `ToolMiddleware`, `ToolCallContext`, `ToolNext`
- Extensions: `McpServerManager`, `SkillManager`

## License

MIT
