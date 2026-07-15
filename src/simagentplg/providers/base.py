from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

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
