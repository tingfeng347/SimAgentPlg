"""Model provider adapters for the agent core."""

from simagentplg.providers.base import (
    AssistantMessage,
    ContextOverflowError,
    ModelAdapter,
    ModelAuthenticationError,
    ModelErrorKind,
    ModelProviderError,
    ModelRateLimitError,
    ModelResponseCompleted,
    ModelStreamEvent,
    ModelTextDelta,
    ModelThinkingDelta,
    ModelTimeoutError,
    ModelToolCall,
    ModelUsage,
)
from simagentplg.providers.openai import ModelConfig, OpenAIModelAdapter

__all__ = [
    "AssistantMessage",
    "ModelErrorKind",
    "ModelProviderError",
    "ContextOverflowError",
    "ModelRateLimitError",
    "ModelTimeoutError",
    "ModelAuthenticationError",
    "ModelAdapter",
    "ModelStreamEvent",
    "ModelTextDelta",
    "ModelThinkingDelta",
    "ModelResponseCompleted",
    "ModelToolCall",
    "ModelUsage",
    "ModelConfig",
    "OpenAIModelAdapter",
]
