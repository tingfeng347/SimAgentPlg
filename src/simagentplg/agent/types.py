from dataclasses import dataclass
from typing import Any


AgentMessage = dict[str, Any]


@dataclass(slots=True)
class StepOutcome:
    """Normalized result returned by every tool handler."""

    data: Any
    should_exit: bool = False
