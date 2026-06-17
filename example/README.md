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
- `06_skill.py`: local skill discovery, routing, template, and sample injection
- `07_god_simulator.py`: LLM-led god sandbox MVP with a CLI

Tool-enabled agents expose only the handlers explicitly passed to
`BaseAgent`. `BashHandler` provides `bash_run`, while `FinishHandler` provides
`run_finish`. A custom tool can also finish a task by returning
`StepOutcome(..., should_exit=True)`. Plain text does not finish a tool task,
and the third identical consecutive tool call is rejected before execution.

The MCP example requires the commands declared in `mcp_config.json` to be
available locally. Its sample configuration starts the Playwright MCP server
through `npx`.

The skill example loads `example/skills/release_notes/`. `SKILL_MODEL` selects
the model used to route the task to a skill; when omitted, it defaults to
`gpt-4o-mini`. The selected `SKILL.md`, optional `template.md`, and optional
`examples/sample.md` are injected into the agent context.

The god simulator example requires model credentials because every faction
leader is controlled by an LLM agent at strategic turns.
