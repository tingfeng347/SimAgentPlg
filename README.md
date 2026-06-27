# SimAgentPlg

[English](README.md) | [简体中文](README_zh-CN.md)

SimAgentPlg 0.2.2 is a lightweight framework for building stateful
OpenAI-compatible agents with composable tool handlers, optional MCP tools,
local skill routing, and simple role-based multi-agent workflows.

## Features

- Stateful `BaseAgent` with conversation memory and explicit `reset()`
- Immutable, required `agent_id` owned by each agent
- OpenAI-compatible model configuration through `.env` or direct construction
- Opt-in tool mode with explicit handler registration
- Built-in `BashHandler` for bounded Bash execution
- Built-in `FinishHandler` for explicit task completion and Git change reports
- `MethodToolHandler` for small custom Python tools
- `AgentManager` with per-agent serialization and cross-agent concurrency
- Linear `AgentWorkflow` for planner, executor, reviewer, and similar roles
- Optional MCP integration through `McpToolHandler` and `McpServerManager`
- Optional local skill discovery and routing through `SkillManager`

Python 3.12 or newer is required.

## Installation

Install the local project and dependencies with `uv`:

```bash
uv sync
```

## Configuration

Copy `.env_example` to `.env`, then fill in your model credentials:

```env
MODEL_API_KEY=sk-xxxxxxxx
MODEL_URL=https://api.deepseek.com
CHAT_MODEL=deepseek-v4-flash
SKILL_MODEL=deepseek-v4-flash
LLM_TIMEOUT=60
LLM_TEMPERATURE=0.7
```

`ModelConfig.from_env()` reads `CHAT_MODEL`, `MODEL_API_KEY`, `MODEL_URL`,
`LLM_TIMEOUT`, and `LLM_TEMPERATURE`.

You can also construct a config directly:

```python
from simagentplg import ModelConfig

config = ModelConfig(
    model="deepseek-v4-flash",
    api_key="sk-xxxxxxxx",
    base_url="https://api.deepseek.com",
)
```

## Quick Start

### Plain Chat

Tool execution is disabled by default. A plain agent keeps conversation history
between `runtime()` calls:

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

### Tool Mode

Set `enable_tools=True` and pass handlers explicitly:

```python
import json

from simagentplg import BaseAgent, BashHandler, FinishHandler, ModelConfig

agent = BaseAgent(
    config=ModelConfig.from_env(),
    agent_id="developer",
    system_prompt="Complete coding tasks using the available tools.",
    handlers=[BashHandler(), FinishHandler()],
    enable_tools=True,
)

result = await agent.runtime(task="Create hello.py that prints 'hello'.")
report = json.loads(result)
print(report["summary"])
print(report["changes"])

await agent.shutdown()
```

Tool-enabled agents expose only the handlers passed to `BaseAgent`:

```text
BaseAgent
  -> BashHandler
       -> bash_run
  -> FinishHandler
       -> run_finish
  -> MethodToolHandler subclasses
  -> McpToolHandler
```

In tool mode, ordinary text does not complete a task. The model must call a
finishing tool, normally `run_finish`, or a custom tool must return
`StepOutcome(..., should_exit=True)`.

Tool mode stops with an error when:

- no finishing tool is called within `max_steps`
- the same tool and arguments are requested three consecutive times

## Built-In Handlers

`BashHandler` exposes `bash_run` and executes a bounded Bash command. It has a
working directory, timeout, output limit, and a small blacklist for obviously
dangerous commands.

`FinishHandler` exposes `run_finish`. It returns a JSON result and exits the
current `runtime()`:

```json
{
  "summary": "Created hello.py",
  "changes": {
    "available": true,
    "repository": "/repo/root",
    "added": ["hello.py"],
    "modified": [],
    "deleted": []
  }
}
```

The change report compares Git state at the beginning and end of the current
task. `run_finish` does not commit, stage, or revert files. Outside a Git
repository, the task can still finish with `changes.available` set to `false`.

## Custom Tool Handlers

`MethodToolHandler` maps a tool named `add` to an async method named `do_add`:

```python
from collections.abc import Mapping
from typing import Any

from simagentplg import BaseAgent, MethodToolHandler, ModelConfig, StepOutcome

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


agent = BaseAgent(
    config=ModelConfig.from_env(),
    agent_id="calculator",
    handlers=[MathHandler()],
    enable_tools=True,
)
```

Handler startup builds one routing table. Duplicate tool names are rejected
instead of silently overriding another handler.

## Agent Manager

Each agent owns its identity, so registration does not repeat the ID:

```python
from simagentplg import AgentManager, BaseAgent, ModelConfig

config = ModelConfig.from_env()
manager = AgentManager()

manager.register(
    BaseAgent(
        config=config,
        agent_id="writer",
        system_prompt="You write concise release notes.",
    )
)
manager.register(
    BaseAgent(
        config=config,
        agent_id="reviewer",
        system_prompt="You review software changes for risk.",
    )
)

results = await manager.run_many(
    {
        "writer": "Write release notes for version 0.2.2.",
        "reviewer": "Review the release for compatibility risks.",
    }
)

await manager.shutdown()
```

Calls to the same agent are serialized because they share message history.
Calls to different agents can run concurrently. `run_many()` returns failures
as values so one failed agent does not cancel the others.

`run_isolated(agent_id, task)` resets and executes an agent while holding the
same per-agent lock. Workflows use it to avoid implicit history leaks between
roles or steps.

## Role-Based Workflow

`AgentWorkflow` executes agent roles as a validated linear pipeline:

```python
from simagentplg import (
    AgentManager,
    AgentWorkflow,
    BaseAgent,
    ModelConfig,
    WorkflowStep,
)

config = ModelConfig.from_env()
manager = AgentManager()
manager.register(
    BaseAgent(
        config=config,
        agent_id="planner",
        system_prompt="Create concise implementation plans.",
    )
)
manager.register(
    BaseAgent(
        config=config,
        agent_id="executor",
        system_prompt="Execute the plan using tools.",
        enable_tools=True,
    )
)
manager.register(
    BaseAgent(
        config=config,
        agent_id="reviewer",
        system_prompt="Review completed work for correctness and risk.",
    )
)

workflow = AgentWorkflow(
    manager,
    [
        WorkflowStep(
            name="plan",
            agent_id="planner",
            prompt="Plan this task:\n{input}",
        ),
        WorkflowStep(
            name="execute",
            agent_id="executor",
            prompt=(
                "Original task:\n{original_task}\n\n"
                "Execute this plan:\n{input}"
            ),
        ),
        WorkflowStep(
            name="review",
            agent_id="reviewer",
            prompt="Review the execution result:\n{execute}",
        ),
    ],
)

result = await workflow.run("Implement user login")
print(result.final_output)
await manager.shutdown()
```

Workflow templates support `{input}`, `{original_task}`, and outputs from
completed named steps such as `{plan}` or `{execute}`. Unknown variables and
forward references are rejected when the workflow is created. Version 0.2.2
supports linear steps only.

## MCP Tools

MCP is opt-in and follows the same handler contract:

```python
from simagentplg import BaseAgent, McpToolHandler, ModelConfig

agent = BaseAgent(
    config=ModelConfig.from_env(),
    agent_id="browser",
    handlers=[McpToolHandler("example/mcp_config.json")],
    enable_tools=True,
)
```

Example MCP configuration:

```json
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": ["@playwright/mcp@latest", "--headless"]
    }
  }
}
```

`McpServerManager` loads configured services, exposes tools with service-name
prefixes, and lets one failed service avoid blocking the rest.

## Skills

Skills are optional prompt extensions and remain separate from tool handlers:

```python
from pathlib import Path

from simagentplg import BaseAgent, ModelConfig

agent = BaseAgent(
    config=ModelConfig.from_env(),
    agent_id="skilled-agent",
    skills_dir=Path("example/skills"),
    enable_tools=True,
)
```

`SkillManager` scans each child directory containing `SKILL.md`. The routing
model selected by `SKILL_MODEL` chooses a skill from its YAML front matter.
The selected `SKILL.md`, optional `template.md`, and optional
`examples/sample.md` are injected into the agent context.

```text
example/skills/
  release_notes/
    SKILL.md
    template.md
    examples/
      sample.md
```

Skills currently run through the tool-mode lifecycle, so set
`enable_tools=True` and finish with `run_finish`.

## Examples

Runnable examples are available in [`example/`](example/README.md):

```bash
uv run python example/01_stateful_chat.py
uv run python example/02_custom_tool.py
uv run python example/03_multi_agent.py
uv run python example/04_mcp_tools.py
uv run python example/05_role_workflow.py
uv run python example/06_skill.py
```

## Testing

Run the test suite from the repository root:

```bash
uv run python -m unittest
```

The current tests cover agents, custom handlers, finish behavior, manager
locking/concurrency, workflows, and importable examples.

## Public API

```python
BaseAgent(
    config: ModelConfig | None = None,
    *,
    agent_id: str,
    system_prompt: str = REACT_LOOP_PROMPT,
    handlers: Iterable[BaseHandler] | None = None,
    enable_tools: bool = False,
    skills_dir: str | Path | None = None,
    max_steps: int = 20,
    client: Any | None = None,
)

await agent.runtime(*, task: str) -> str | None
agent.reset(history=None)
await agent.startup()
await agent.shutdown()
```

The top-level package exports `BaseAgent`, `ModelConfig`, `StepOutcome`,
`AgentManager`, workflow types, handler base classes, `MethodToolHandler`,
`BashHandler`, `FinishHandler`, `McpToolHandler`, handler errors,
`McpServerManager`, `SkillManager`, and default resource paths.

## Changes in 0.2.2

- Added the sibling `FinishHandler` and built-in `run_finish` tool
- Added per-task Git change reporting
- Required explicit finishing-tool completion in tool mode
- Added protection against three identical consecutive tool calls
- Raised a clear error when tool mode exhausts `max_steps`
- Kept `BashHandler` focused exclusively on `bash_run`

## License

MIT
