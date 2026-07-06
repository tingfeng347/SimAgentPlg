# SimAgentPlg

[English](README.md) | [简体中文](README_zh-CN.md)

SimAgentPlg 0.2.3 is a lightweight framework for building stateful
OpenAI-compatible agents with composable tool handlers, optional MCP tools,
and local skill indexing.

## Features

- Stateful `BaseAgent` with conversation memory and explicit `reset()`
- Immutable, required `agent_id` owned by each agent
- OpenAI-compatible model configuration through `.env` or direct construction
- Handler-driven tool execution with no separate tool-mode switch
- Built-in `BashHandler` for bounded Bash execution
- Built-in `GitDiffHandler` for Git working-tree inspection
- Built-in `FinishHandler` for explicit task completion
- `MethodToolHandler` for small custom Python tools
- `AgentManager` with per-agent serialization and cross-agent concurrency
- Optional MCP integration through `McpToolHandler` and `McpServerManager`
- Optional local skill discovery, indexing, and on-demand loading through `SkillManager`

Python 3.12 or newer is required.

## Installation

Install the local project and dependencies with `uv`:

```bash
uv sync
```

## Configuration

Copy `.env.example` to `.env`, then fill in your model credentials:

```env
MODEL_API_KEY=sk-xxxxxxxx
MODEL_URL=https://api.deepseek.com
CHAT_MODEL=deepseek-v4-flash
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

Pass handlers explicitly to enable tool execution:

```python
import json

from simagentplg import (
    BaseAgent,
    BashHandler,
    FinishHandler,
    GitDiffHandler,
    ModelConfig,
)

agent = BaseAgent(
    config=ModelConfig.from_env(),
    agent_id="developer",
    system_prompt="Complete coding tasks using the available tools.",
    handlers=[BashHandler(), GitDiffHandler(), FinishHandler()],
)

result = await agent.runtime(task="Create hello.py that prints 'hello'.")
report = json.loads(result)
print(report["summary"])

await agent.shutdown()
```

Agents expose only the handlers passed to `BaseAgent`:

```text
BaseAgent
  -> BashHandler
       -> bash_run
  -> GitDiffHandler
       -> run_gitdiff
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
working directory, timeout, and output limit.

`GitDiffHandler` exposes `run_gitdiff`. It inspects the current Git working
tree and does not finish the task:

```json
{
  "status": "success",
  "mode": "status",
  "command": "git status --short",
  "output": "?? hello.py\n"
}
```

Supported modes are `status` for `git status --short`, `stat` for
`git diff --stat`, and `diff` for `git diff`.

`FinishHandler` exposes `run_finish`. It returns a JSON result and exits the
current `runtime()`:

```json
{
  "summary": "Created hello.py"
}
```

## Tool Middleware

`ToolMiddleware` can inspect tool calls before execution. The framework does
not define global risk levels; applications can write their own middleware.
`BashApprovalMiddleware` is an approval gate, not a shell sandbox or security
boundary. By default, commands outside a small safe allowlist require y/n
approval:

```python
from simagentplg import (
    BaseAgent,
    BashApprovalMiddleware,
    BashHandler,
    FinishHandler,
    ModelConfig,
)

agent = BaseAgent(
    ModelConfig.from_env(),
    agent_id="coder",
    handlers=[BashHandler(), FinishHandler()],
    middlewares=[BashApprovalMiddleware()],
)
```

The default `approval_policy="unless_safe"` skips approval only for simple
read-only commands such as `pwd`, `ls`, `git status`, `git diff`, `git log`,
`rg`, `sed -n`, `cat`, and Python unittest invocations. Use
`approval_policy="always"` to review every `bash_run` command.
`approval_policy="never"` disables this approval gate explicitly.

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
        "writer": "Write release notes for version 0.2.3.",
        "reviewer": "Review the release for compatibility risks.",
    }
)

await manager.shutdown()
```

Calls to the same agent are serialized because they share message history.
Calls to different agents can run concurrently. `run_many()` returns failures
as values so one failed agent does not cancel the others.

## MCP Tools

MCP is opt-in and follows the same handler contract:

```python
from simagentplg import BaseAgent, McpToolHandler, ModelConfig

agent = BaseAgent(
    config=ModelConfig.from_env(),
    agent_id="browser",
    handlers=[McpToolHandler("example/mcp_config.json")],
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

from simagentplg import BaseAgent, FinishHandler, ModelConfig

agent = BaseAgent(
    config=ModelConfig.from_env(),
    agent_id="skilled-agent",
    handlers=[FinishHandler()],
    skills_dir=Path("example/skills"),
)
```

`SkillManager` scans each child directory containing `SKILL.md`, indexes the
skill name and YAML front matter description, and injects compact skill
metadata into the model context. The model can call the internal `load_skill`
tool to load full `SKILL.md`, optional `template.md`, and optional
`examples/sample.md` content on demand. Users can also force a skill with
`$skill_name` or `skill:skill_name`.

```text
example/skills/
  release_notes/
    SKILL.md
    template.md
    examples/
      sample.md
```

Skill context itself does not require handler tools. Register `FinishHandler`
only when the task should finish with `run_finish`.

## Examples

Runnable examples are available in [`example/`](example/README.md):

```bash
uv run python example/01_stateful_chat.py
uv run python example/02_custom_tool.py
uv run python example/03_multi_agent.py
uv run python example/04_mcp_tools.py
uv run python example/06_skill.py
uv run python example/07_bash_approval.py
```

## Testing

Run the test suite from the repository root:

```bash
uv run python -m unittest
```

The current tests cover agents, custom handlers, tool middleware, finish
behavior, manager locking/concurrency, and importable examples.

## Public API

```python
BaseAgent(
    config: ModelConfig | None = None,
    *,
    agent_id: str,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    handlers: Iterable[BaseHandler] | None = None,
    middlewares: Iterable[MiddleWare] | None = None,
    skills_dir: str | Path | None = None,
    max_steps: int = 20,
    client: Any | None = None,
)

await agent.runtime(*, task: str) -> str | None
agent.reset(history=None)
await agent.startup()
await agent.shutdown()
```

Passing handlers puts `BaseAgent` in tool execution mode and injects the
runtime's internal tool protocol system message. Without handlers, the agent is
plain chat; `skills_dir` can still expose the internal `load_skill` context
tool without requiring a finishing tool.

The top-level package exports `BaseAgent`, `ModelConfig`, `StepOutcome`,
`AgentManager`, handler base classes, `MethodToolHandler`,
`BashHandler`, `GitDiffHandler`, `FinishHandler`, `McpToolHandler`, handler errors,
`McpServerManager`, `SkillManager`, and default resource paths.

## Changes in 0.2.3

- Added the sibling `FinishHandler` and built-in `run_finish` tool
- Added the sibling `GitDiffHandler` and built-in `run_gitdiff` tool
- Required explicit finishing-tool completion in tool mode
- Added protection against three identical consecutive tool calls
- Raised a clear error when tool mode exhausts `max_steps`
- Kept `BashHandler` focused exclusively on `bash_run`

## License

MIT
