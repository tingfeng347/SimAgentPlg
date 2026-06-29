"""Composable stateful agents with tool handlers and MCP integration."""

from simagentplg.agent.base import BaseAgent, ModelConfig, StepOutcome
from simagentplg.agent.manager import (
    AgentAlreadyExistsError,
    AgentManager,
    AgentManagerError,
    AgentNotFoundError,
)
from simagentplg.agent.middleware import (
    BashApprovalMiddleware,
    HumanApproval,
    MiddleWare,
    ToolMiddleware,
    format_tool_call_preview,
)
from simagentplg.agent.workflow import (
    AgentWorkflow,
    WorkflowExecutionError,
    WorkflowResult,
    WorkflowStep,
    WorkflowStepResult,
)
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
    "StepOutcome",
    "AgentManager",
    "AgentManagerError",
    "AgentAlreadyExistsError",
    "AgentNotFoundError",
    "MiddleWare",
    "ToolMiddleware",
    "HumanApproval",
    "BashApprovalMiddleware",
    "format_tool_call_preview",
    "AgentWorkflow",
    "WorkflowStep",
    "WorkflowStepResult",
    "WorkflowResult",
    "WorkflowExecutionError",
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
