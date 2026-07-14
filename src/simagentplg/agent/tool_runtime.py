import json
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from simagentplg.agent.state import AgentState
from simagentplg.agent.types import StepOutcome, ToolControl
from simagentplg.handlers.base import BaseHandler
from simagentplg.middleware import (
    ToolCallContext,
    ToolMiddleware,
    ToolNext,
    compose_tool_middlewares,
)
from simagentplg.providers.base import ModelToolCall


@dataclass(frozen=True, slots=True)
class ToolCallResult:
    messages: tuple[dict[str, Any], ...]
    control: ToolControl = ToolControl.CONTINUE
    output: str | None = None


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
    ) -> StepOutcome:
        if not self._started:
            await self.startup()

        self._get_handler(tool_name)
        if self._tool_chain is None:
            raise RuntimeError("tool middleware chain is not initialized")
        context = ToolCallContext(
            state=self.state,
            tool_name=tool_name,
            arguments=dict(arguments),
            tool_call_id=tool_call_id,
        )
        return await self._tool_chain(context)

    async def execute_tool_call(
        self,
        tool_call: ModelToolCall,
    ) -> ToolCallResult:
        tool_name = tool_call.name
        raw_arguments = tool_call.arguments
        self._check_repeated_tool_call(tool_name, raw_arguments)
        self.logger.info(
            "Calling tool %s arguments=%s",
            tool_name,
            summarize_for_log(raw_arguments),
        )
        try:
            arguments = json.loads(raw_arguments)
            if not isinstance(arguments, dict):
                raise TypeError("tool arguments must be a JSON object")
            outcome = await self.dispatch(
                tool_name,
                arguments,
                tool_call_id=tool_call.id,
            )
            self.logger.info(
                "Tool %s completed control=%s result=%s",
                tool_name,
                outcome.control,
                summarize_for_log(outcome.data),
            )
        except Exception as exc:
            self.logger.warning(
                "Tool %s failed: %s arguments=%s",
                tool_name,
                exc,
                summarize_for_log(raw_arguments),
            )
            outcome = StepOutcome(
                {
                    "status": "error",
                    "tool": tool_name,
                    "error": str(exc),
                }
            )

        serialized = serialize_tool_result(outcome.data)
        message = {
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": serialized,
        }
        if outcome.control is not ToolControl.CONTINUE:
            return ToolCallResult(
                (message,),
                control=outcome.control,
                output=serialized,
            )
        return ToolCallResult((message,))

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
        return await handler.dispatch(context.tool_name, context.arguments)

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


def summarize_for_log(data: Any, *, limit: int = 600) -> str:
    if isinstance(data, str):
        text = data
    else:
        text = json.dumps(data, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...<truncated {len(text) - limit} chars>"
