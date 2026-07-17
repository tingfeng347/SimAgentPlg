from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from simagentplg.agent.cancellation import (
    CancellationSource,
    CancellationToken,
)
from simagentplg.agent.types import StepOutcome, ToolProgressReporter
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
        if config_path is None and manager is None:
            raise ValueError(
                "config_path is required when manager is not provided"
            )
        if config_path is not None and manager is not None:
            raise ValueError("provide either config_path or manager, not both")
        if manager is not None:
            self.manager = manager
        else:
            assert config_path is not None
            self.manager = McpServerManager(config_path)
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
        *,
        cancellation: CancellationToken | None = None,
        progress: ToolProgressReporter | None = None,
    ) -> StepOutcome:
        if not self.can_handle(tool_name):
            raise UnknownToolError(f"unknown MCP tool {tool_name!r}")
        token = cancellation or CancellationSource().token
        result = await token.run(
            self.manager.call_tool(tool_name, dict(arguments))
        )
        return StepOutcome(result)
