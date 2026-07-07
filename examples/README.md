# SimAgentPlg Examples

These examples use the environment variables documented in the project
README. Copy `.env.example` to `.env` and fill in your model credentials before
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
- `06_skill.py`: local skill discovery, indexing, template, and sample injection
- `07_bash_approval.py`: deterministic `BashApprovalMiddleware` y/n approval

Tool-enabled agents expose only the handlers explicitly passed to
`BaseAgent`. `BashHandler` provides `bash_run`, while `FinishHandler` provides
`run_finish`. A custom tool can also finish a task by returning
`StepOutcome(..., should_exit=True)`. Plain text does not finish a tool task,
and the third identical consecutive tool call is rejected before execution.

The MCP example requires the commands declared in `mcp_config.json` to be
available locally. Its sample configuration starts the Playwright MCP server
through `npx`.

The skill example loads `example/skills/release_notes/`. Skill metadata is
injected into the model context. The model can call the internal `load_skill`
tool to load full `SKILL.md`, optional `template.md`, and optional
`examples/sample.md` content, and the user can still name a skill explicitly.
