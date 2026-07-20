import asyncio

from simagentplg.agent.events import (
    AgentEvent,
    AgentFinished,
    AgentStarted,
    CompactionCompleted,
    MessageCompleted,
    ToolCompleted,
)
from simagentplg.providers.base import serialize_assistant_message
from simagentplg.session.storage import SessionStorage
from simagentplg.session.types import AgentSession

_RECORDED_PAYLOADS = (
    AgentStarted,
    MessageCompleted,
    ToolCompleted,
    AgentFinished,
    CompactionCompleted,
)


class SessionRecorder:
    """Build a linear Agent Session from read-only lifecycle events."""

    def __init__(
        self,
        *,
        session_id: str,
        storage: SessionStorage,
    ) -> None:
        session_id = session_id.strip()
        if not session_id:
            raise ValueError("session_id must not be empty")
        self.session_id = session_id
        self.storage = storage
        self._lock = asyncio.Lock()

    async def emit(self, event: AgentEvent) -> None:
        payload = event.payload
        if not isinstance(payload, _RECORDED_PAYLOADS):
            return
        if isinstance(payload, CompactionCompleted) and not payload.result.completed:
            return

        async with self._lock:
            session = await self.storage.load(self.session_id)
            if session is None:
                session = AgentSession(session_id=self.session_id)
            session.bind_agent(event.agent_id)

            if isinstance(payload, AgentStarted):
                session.begin_run(event.run_id, payload.task, event.sequence)
            elif isinstance(payload, CompactionCompleted):
                assert payload.result.summary is not None
                session.apply_compaction(
                    payload.result.operation_id,
                    event.sequence,
                    payload.result.summary,
                    payload.result.messages,
                )
            elif isinstance(payload, MessageCompleted):
                session.append_message(
                    event.run_id,
                    event.sequence,
                    serialize_assistant_message(
                        payload.message,
                        usage=payload.usage,
                    ),
                )
            elif isinstance(payload, ToolCompleted):
                for message in payload.result.messages:
                    session.append_message(
                        event.run_id,
                        event.sequence,
                        message,
                    )
            else:
                session.finish_run(
                    event.run_id,
                    event.sequence,
                    payload.result,
                )

            await self.storage.save(session)

    async def load(self) -> AgentSession | None:
        """Load the currently persisted detached Session snapshot."""

        return await self.storage.load(self.session_id)
