# SimAgentPlg Tool Protocol

> Protocol version: 1  
> Status: Core contract  
> Applies to: custom handlers, MCP adapters, derived-agent tools, and tool middleware

## 1. Purpose

This protocol defines how tools integrate with the SimAgentPlg core. It keeps
tool implementation independent from orchestration and prevents derived agents
from inventing incompatible meanings for completion, failure, rejection, or
cancellation.

The protocol standardizes:

- how a tool is declared to the model;
- how a tool is routed and executed;
- how arguments are validated;
- how results are returned to the model;
- how a tool influences the agent loop;
- how lifecycle and middleware behave;
- which security responsibilities belong to the derived agent.

The protocol does not define concrete Bash, filesystem, Git, browser, approval,
or completion tools. Those belong to derived agents.

## 2. Roles

| Role | Responsibility |
|---|---|
| Agent | Owns state and exposes the public run API |
| Orchestrator | Coordinates model calls, tool calls, and terminal results |
| ToolRuntime | Owns tool lifecycle, routing, middleware, and serialization |
| Handler | Declares and executes one or more related tools |
| Middleware | Intercepts one tool execution without owning its implementation |
| Tool | One model-visible callable operation |

## 3. Tool declaration

Every tool must be declared using an OpenAI-compatible function schema:

```python
READ_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read a UTF-8 text file from the workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Workspace-relative file path.",
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
}
```

### 3.1 Required declaration rules

1. `type` must be `"function"`.
2. `function.name` must be a non-empty string.
3. A tool name must be unique within one Agent.
4. `function.description` should describe when the model should call the tool.
5. `function.parameters` should be a JSON Schema object.
6. Required properties must be listed explicitly.
7. `additionalProperties: False` is recommended for deterministic tools.

Duplicate names are configuration errors. `ToolRuntime.startup()` must reject
them instead of silently replacing a route.

### 3.2 Naming

Tool names should:

- use lowercase `snake_case`;
- describe one operation;
- remain stable after publication;
- avoid encoding UI or provider-specific concepts;
- use a namespace prefix when collision is likely.

Examples:

```text
read_file
edit_file
git_status
browser_open
acme_search_records
```

## 4. Handler contract

A reusable tool group implements `BaseHandler`:

```python
class BaseHandler(ABC):
    @property
    def tools(self) -> Sequence[ToolSchema]: ...

    async def startup(self) -> None: ...
    async def shutdown(self) -> None: ...
    async def on_task_start(self) -> None: ...

    async def dispatch(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> StepOutcome: ...
```

### 4.1 Handler rules

- The constructor should only store configuration and must avoid expensive I/O.
- `startup()` acquires resources and must be idempotent from the runtime's point
  of view.
- `shutdown()` releases acquired resources and should tolerate partial startup.
- `on_task_start()` resets per-task state without deleting persistent resources.
- `dispatch()` must reject tool names not owned by the handler.
- `dispatch()` must return `StepOutcome`.
- Handler instances must not mutate `AgentState` directly.

`MethodToolHandler` may be used for small handlers. It maps a tool called
`read_file` to an async method called `do_read_file()`.

## 5. Arguments

The runtime parses provider arguments as JSON and requires the top-level value
to be an object. The handler receives a mapping:

```python
async def do_read_file(
    self,
    arguments: Mapping[str, Any],
) -> StepOutcome:
    ...
```

Protocol v1 does not perform complete JSON Schema validation in the core.
Therefore every handler must validate all values it uses, including:

- required fields;
- primitive types;
- enum values;
- ranges and length limits;
- path or identifier format;
- cross-field constraints.

Validation failure should normally be returned as a recoverable tool result:

```python
return StepOutcome(
    {
        "status": "error",
        "error": "path must be a non-empty string",
    }
)
```

This allows the model to correct the call on the next turn.

## 6. Result contract

Every successful dispatch returns:

```python
StepOutcome(
    data=...,
    control=ToolControl.CONTINUE,
)
```

`data` should be one of:

- a string;
- a JSON-serializable object;
- a JSON-serializable list.

Structured objects are preferred because they let the model reliably
distinguish status, output, metadata, and error information.

Recommended success shape:

```python
{
    "status": "success",
    "content": "...",
    "metadata": {...},
}
```

Recommended recoverable error shape:

```python
{
    "status": "error",
    "error": "human-readable explanation",
    "code": "stable_machine_code",
}
```

The core serializes strings without modification and serializes other values as
JSON before adding the tool message to conversation state.

## 7. Tool control

Tool payload and loop control are separate. A tool must use `ToolControl` rather
than encoding terminal behavior in `data`.

| Control | Run behavior | Terminal status |
|---|---|---|
| `CONTINUE` | Add the tool result and call the model again | None |
| `COMPLETE` | Stop immediately with the serialized tool result | `COMPLETED` |
| `REJECT` | Stop because execution was rejected by policy or a human | `REJECTED` |
| `CANCEL` | Stop because the operation was cancelled | `CANCELLED` |

### 7.1 Continue

Normal information and action tools should return `CONTINUE`:

```python
return StepOutcome(
    {"status": "success", "value": 42},
    control=ToolControl.CONTINUE,
)
```

`CONTINUE` is the default and may be omitted.

### 7.2 Complete

`COMPLETE` is an optional explicit completion channel:

```python
return StepOutcome(
    {
        "summary": "Implemented the requested change.",
        "tests": ["60 tests passed"],
    },
    control=ToolControl.COMPLETE,
)
```

An Agent with tools does not automatically require a `COMPLETE` tool. With the
default `RuntimePolicy`, tools return `CONTINUE` and the model's final text ends
the task.

Only an Agent configured with:

```python
RuntimePolicy(require_explicit_finish=True)
```

requires at least one reachable execution path that returns `COMPLETE`. The
derived Agent owns that tool, its schema, and the prompt explaining when it must
be called.

### 7.3 Reject

Use `REJECT` only when a requested action was considered but not authorized:

```python
return StepOutcome(
    {
        "status": "rejected",
        "reason": "workspace policy denied access",
    },
    control=ToolControl.REJECT,
)
```

A rejection is not a successful completion and must not use `COMPLETE`.

### 7.4 Cancel

Use `CANCEL` when the action or run was cancelled after it began or while it was
waiting for an external operation:

```python
return StepOutcome(
    {"status": "cancelled"},
    control=ToolControl.CANCEL,
)
```

## 8. Natural completion

The default loop follows this sequence:

```text
assistant tool call
  → handler returns CONTINUE
  → tool result enters context
  → model produces another response
  → response contains no tool call and has non-empty text
  → run completes with StopReason.TEXT_RESPONSE
```

This is the normal behavior for MCP tools and general-purpose agents. A fixed
finish tool is not part of the core protocol.

## 9. Exceptions and recoverable failures

Tool authors must distinguish expected operational errors from programming or
infrastructure failures.

### 9.1 Expected error

Return a normal `StepOutcome` with `CONTINUE`:

```python
return StepOutcome(
    {
        "status": "error",
        "code": "file_not_found",
        "error": f"file not found: {path}",
    }
)
```

The model can inspect the error and select another action.

### 9.2 Unexpected failure

Raise an exception for invariant violations, unavailable infrastructure, or
unexpected implementation failures:

```python
raise RuntimeError("remote executor disconnected")
```

`ToolRuntime` catches execution exceptions invoked through model tool calls and
converts them into error tool results. Direct calls to `BaseAgent.dispatch()`
surface handler and middleware exceptions to the caller.

Tools must not return `COMPLETE` when their requested action failed.

## 10. Multiple tool calls

Protocol v1 executes tool calls sequentially in provider order.

If a tool returns a terminal control (`COMPLETE`, `REJECT`, or `CANCEL`), the
runtime stops processing the remaining calls in that assistant message.

Tool authors must therefore avoid assuming that later calls in the same batch
will run. Parallel execution and batch-level terminal semantics are reserved for
a future protocol version.

## 11. Middleware

Middleware wraps one tool dispatch:

```python
class AuditMiddleware(ToolMiddleware):
    async def __call__(self, context, call_next):
        record_request(context)
        outcome = await call_next(context)
        record_result(context, outcome)
        return outcome
```

Middleware may:

- observe calls;
- validate policy;
- rewrite arguments before calling the next layer;
- short-circuit with a `StepOutcome`;
- post-process an outcome;
- record metrics or audit data.

Middleware must preserve the meaning of `ToolControl`. A policy rejection must
return `REJECT`, not `COMPLETE`.

Middleware runs in declaration order around the handler. Lifecycle methods are
started in declaration order and shut down in reverse order.

## 12. MCP adapter

`McpToolHandler` adapts MCP tools to the same Handler contract:

```text
MCP tool schema → BaseHandler.tools
MCP call        → BaseHandler.dispatch
MCP result      → StepOutcome(CONTINUE)
```

MCP tools naturally return `CONTINUE` unless a derived Agent adds an explicit
policy adapter. A standalone MCP Agent normally completes through the model's
final text response.

## 13. State and side effects

Tools may modify external systems when their declared purpose requires it, but
they must not directly append conversation messages or change Agent run status.

The runtime owns:

- assistant and tool message persistence;
- task status transitions;
- turn counters;
- completion and failure results.

Tools own:

- operation-specific side effects;
- validation and domain errors;
- external resource cleanup;
- operation-specific result data.

## 14. Security boundary

The core Tool Protocol is not a sandbox or permission system.

Derived agents implementing sensitive tools must define:

- workspace boundaries;
- path traversal and symlink rules;
- command allow/deny policy;
- timeouts and output limits;
- network policy;
- credential handling;
- approval requirements;
- audit logging;
- cancellation and cleanup behavior.

A tool description is not a security control. All restrictions must be enforced
in executable code before the side effect occurs.

## 15. Complete example

```python
from collections.abc import Mapping
from typing import Any

from simagentplg import (
    BaseAgent,
    MethodToolHandler,
    ModelConfig,
    StepOutcome,
    ToolControl,
)

LOOKUP_TOOL = {
    "type": "function",
    "function": {
        "name": "lookup_record",
        "description": "Look up one record by its stable identifier.",
        "parameters": {
            "type": "object",
            "properties": {
                "record_id": {"type": "string", "minLength": 1},
            },
            "required": ["record_id"],
            "additionalProperties": False,
        },
    },
}


class RecordHandler(MethodToolHandler):
    def __init__(self) -> None:
        super().__init__((LOOKUP_TOOL,))

    async def do_lookup_record(
        self,
        arguments: Mapping[str, Any],
    ) -> StepOutcome:
        record_id = arguments.get("record_id")
        if not isinstance(record_id, str) or not record_id.strip():
            return StepOutcome(
                {
                    "status": "error",
                    "code": "invalid_record_id",
                    "error": "record_id must be a non-empty string",
                }
            )

        record = await lookup_record(record_id.strip())
        if record is None:
            return StepOutcome(
                {
                    "status": "error",
                    "code": "not_found",
                    "error": f"record not found: {record_id}",
                }
            )

        return StepOutcome(
            {"status": "success", "record": record},
            control=ToolControl.CONTINUE,
        )


agent = BaseAgent(
    config=ModelConfig.from_env(),
    agent_id="record-agent",
    handlers=[RecordHandler()],
)
```

## 16. Compliance checklist

A tool is Protocol v1 compliant when:

- [ ] its schema is a named function with object parameters;
- [ ] its name is unique and stable;
- [ ] its constructor performs no expensive I/O;
- [ ] resource acquisition and cleanup use lifecycle methods;
- [ ] every used argument is validated by the handler;
- [ ] every dispatch returns `StepOutcome`;
- [ ] normal tools return `CONTINUE`;
- [ ] only successful explicit completion returns `COMPLETE`;
- [ ] policy denial returns `REJECT`;
- [ ] cancellation returns `CANCEL`;
- [ ] expected errors are structured and recoverable;
- [ ] unexpected failures raise exceptions;
- [ ] sensitive side effects enforce policy in code;
- [ ] the tool does not mutate Agent messages or task status directly;
- [ ] unit tests cover success, invalid arguments, expected errors, and control
      signals.

## 17. Reserved future extensions

The following are intentionally not part of Protocol v1:

- streaming tool progress;
- cancellation tokens passed into handlers;
- binary and image result parts;
- JSON Schema validation performed by the core;
- parallel tool execution;
- batch-level completion semantics;
- resumable or suspended tool calls;
- standardized approval requests.

These features should extend the protocol without changing the meanings of
`CONTINUE`, `COMPLETE`, `REJECT`, or `CANCEL`.
