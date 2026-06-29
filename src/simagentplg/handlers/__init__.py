"""Composable local and external tool handlers."""

from simagentplg.handlers.base import (
    BaseHandler,
    MethodToolHandler,
    ToolDefinitionError,
    UnknownToolError,
)
from simagentplg.handlers.bash import BashHandler
from simagentplg.handlers.finish import FinishHandler
from simagentplg.handlers.gitdiff import GitDiffHandler
from simagentplg.handlers.mcp import McpToolHandler

__all__ = [
    "BaseHandler",
    "MethodToolHandler",
    "ToolDefinitionError",
    "UnknownToolError",
    "BashHandler",
    "FinishHandler",
    "GitDiffHandler",
    "McpToolHandler",
]
