import json
import logging
from collections.abc import Iterable, Mapping
from typing import Any

from simagentplg.agent.cancellation import (
    AgentCancelledError,
    CancellationSource,
    CancellationToken,
)
from simagentplg.agent.events import (
    AgentEventEmitter,
    ToolCompleted,
    ToolStarted,
)
from simagentplg.agent.state import AgentState
from simagentplg.agent.types import StepOutcome, ToolCallResult, ToolControl
from simagentplg.handlers.base import BaseHandler
from simagentplg.middleware import (
    ToolCallContext,
    ToolMiddleware,
    ToolNext,
    compose_tool_middlewares,
)
from simagentplg.providers.base import ModelToolCall

class RepeatedToolCallError(RuntimeError):
    """Raised when an identical tool call reaches the configured limit."""


class ToolRuntime:
    """Lifecycle, routing, middleware, and execution for tool handlers."""

    def __init__(
        self,
        handlers: Iterable[BaseHandler],
        middlewares: Iterable[ToolMiddleware],
        *,
        state: AgentState,
        logger: logging.Logger,
        event_emitter: AgentEventEmitter,
        max_repeated_tool_calls: int = 3,
    ) -> None:
        if max_repeated_tool_calls <= 0:
            raise ValueError(
                "max_repeated_tool_calls must be greater than zero"
            )
        self.handlers = list(handlers)
        self.middlewares = list(middlewares)
        self.state = state
        self.logger = logger
        self.event_emitter = event_emitter
        self.max_repeated_tool_calls = max_repeated_tool_calls
        self._tool_routes: dict[str, BaseHandler] = {}
        self._active_middlewares: list[ToolMiddleware] = []
        self._tool_chain: ToolNext | None = None
        self._started = False
        self._last_tool_signature: tuple[str, str] | None = None
        self._repeated_tool_calls = 0

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [
            tool
            for handler in self.handlers
            for tool in handler.tools
        ]

    async def startup(self) -> None:
        if self._started:
            return

        started_handlers: list[BaseHandler] = []
        started_middlewares: list[ToolMiddleware] = []
        try:
            for handler in self.handlers:
                await handler.startup()
                started_handlers.append(handler)
            self._tool_routes = self._build_tool_routes()
            self._active_middlewares = (
                [
                    middleware
                    for middleware in self.middlewares
                    if middleware.enabled
                ]
                if self._tool_routes
                else []
            )
            for middleware in self._active_middlewares:
                await middleware.startup()
                started_middlewares.append(middleware)
            self._tool_chain = compose_tool_middlewares(
                self._active_middlewares,
                self._dispatch_handler,
            )
        except Exception:
            for middleware in reversed(started_middlewares):
                try:
                    await middleware.shutdown()
                except Exception as shutdown_error:
                    self.logger.warning(
                        "Middleware %s rollback shutdown failed: %s",
                        type(middleware).__name__,
                        shutdown_error,
                    )
            for handler in reversed(started_handlers):
                try:
                    await handler.shutdown()
                except Exception as shutdown_error:
                    self.logger.warning(
                        "Handler %s rollback shutdown failed: %s",
                        type(handler).__name__,
                        shutdown_error,
                    )
            self._tool_routes.clear()
            self._active_middlewares.clear()
            self._tool_chain = None
            raise

        self._started = True

    async def shutdown(self) -> None:
        if not self._started:
            return

        errors: list[Exception] = []
        for middleware in reversed(self._active_middlewares):
            try:
                await middleware.shutdown()
            except Exception as exc:
                errors.append(exc)
        for handler in reversed(self.handlers):
            try:
                await handler.shutdown()
            except Exception as exc:
                errors.append(exc)

        self._tool_routes.clear()
        self._active_middlewares.clear()
        self._tool_chain = None
        self._started = False
        if errors:
            raise RuntimeError(
                f"failed to shut down {len(errors)} handler(s)"
            ) from errors[0]

    async def on_task_start(self) -> None:
        """Reset per-task tool state and notify handlers/middleware."""

        self._last_tool_signature = None
        self._repeated_tool_calls = 0
        for handler in self.handlers:
            await handler.on_task_start()
        for middleware in self._active_middlewares:
            await middleware.on_task_start()

    async def dispatch(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        tool_call_id: str | None = None,
        cancellation: CancellationToken | None = None,
    ) -> StepOutcome:
        if not self._started:
            await self.startup()

        self._get_handler(tool_name)
        if self._tool_chain is None:
            raise RuntimeError("tool middleware chain is not initialized")
        token = cancellation or CancellationSource().token
        context = ToolCallContext(
            state=self.state,
            tool_name=tool_name,
            arguments=dict(arguments),
            tool_call_id=tool_call_id,
            cancellation=token,
        )
        return await token.run(self._tool_chain(context))

    async def execute_tool_call(
        self,
        tool_call: ModelToolCall,
        *,
        cancellation: CancellationToken,
    ) -> ToolCallResult:
        tool_name = tool_call.name
        raw_arguments = tool_call.arguments
        await self.event_emitter.emit(
            ToolStarted(self.state.turn, tool_call)
        )
        error: str | None = None
        try:
            self._check_repeated_tool_call(tool_name, raw_arguments)
            arguments = json.loads(raw_arguments)
            if not isinstance(arguments, dict):
                raise TypeError("tool arguments must be a JSON object")
            outcome = await self.dispatch(
                tool_name,
                arguments,
                tool_call_id=tool_call.id,
                cancellation=cancellation,
            )
        except AgentCancelledError as exc:
            result = self._cancelled_tool_result(tool_call, str(exc))
            await self.event_emitter.emit(
                ToolCompleted(self.state.turn, tool_call, result)
            )
            return result
        except RepeatedToolCallError as exc:
            result = ToolCallResult((), error=str(exc))
            await self.event_emitter.emit(
                ToolCompleted(self.state.turn, tool_call, result)
            )
            raise
        except Exception as exc:
            error = str(exc)
            outcome = StepOutcome(
                {
                    "status": "error",
                    "tool": tool_name,
                    "error": error,
                }
            )

        serialized = serialize_tool_result(outcome.data)
        message = {
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": serialized,
        }
        if outcome.control is not ToolControl.CONTINUE:
            result = ToolCallResult(
                (message,),
                control=outcome.control,
                output=serialized,
                error=error,
            )
        else:
            result = ToolCallResult((message,), error=error)
        await self.event_emitter.emit(
            ToolCompleted(self.state.turn, tool_call, result)
        )
        return result

    async def cancel_tool_call(
        self,
        tool_call: ModelToolCall,
        *,
        reason: str,
    ) -> ToolCallResult:
        """Settle an unstarted call skipped after run cancellation."""

        await self.event_emitter.emit(
            ToolStarted(self.state.turn, tool_call)
        )
        result = self._cancelled_tool_result(tool_call, reason)
        await self.event_emitter.emit(
            ToolCompleted(self.state.turn, tool_call, result)
        )
        return result

    def _build_tool_routes(self) -> dict[str, BaseHandler]:
        routes: dict[str, BaseHandler] = {}
        for handler in self.handlers:
            for tool_name in handler.tool_names:
                if tool_name in routes:
                    first = type(routes[tool_name]).__name__
                    second = type(handler).__name__
                    raise ValueError(
                        f"duplicate tool {tool_name!r} in {first} and {second}"
                    )
                routes[tool_name] = handler
        return routes

    def _get_handler(self, tool_name: str) -> BaseHandler:
        try:
            return self._tool_routes[tool_name]
        except KeyError as exc:
            available = ", ".join(sorted(self._tool_routes)) or "none"
            raise KeyError(
                f"unknown tool {tool_name!r}; available tools: {available}"
            ) from exc

    async def _dispatch_handler(self, context: ToolCallContext) -> StepOutcome:
        handler = self._get_handler(context.tool_name)
        return await handler.dispatch(
            context.tool_name,
            context.arguments,
            cancellation=context.cancellation,
        )

    def _cancelled_tool_result(
        self,
        tool_call: ModelToolCall,
        reason: str,
    ) -> ToolCallResult:
        message = {
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": serialize_tool_result(
                {
                    "status": "cancelled",
                    "tool": tool_call.name,
                    "error": reason,
                }
            ),
        }
        return ToolCallResult(
            (message,),
            error=reason,
            cancelled=True,
        )

    def _check_repeated_tool_call(
        self,
        tool_name: str,
        raw_arguments: str,
    ) -> None:
        try:
            arguments = json.loads(raw_arguments)
            normalized_arguments = json.dumps(
                arguments,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError):
            normalized_arguments = raw_arguments

        signature = (tool_name, normalized_arguments)
        if signature == self._last_tool_signature:
            self._repeated_tool_calls += 1
        else:
            self._last_tool_signature = signature
            self._repeated_tool_calls = 1

        if self._repeated_tool_calls >= self.max_repeated_tool_calls:
            raise RepeatedToolCallError(
                f"tool {tool_name!r} was called with the same arguments "
                f"{self.max_repeated_tool_calls} consecutive times"
            )


def serialize_tool_result(data: Any) -> str:
    if isinstance(data, str):
        return data
    return json.dumps(data, ensure_ascii=False, default=str)
