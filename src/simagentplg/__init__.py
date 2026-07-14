"""Composable stateful agents with tool handlers and MCP integration."""

from simagentplg.agent.base import BaseAgent, ModelConfig
from simagentplg.agent.context_builder import AgentContextBuilder, ContextBuildResult
from simagentplg.middleware import (
    BashApprovalMiddleware,
    HumanApproval,
    Middleware,
    ToolMiddleware,
    format_tool_call_preview,
)
from simagentplg.agent.types import StepOutcome
from simagentplg.agent.state import AgentState, AgentStatus
from simagentplg.handlers import (
    BaseHandler,
    BashHandler,
    FinishHandler,
    GitDiffHandler,
    McpToolHandler,
    MethodToolHandler,
    ToolDefinitionError,
    UnknownToolError,
)
from simagentplg.plugins.mcp.mcp_manager import McpServerManager
from simagentplg.plugins.skill.skill_manager import SkillManager
from simagentplg.resources import DEFAULT_MCP_CONFIG, DEFAULT_SKILLS_DIR

__all__ = [
    "BaseAgent",
    "ModelConfig",
    "AgentState",
    "AgentStatus",
    "AgentContextBuilder",
    "ContextBuildResult",
    "StepOutcome",
    "Middleware",
    "ToolMiddleware",
    "HumanApproval",
    "BashApprovalMiddleware",
    "format_tool_call_preview",
    "BaseHandler",
    "MethodToolHandler",
    "BashHandler",
    "FinishHandler",
    "GitDiffHandler",
    "McpToolHandler",
    "ToolDefinitionError",
    "UnknownToolError",
    "McpServerManager",
    "SkillManager",
    "DEFAULT_MCP_CONFIG",
    "DEFAULT_SKILLS_DIR",
]
