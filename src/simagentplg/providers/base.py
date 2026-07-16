from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeAlias

if TYPE_CHECKING:
    from simagentplg.agent.cancellation import CancellationToken
    from simagentplg.agent.context_builder import ContextBuildResult


@dataclass(frozen=True, slots=True)
class ModelToolCall:
    """Provider-neutral function call requested by an assistant message."""

    id: str
    name: str
    arguments: str

    def to_agent_message(self) -> dict[str, Any]:
        """Serialize the call into the current conversation message format."""

        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": self.arguments,
            },
        }


@dataclass(frozen=True, slots=True)
class AssistantMessage:
    """Provider-neutral assistant response consumed by the orchestrator."""

    content: str | None = None
    tool_calls: tuple[ModelToolCall, ...] = ()

    def to_agent_message(self) -> dict[str, Any]:
        """Serialize the response for persistent conversation state."""

        message: dict[str, Any] = {
            "role": "assistant",
            "content": self.content,
        }
        if self.tool_calls:
            message["tool_calls"] = [
                tool_call.to_agent_message()
                for tool_call in self.tool_calls
            ]
        return message


@dataclass(frozen=True, slots=True)
class ModelTextDelta:
    """One provider-neutral piece of assistant text."""

    delta: str

    def __post_init__(self) -> None:
        if not self.delta:
            raise ValueError("model text delta must not be empty")


@dataclass(frozen=True, slots=True)
class ModelThinkingDelta:
    """One provider-neutral piece of provisional model reasoning."""

    delta: str

    def __post_init__(self) -> None:
        if not self.delta:
            raise ValueError("model thinking delta must not be empty")


@dataclass(frozen=True, slots=True)
class ModelResponseCompleted:
    """Terminal stream event containing the normalized assistant message."""

    message: AssistantMessage


ModelStreamEvent: TypeAlias = (
    ModelTextDelta | ModelThinkingDelta | ModelResponseCompleted
)


class ModelAdapter(ABC):
    """Provider boundary used by the agent core."""

    async def startup(self) -> None:
        """Acquire optional provider resources."""

    async def shutdown(self) -> None:
        """Release optional provider resources."""

    @abstractmethod
    async def complete(
        self,
        context: "ContextBuildResult",
        *,
        cancellation: "CancellationToken | None" = None,
    ) -> AssistantMessage:
        """Return one complete response and honor the per-run cancellation."""

    async def stream(
        self,
        context: "ContextBuildResult",
        *,
        cancellation: "CancellationToken | None" = None,
    ) -> AsyncIterator[ModelStreamEvent]:
        """Adapt a complete-only provider into one terminal stream event."""

        message = await self.complete(
            context,
            cancellation=cancellation,
        )
        yield ModelResponseCompleted(message)
