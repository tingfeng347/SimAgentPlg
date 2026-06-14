# SimAgentPlg

A lightweight framework for stateful OpenAI-compatible agents, composable tool
handlers, MCP integration, and multi-agent coordination.

## Features

- **Stateful `BaseAgent`** with explicit `reset()` support
- **Reusable tool handlers** instead of tool logic embedded in the agent
- **Atomic local tools** through `MethodToolHandler`
- **MCP tools** through an explicit `McpToolHandler`
- **`AgentManager`** with per-agent serialization and cross-agent concurrency
- **Optional skills** loaded from a local skills directory
- **OpenAI-compatible models** configured with `ModelConfig`

## Installation

```bash
pip install simagentplg
```

Python 3.12 or newer is required.

## Configuration

Create a `.env` file:

```env
CHAT_MODEL=deepseek-v4-flash
MODEL_API_KEY=sk-xxxxxxxx
MODEL_URL=https://api.deepseek.com
LLM_TIMEOUT=60
LLM_TEMPERATURE=0.7
SKILL_MODEL=deepseek-v4-flash
```

`ModelConfig.from_env()` reads these variables. A config can also be constructed
directly and shared safely by multiple agents:

```python
from simagentplg import ModelConfig

config = ModelConfig(
    model="deepseek-v4-flash",
    api_key="sk-xxxxxxxx",
    base_url="https://api.deepseek.com",
)
```

## Quick Start

`BaseAgent` uses a `BashHandler` by default:

```python
from simagentplg import BaseAgent, ModelConfig

agent = BaseAgent(config=ModelConfig.from_env())
result = await agent.runtime(task="打印当前目录中的 Python 文件")
await agent.shutdown()
```

For plain chat, tool handlers are neither started nor sent to the model:

```python
agent = BaseAgent(
    config=ModelConfig.from_env(),
    system_prompt="你是一个言简意赅的 Python 导师。",
    enable_tools=False,
)
result = await agent.runtime(task="解释什么是生成器")
```

An agent keeps conversation memory between `runtime()` calls:

```python
await agent.runtime(task="我叫小明")
result = await agent.runtime(task="我叫什么？")

agent.reset()
agent.reset(history=[{"role": "user", "content": "从这里继续"}])
```

## Examples

Runnable examples are available in [`example/`](example/README.md):

```bash
uv run python example/01_stateful_chat.py
uv run python example/02_custom_tool.py
uv run python example/03_multi_agent.py
uv run python example/04_mcp_tools.py
uv run python example/05_role_workflow.py
```

They cover stateful chat, custom atomic tools, multi-agent coordination, and
MCP integration, plus role-based serial workflows.

## Tool Handlers

`BaseHandler` is the common interface for local and external tools:

```text
BaseAgent
  -> BaseHandler
       -> MethodToolHandler
            -> BashHandler
            -> custom atomic handlers
       -> McpToolHandler
```

### Custom Atomic Tools

`MethodToolHandler` maps a tool named `add` to an async method named `do_add`:

```python
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
        super().__init__([ADD_TOOL])

    async def do_add(self, arguments: dict[str, Any]) -> StepOutcome:
        value = arguments["left"] + arguments["right"]
        return StepOutcome({"value": value})
```

Compose handlers when creating an agent:

```python
from simagentplg import BaseAgent, BashHandler, ModelConfig

agent = BaseAgent(
    config=ModelConfig.from_env(),
    handlers=[
        BashHandler(),
        MathHandler(),
    ],
)
```

Handler startup builds one tool routing table. Duplicate tool names fail
immediately instead of silently overriding another handler.

### MCP Tools

MCP is opt-in and uses the same handler contract:

```python
from simagentplg import BaseAgent, McpToolHandler, ModelConfig

agent = BaseAgent(
    config=ModelConfig.from_env(),
    handlers=[
        McpToolHandler("my_project/mcp_config.json"),
    ],
)
```

Example configuration:

```json
{
  "playwright": {
    "command": "npx",
    "args": ["@playwright/mcp@latest", "--headless"]
  }
}
```

Calling an unknown local tool never falls back to MCP. Only tools registered by
`McpToolHandler` are routed to MCP.

## Skills

Skills remain optional prompt extensions and are separate from tool handlers:

```python
from simagentplg import BaseAgent, DEFAULT_SKILLS_DIR, ModelConfig

agent = BaseAgent(
    config=ModelConfig.from_env(),
    skills_dir=DEFAULT_SKILLS_DIR,
)
```

Each skill directory must contain `SKILL.md` with YAML front matter. Pass a
custom directory to load application-specific skills.

## Agent Manager

`AgentManager` registers existing agents. It does not construct agents or own
application-specific workflow rules.

```python
from simagentplg import AgentManager, BaseAgent, ModelConfig

config = ModelConfig.from_env()
manager = AgentManager()
manager.register(
    "assistant",
    BaseAgent(config=config, system_prompt="You are a helpful assistant."),
)
manager.register(
    "reviewer",
    BaseAgent(config=config, system_prompt="You are a careful reviewer."),
)

result = await manager.run("assistant", "完成任务")

results = await manager.run_many(
    {
        "assistant": "总结当前进度",
        "reviewer": "检查当前结果",
    }
)

await manager.shutdown()
```

Calls to the same agent are serialized because they mutate one message history.
Calls to different agents run concurrently. `run_many()` returns exceptions as
values for failed entries so one failure does not cancel the remaining agents.

## Agent Workflow

`AgentWorkflow` connects specialized agents as a linear pipeline. Every step
resets its agent before running, so roles exchange information only through
explicit prompt templates.

```python
from simagentplg import AgentWorkflow, WorkflowStep

workflow = AgentWorkflow(
    manager,
    [
        WorkflowStep(
            name="plan",
            agent_id="planner",
            prompt="请规划以下任务：\n{input}",
        ),
        WorkflowStep(
            name="execute",
            agent_id="executor",
            prompt=(
                "原始目标：\n{original_task}\n\n"
                "请执行以下方案：\n{input}"
            ),
        ),
        WorkflowStep(
            name="review",
            agent_id="reviewer",
            prompt="请审查执行结果：\n{execute}",
        ),
    ],
)

result = await workflow.run("实现用户登录功能")
print(result.final_output)
```

Templates support `{input}` for the previous output, `{original_task}` for the
initial task, and a completed step name such as `{plan}` or `{execute}`.
Unknown and forward references fail when the workflow is created.

`WorkflowResult` contains the original task, every rendered step task and
output, and the final output. A failed step raises `WorkflowExecutionError`
with the failed step, original cause, and all completed step results.

## Public API

```python
BaseAgent(
    config: ModelConfig | None = None,
    *,
    system_prompt: str = REACT_LOOP_PROMPT,
    handlers: Iterable[BaseHandler] | None = None,
    enable_tools: bool = True,
    skills_dir: str | Path | None = None,
    max_steps: int = 20,
)

await agent.runtime(*, task: str) -> str | None
agent.reset(history=None)
await agent.startup()
await agent.shutdown()

await manager.run_isolated(agent_id, task)
await workflow.run(task) -> WorkflowResult
```

## Migrating from 0.1.3

Version 0.2.0 intentionally removes the old inheritance API:

- `LLMConfig` has been removed.
- `BaseHandler` is now composed into `BaseAgent` instead of inherited by it.
- Move `do_<tool_name>` methods into a `MethodToolHandler` subclass.
- Wrap MCP configuration with `McpToolHandler`.
- `runtime()` keeps memory; use `reset()` to start a clean conversation.
- Use `AgentManager` when coordinating multiple stateful agents.

## Development

```bash
uv run python -m unittest discover -s tests -p "test*.py" -v
```

## License

MIT
