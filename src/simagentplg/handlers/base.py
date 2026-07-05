from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any

from simagentplg.agent.types import StepOutcome

ToolSchema = dict[str, Any]


class ToolDefinitionError(ValueError):
    """Raised when a handler exposes an invalid tool definition."""


class UnknownToolError(KeyError):
    """Raised when a handler is asked to execute an unknown tool."""


class BaseHandler(ABC):
    """Interface implemented by reusable groups of related tools."""

    @property
    @abstractmethod
    def tools(self) -> Sequence[ToolSchema]:
        """Return tool definitions in OpenAI function-calling format."""

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(self._tool_name(tool) for tool in self.tools)

    async def startup(self) -> None:
        """Initialize optional external resources."""

    async def shutdown(self) -> None:
        """Release optional external resources."""

    async def on_task_start(self) -> None:
        """Prepare handler state for one new agent task."""

    def can_handle(self, tool_name: str) -> bool:
        return tool_name in self.tool_names

    @abstractmethod
    async def dispatch(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> StepOutcome:
        """Execute a registered tool."""

    @staticmethod
    def _tool_name(tool: ToolSchema) -> str:
        try:
            tool_type = tool["type"]
            function = tool["function"]
            name = function["name"]
        except (KeyError, TypeError) as exc:
            raise ToolDefinitionError(
                "tool must contain type and function.name"
            ) from exc

        if tool_type != "function" or not isinstance(name, str) or not name:
            raise ToolDefinitionError(
                "only named function tools are supported"
            )
        return name


class MethodToolHandler(BaseHandler):
    """Handler that maps tool names to async ``do_<tool_name>`` methods."""

    def __init__(self, tools: Sequence[ToolSchema]) -> None:
        self._tools = tuple(tools)
        names = self.tool_names
        if len(names) != len(set(names)):
            raise ToolDefinitionError("handler contains duplicate tool names")

    @property
    def tools(self) -> Sequence[ToolSchema]:
        return self._tools

    async def dispatch(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> StepOutcome:
        if not self.can_handle(tool_name):
            raise UnknownToolError(f"unknown tool {tool_name!r}")

        method = getattr(self, f"do_{tool_name}", None)
        if method is None:
            raise ToolDefinitionError(
                f"{type(self).__name__} must define do_{tool_name}()"
            )

        outcome = await method(dict(arguments))
        if not isinstance(outcome, StepOutcome):
            raise TypeError(
                f"do_{tool_name}() must return StepOutcome, "
                f"got {type(outcome).__name__}"
            )
        return outcome
