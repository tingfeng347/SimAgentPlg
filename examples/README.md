# SimAgentPlg Examples

All examples use the environment variables documented in the project README.
Copy `.env.example` to `.env` and fill in credentials for an OpenAI-compatible
provider before running them. Examples `07` through `10` exercise the Harness
against the real `OpenAIModelAdapter`; they do not use scripted model results.

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
- `07_event_observers.py`: ordered lifecycle events and multiple independent
  sinks
- `08_session_resume.py`: linear Session recording and recovery in a new agent
- `09_runtime_control.py`: `abort()`, `wait_for_idle()`, terminal events, and reuse
- `10_composed_harness.py`: Model Adapter, tools, Middleware, events, Session,
  runtime policy, and explicit completion composed as one Harness

Run the composed Harness example directly:

```bash
uv run python examples/10_composed_harness.py
```

The Harness examples make real provider requests, so response text and exact
timing depend on the configured model. Their control flow, event ordering,
Session projection, and terminal result protocols remain deterministic.

Tool-enabled agents expose only the handlers explicitly passed to
`BaseAgent`. Tool availability does not force an explicit completion call;
plain text completes a task by default. A derived agent can require explicit
completion through `RuntimePolicy` and return
`StepOutcome(..., control=ToolControl.COMPLETE)` from its own completion tool.
The repeated-tool-call guard remains configurable through the same policy.

The MCP example requires the commands declared in `mcp_config.json` to be
available locally. Its sample configuration starts the Playwright MCP server
through `npx`.

The skill example loads `examples/skills/release_notes/`. Skill metadata,
including its file location, is injected into the model context. Naming the
skill explicitly loads its full `SKILL.md`, optional `template.md`, and optional
`examples/sample.md` content. The core does not register a special skill tool.

The event examples use `CompositeAgentEventSink` to attach observers without
changing the Agent Loop. The Session examples use `SessionRecorder` and
`MemorySessionStorage`; only real user, assistant, and tool messages are saved.
The runtime-control example distinguishes external `abort()` from
`ToolControl.CANCEL` and waits until terminal event sinks have settled before
reporting idle. It waits briefly before aborting the provider request; set
`HARNESS_ABORT_DELAY` to adjust that delay.
