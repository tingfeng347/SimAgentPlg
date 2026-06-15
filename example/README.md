# SimAgentPlg Examples

These examples use the environment variables documented in the project
README. Copy `.env_example` to `.env` and fill in your model credentials before
running them.

Run an example from the repository root:

```bash
uv run python example/01_stateful_chat.py
```

Every `BaseAgent` declares its own immutable `agent_id`. Manager examples
register the agent object directly with `manager.register(agent)`.

## Examples

- `01_stateful_chat.py`: plain chat, conversation memory, and `reset()`
- `02_custom_tool.py`: a custom atomic tool with `MethodToolHandler`
- `03_multi_agent.py`: concurrent tasks coordinated by `AgentManager`
- `04_mcp_tools.py`: opt-in MCP integration with a custom config file
- `05_role_workflow.py`: planner, executor, and reviewer in a linear workflow

Tool-enabled agents include two sibling built-in handlers: `BashHandler`
provides `bash_run`, while `FinishHandler` provides `run_finish`. The finish
result contains the model's summary plus Git files added, modified, or deleted
during that `runtime()` call. Plain text does not finish a tool task, and the
third identical consecutive tool call is rejected before execution.

The MCP example requires the commands declared in `mcp_config.json` to be
available locally. Its sample configuration starts the Playwright MCP server
through `npx`.
