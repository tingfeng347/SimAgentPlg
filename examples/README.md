# SimAgentPlg Examples

These examples use the environment variables documented in the project
README. Copy `.env.example` to `.env` and fill in your model credentials before
running them.

Run an example from the repository root:

```bash
uv run python examples/01_stateful_chat.py
```

Every `BaseAgent` declares its own immutable `agent_id`.

## Examples

- `01_stateful_chat.py`: plain chat, conversation memory, and `reset()`
- `02_custom_tool.py`: a custom atomic tool with `MethodToolHandler`
- `04_mcp_tools.py`: opt-in MCP integration with a custom config file
- `06_skill.py`: local skill discovery, indexing, template, and sample injection

Tool-enabled agents expose only the handlers explicitly passed to
`BaseAgent`. Tool availability does not force an explicit completion call;
plain text completes a task by default. A derived agent can require explicit
completion through `RuntimePolicy` and return
`StepOutcome(..., control=ToolControl.COMPLETE)` from its own completion tool.
The repeated-tool-call guard remains configurable through the same policy.

The MCP example requires the commands declared in `mcp_config.json` to be
available locally. Its sample configuration starts the Playwright MCP server
through `npx`.

The skill example loads `examples/skills/release_notes/`. Skill metadata is
injected into the model context. The model can call the internal `load_skill`
tool to load full `SKILL.md`, optional `template.md`, and optional
`examples/sample.md` content, and the user can still name a skill explicitly.
