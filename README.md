# SimAgentPlg

A lightweight multi-agent framework with ReAct reasoning, tool dispatch, and MCP integration.

## Features

- **ReAct Agent** — ReAct (Reasoning + Acting) loop with multi-turn tool calling
- **Chat Agent** — simple conversational agent with multi-turn history support
- **Tool Dispatch** — convention-over-configuration: define `do_{tool_name}` methods, auto-routed via reflection
- **MCP Integration** — pluggable MCP server manager for external tool providers
- **Skill System** — skill-based prompt injection for domain-specific behaviors
- **Built-in Bash Executor** — async sandboxed bash execution with timeout, output truncation, and blacklist filtering
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
CHAT_MODEL=deepseek-chat
MODEL_API_KEY=sk-xxxxxxxx
MODEL_URL=https://api.deepseek.com
LLM_TIMEOUT=30
```

### Chat Agent

```python
from simagentplg import ChatLoop

loop = ChatLoop()
result = await loop.runtime(task="介绍一下你自己")

# With multi-turn history
history = [
    {"role": "user", "content": "今天天气不错"},
    {"role": "assistant", "content": "是啊，适合出去走走"},
]
result = await loop.runtime(task="我们去哪", history=history)
```

### ReAct Agent

```python
from simagentplg import ReactLoop

loop = ReactLoop()
result = await loop.runtime(task="帮我写一个Python脚本打印当前时间")
```

The ReAct agent supports built-in tools (like `bash_run`) and any MCP tools configured in `mcp_config.json`.

### MCP Configuration

Place an `mcp_config.json` alongside your ReactLoop:

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

## Architecture

```
LLMConfig (BaseHandler, ABC)
├── ChatLoop         — stateless conversational agent
├── ReactLoop        — ReAct reasoning + tool dispatch
│   ├── MCP tools    — external tools via MCP protocol
│   ├── Skill system — domain-specific prompt injection
│   └── Local tools  — built-in bash_run, extensible
└── (future) PlanLoop / ExecuteLoop
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
LOCAL_TOOLS = [
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
]
```

2. Add the `do_calculator` method in `LLMConfig`:

```python
async def do_calculator(self, args: dict) -> StepOutcome:
    result = eval(args["expression"])
    return StepOutcome(data=result, next_prompt="\n")
```

All agents automatically inherit the new tool.

## API

### `ChatLoop`

```python
loop = ChatLoop(temperature=0.7)
await loop.runtime(*, task, system_prompt=None, history=None) -> str | None
```

### `ReactLoop`

```python
loop = ReactLoop()
await loop.runtime(*, task, system_prompt=None, history=None) -> str | None
```

### `StepOutcome`

```python
@dataclass
class StepOutcome:
    data: Any              # tool return value
    next_prompt: str | None  # None = task complete
    should_exit: bool      # True = force exit
```

## Requirements

- Python >= 3.12
- fastmcp >= 3.4.2
- openai >= 2.41.0
- python-dotenv >= 1.2.2

## License

MIT
