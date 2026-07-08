import logging
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from simagentplg.agent.middleware import Middleware
from simagentplg.agent.types import StepOutcome
from simagentplg.handlers.base import BaseHandler

MAX_REPEATED_TOOL_CALLS = 3


@dataclass(frozen=True, slots=True)
class ToolCallResult:
    messages: tuple[dict[str, Any], ...]
    exit_value: str | None = None


class ToolRuntime:
    """Lifecycle, routing, middleware, and execution for tool handlers."""

    def __init__(
        self,
        handlers: Iterable[BaseHandler],
        middlewares: Iterable[Middleware],
        *,
        logger: logging.Logger,
    ) -> None:
        self.handlers = list(handlers)
        self.middlewares = list(middlewares)
        self.logger = logger
        self._tool_routes: dict[str, BaseHandler] = {}
        self._started = False
        self._last_tool_signature: tuple[str, str] | None = None
        self._repeated_tool_calls = 0

    @property
    def started(self) -> bool:
        return self._started

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
        started_middlewares: list[Middleware] = []
        try:
            for handler in self.handlers:
                await handler.startup()
                started_handlers.append(handler)
            self._tool_routes = self._build_tool_routes()
            for middleware in self._enabled_middlewares():
                await middleware.startup()
                started_middlewares.append(middleware)
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
            raise

        self._started = True

    async def shutdown(self) -> None:
        if not self._started:
            return

        errors: list[Exception] = []
        for middleware in reversed(self._enabled_middlewares()):
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
        for middleware in self._enabled_middlewares():
            await middleware.on_task_start()

    async def dispatch(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> StepOutcome:
        if not self._started:
            await self.startup()

        try:
            handler = self._tool_routes[tool_name]
        except KeyError as exc:
            available = ", ".join(sorted(self._tool_routes)) or "none"
            raise KeyError(
                f"unknown tool {tool_name!r}; available tools: {available}"
            ) from exc

        middleware_outcome = await self._run_middleware_hook(
            "before_tool_call",
            tool_name,
            arguments,
        )
        if middleware_outcome is not None:
            return middleware_outcome

        return await handler.dispatch(tool_name, arguments)

    async def execute_tool_calls(self, message: Any) -> ToolCallResult:
        result_messages: list[dict[str, Any]] = []
        function_calls = [
            tool_call
            for tool_call in message.tool_calls or []
            if tool_call.type == "function"
        ]

        for tool_call in function_calls:
            result = await self.execute_tool_call(tool_call)
            result_messages.extend(result.messages)
            if result.exit_value is not None:
                return ToolCallResult(
                    tuple(result_messages),
                    exit_value=result.exit_value,
                )
        return ToolCallResult(tuple(result_messages))

    async def execute_tool_call(self, tool_call: Any) -> ToolCallResult:
        tool_name = tool_call.function.name
        raw_arguments = tool_call.function.arguments
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
            outcome = await self.dispatch(tool_name, arguments)
            self.logger.info(
                "Tool %s completed exit=%s result=%s",
                tool_name,
                outcome.should_exit,
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
        if outcome.should_exit:
            return ToolCallResult((message,), exit_value=serialized)
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

    def _enabled_middlewares(self) -> list[Middleware]:
        return [
            middleware
            for middleware in self.middlewares
            if middleware.enabled
        ]

    async def _run_middleware_hook(
        self,
        hook_name: str,
        *args: Any,
    ) -> StepOutcome | None:
        for middleware in self._enabled_middlewares():
            hook = getattr(middleware, hook_name, None)
            if hook is None:
                continue
            outcome = await hook(*args)
            if outcome is None:
                continue
            if not isinstance(outcome, StepOutcome):
                raise TypeError(
                    f"{hook_name}() must return StepOutcome or None, "
                    f"got {type(outcome).__name__}"
                )
            return outcome
        return None

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

        if self._repeated_tool_calls >= MAX_REPEATED_TOOL_CALLS:
            raise RuntimeError(
                f"tool {tool_name!r} was called with the same arguments "
                f"{MAX_REPEATED_TOOL_CALLS} consecutive times"
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
