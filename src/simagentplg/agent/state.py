from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum

from simagentplg.agent.types import AgentMessage


class AgentStatus(StrEnum):
    """Lifecycle status of the current agent task."""

    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(slots=True)
class AgentState:
    """Persistent conversation and current-task state for one agent."""

    messages: list[AgentMessage] = field(default_factory=list)
    task: str | None = None
    status: AgentStatus = AgentStatus.IDLE
    turn: int = 0
    no_tool_response_count: int = 0
    active_skill_name: str | None = None
    result: str | None = None
    error: str | None = None

    def reset(self, messages: list[AgentMessage]) -> None:
        """Replace conversation history and clear the current task state."""

        self.messages = [dict(message) for message in messages]
        self.task = None
        self.status = AgentStatus.IDLE
        self.turn = 0
        self.no_tool_response_count = 0
        self.active_skill_name = None
        self.result = None
        self.error = None

    def begin_task(self, task: str) -> None:
        """Start a new task while preserving the conversation history."""

        self.task = task
        self.status = AgentStatus.RUNNING
        self.turn = 0
        self.no_tool_response_count = 0
        self.active_skill_name = None
        self.result = None
        self.error = None
        self.messages.append({"role": "user", "content": task})

    def advance_turn(self) -> int:
        """Record and return the next model turn number."""

        self.turn += 1
        return self.turn

    def add_message(self, message: AgentMessage) -> None:
        """Append one conversation message."""

        self.messages.append(dict(message))

    def add_messages(self, messages: list[AgentMessage]) -> None:
        """Append multiple conversation messages."""

        self.messages.extend(dict(message) for message in messages)

    def complete(self, result: str) -> None:
        """Mark the current task as completed."""

        self.status = AgentStatus.COMPLETED
        self.result = result
        self.error = None

    def fail(self, error: Exception | str) -> None:
        """Mark the current task as failed without discarding its history."""

        self.status = AgentStatus.FAILED
        self.result = None
        self.error = str(error)

    def snapshot(self) -> "AgentState":
        """Return an independent copy suitable for observation or persistence."""

        return AgentState(
            messages=deepcopy(self.messages),
            task=self.task,
            status=self.status,
            turn=self.turn,
            no_tool_response_count=self.no_tool_response_count,
            active_skill_name=self.active_skill_name,
            result=self.result,
            error=self.error,
        )
