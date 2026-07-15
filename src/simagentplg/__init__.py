"""Composable stateful agents with tool handlers and MCP integration."""

from simagentplg.agent.base import BaseAgent
from simagentplg.agent.context_builder import AgentContextBuilder, ContextBuildResult
from simagentplg.agent.events import (
    AgentEvent,
    AgentEventKind,
    AgentEventPayload,
    AgentEventSink,
    AgentFinished,
    AgentStarted,
    MessageCompleted,
    ToolCompleted,
    ToolStarted,
    TurnCompleted,
    TurnStarted,
)
from simagentplg.agent.orchestrator import AgentOrchestrator
from simagentplg.agent.result import (
    AgentRunError,
    AgentRunResult,
    RunStatus,
    StopReason,
)
from simagentplg.agent.runtime_policy import RuntimePolicy
from simagentplg.middleware import (
    Middleware,
    ToolCallContext,
    ToolMiddleware,
    ToolNext,
    compose_tool_middlewares,
    format_tool_call_preview,
)
from simagentplg.agent.types import StepOutcome, ToolCallResult, ToolControl
from simagentplg.agent.state import AgentState, AgentStatus
from simagentplg.handlers import (
    BaseHandler,
    McpToolHandler,
    MethodToolHandler,
    ToolDefinitionError,
    UnknownToolError,
)
from simagentplg.plugins.mcp.mcp_manager import McpServerManager
from simagentplg.plugins.skill.skill_manager import SkillManager
from simagentplg.providers import (
    AssistantMessage,
    ModelAdapter,
    ModelConfig,
    ModelToolCall,
    OpenAIModelAdapter,
)

__all__ = [
    "BaseAgent",
    "ModelConfig",
    "ModelAdapter",
    "OpenAIModelAdapter",
    "AssistantMessage",
    "ModelToolCall",
    "AgentState",
    "AgentStatus",
    "AgentContextBuilder",
    "ContextBuildResult",
    "AgentEvent",
    "AgentEventKind",
    "AgentEventPayload",
    "AgentEventSink",
    "AgentStarted",
    "TurnStarted",
    "MessageCompleted",
    "ToolStarted",
    "ToolCompleted",
    "TurnCompleted",
    "AgentFinished",
    "AgentOrchestrator",
    "RuntimePolicy",
    "AgentRunResult",
    "AgentRunError",
    "RunStatus",
    "StopReason",
    "StepOutcome",
    "ToolCallResult",
    "ToolControl",
    "Middleware",
    "ToolMiddleware",
    "ToolCallContext",
    "ToolNext",
    "compose_tool_middlewares",
    "format_tool_call_preview",
    "BaseHandler",
    "MethodToolHandler",
    "McpToolHandler",
    "ToolDefinitionError",
    "UnknownToolError",
    "McpServerManager",
    "SkillManager",
]
