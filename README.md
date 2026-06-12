# SimAgentPlg

A lightweight multi-agent framework with ReAct reasoning, tool dispatch, and MCP integration.

## Features

- **BaseAgent** — unified ReAct agent that doubles as a chat bot via `enable_tools=False`
- **Tool Dispatch** — convention-over-configuration: define `do_{tool_name}` methods, auto-routed via reflection
- **MCP Integration** — pluggable MCP server manager for external tool providers
- **Skill System** — skill-based prompt injection for domain-specific behaviors
- **Built-in Bash Executor** — async sandboxed bash execution with timeout, output truncation, and blacklist filtering
- **Customizable Prompt & Tools** — override system prompt or point to your own MCP config / skills directory
- **Stateless Execution** — each `runtime()` call starts with a clean context; history is caller-managed
- **OpenAI-compatible** — works with any OpenAI-compatible API (DeepSeek, etc.)

## Installation

```bash
pip install simagentplg
```

Or with uv:

```bash
uv pip install simagentplg
```

## Quick Start

Set up your environment variables (`.env`):

```env
CHAT_MODEL=deepseek-v4-flash
SKLL_MODEL=deepseek-v4-flash
MODEL_API_KEY=sk-xxxxxxxx
MODEL_URL=https://api.deepseek.com
LLM_TIMEOUT=30
```

### Tool Mode (default)

```python
from simagentplg import BaseAgent

agent = BaseAgent()
result = await agent.runtime(task="帮我写一个Python脚本打印当前时间")
```

In tool mode, `BaseAgent` follows a ReAct loop — it thinks, calls tools (built-in `bash_run`, MCP tools, skills), and iterates until it reaches a final answer.

### Chat Mode

```python
agent = BaseAgent(enable_tools=False)
result = await agent.runtime(task="介绍一下你自己")
```

When `enable_tools=False`, no MCP/skills are loaded and `tools=None` is passed to the LLM, turning it into a pure conversational agent.

### Custom System Prompt

```python
agent = BaseAgent(
    system_prompt="你是一个专业的 Python 导师，回答时要言简意赅。",
    enable_tools=False,
)
result = await agent.runtime(task="如何在 Python 中读写 JSON 文件？")
```

### Custom MCP Config & Skills

```python
agent = BaseAgent(
    mcp_config_path="/my_project/mcp_config.json",
    skills_dir="/my_project/skills",
)
result = await agent.runtime(task="你的任务")
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `system_prompt` | ReAct prompt | System prompt for the agent |
| `enable_tools` | `True` | Enable tool calling (MCP + skills + local tools) |
| `mcp_config_path` | auto (built-in) | Path to your MCP config JSON file |
| `skills_dir` | auto (built-in) | Path to your skills directory |

### Multi-turn History

```python
history = [
    {"role": "user", "content": "今天天气不错"},
    {"role": "assistant", "content": "是啊，适合出去走走"},
]
result = await agent.runtime(task="我们去哪", history=history)
```

## Architecture

```
LLMConfig (BaseHandler, ABC)
└── BaseAgent             — unified agent (tool mode + chat mode)
     ├── MCP tools        — external tools via MCP protocol
     ├── Skill system     — domain-specific prompt injection
     └── Local tools      — built-in bash_run, extensible
```

### Directory Structure

```
agent/
  runner/
    baseagent.py          ← BaseAgent + REACT_LOOP_PROMPT
    mcp_config.json        ← default MCP server configuration
    skills/                ← default skills directory
      weather/
        SKILL.md
  base.py                  ← LLMConfig, BaseHandler, StepOutcome
  tool_schema.py           ← local tool schemas
```

### Tool Dispatch Flow

```
LLM calls "bash_run"
    → BaseHandler.dispatch("bash_run", args)
        → hasattr(self, "do_bash_run")?  YES
            → await self.do_bash_run(args)  ← local tool
        → NO
            → "未知工具" → MCP fallback  ← external tool
```

### Adding a Local Tool

1. Define the tool schema in `tool_schema.py`:

```python
{
    "type": "function",
    "function": {
        "name": "calculator",
        "description": "Evaluate a math expression",
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "Math expression"}
            },
            "required": ["expression"]
        }
    }
}
```

2. Add the `do_calculator` method in `LLMConfig`:

```python
async def do_calculator(self, args: dict) -> StepOutcome:
    result = eval(args["expression"])
    return StepOutcome(data=result, next_prompt="\n")
```

All agents automatically inherit the new tool.

## MCP Configuration

Place an `mcp_config.json` alongside your `BaseAgent`, or pass `mcp_config_path`:

```json
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": ["-y", "@anthropic/mcp-playwright"]
    }
  }
}
```

## Skill System

Create a skills directory with subdirectories each containing a `SKILL.md`:

```
skills/
  my_skill/
    SKILL.md   ← skill definition (markdown with YAML front-matter)
```

Pass `skills_dir` to `BaseAgent` or use the built-in `skills/` directory.

## API

### `BaseAgent`

```python
agent = BaseAgent(
    system_prompt=REACT_LOOP_PROMPT,  # custom prompt
    enable_tools=True,                # tool mode (False = chat mode)
    mcp_config_path=None,             # path to MCP config JSON
    skills_dir=None,                  # path to skills directory
)
await agent.runtime(*, task, history=None) -> str | None
```

### `StepOutcome`

```python
@dataclass
class StepOutcome:
    data: Any                # tool return value
    next_prompt: str | None  # None = task complete
    should_exit: bool        # True = force exit
```

## Running Tests

```bash
# Init-only tests (no API key needed)
pytest tests/test_react_loop.py -m "not integration"

# Full integration tests (.env required)
python tests/test_react_loop.py
```

## Requirements

- Python >= 3.12
- fastmcp >= 3.4.2
- openai >= 2.41.0
- python-dotenv >= 1.2.2

## License

MIT
