"""Composable local and external tool handlers."""

from simagentplg.handlers.base import (
    BaseHandler,
    MethodToolHandler,
    ToolDefinitionError,
    UnknownToolError,
)
from simagentplg.handlers.bash import BashHandler
from simagentplg.handlers.mcp import McpToolHandler

__all__ = [
    "BaseHandler",
    "MethodToolHandler",
    "ToolDefinitionError",
    "UnknownToolError",
    "BashHandler",
    "McpToolHandler",
]
