from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from simagentplg.agent.types import StepOutcome
from simagentplg.handlers.base import BaseHandler, ToolSchema, UnknownToolError
from simagentplg.plugins.mcp.mcp_manager import McpServerManager


class McpToolHandler(BaseHandler):
    """Expose tools from configured MCP servers through the handler API."""

    def __init__(
        self,
        config_path: str | Path | None = None,
        *,
        manager: Any | None = None,
    ) -> None:
        self.manager = manager or McpServerManager(
            Path(config_path) if config_path is not None else None
        )
        self._tools: tuple[ToolSchema, ...] = ()
        self._started = False

    @property
    def tools(self) -> Sequence[ToolSchema]:
        return self._tools

    async def startup(self) -> None:
        if self._started:
            return
        await self.manager.startup()
        self._tools = tuple(self.manager.get_openai_tools())
        self._started = True

    async def shutdown(self) -> None:
        if not self._started:
            return
        await self.manager.shutdown()
        self._tools = ()
        self._started = False

    async def dispatch(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> StepOutcome:
        if not self.can_handle(tool_name):
            raise UnknownToolError(f"unknown MCP tool {tool_name!r}")
        result = await self.manager.call_tool(tool_name, dict(arguments))
        return StepOutcome(result)
