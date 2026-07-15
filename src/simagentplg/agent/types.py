from dataclasses import dataclass
from enum import StrEnum
from typing import Any


AgentMessage = dict[str, Any]


class ToolControl(StrEnum):
    """Control signal returned by a tool independently of its payload."""

    CONTINUE = "continue"
    COMPLETE = "complete"
    REJECT = "reject"
    CANCEL = "cancel"


@dataclass(slots=True)
class StepOutcome:
    """Normalized result returned by every tool handler."""

    data: Any
    control: ToolControl = ToolControl.CONTINUE


@dataclass(frozen=True, slots=True)
class ToolCallResult:
    """Normalized result of one model-requested tool execution."""

    messages: tuple[AgentMessage, ...]
    control: ToolControl = ToolControl.CONTINUE
    output: str | None = None
    error: str | None = None
