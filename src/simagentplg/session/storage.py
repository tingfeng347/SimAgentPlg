from typing import Protocol, runtime_checkable

from simagentplg.session.journal import SessionRecord, SessionRecordDraft
from simagentplg.session.types import AgentSession


class SessionStorage(Protocol):
    """Persistence boundary for detached Agent Session snapshots."""

    async def load(self, session_id: str) -> AgentSession | None:
        """Load a detached Session or return ``None`` when it does not exist."""

    async def save(self, session: AgentSession) -> None:
        """Create or replace one Session snapshot."""


@runtime_checkable
class SessionJournalStorage(SessionStorage, Protocol):
    """Storage capable of appending semantic Session journal records."""

    async def append(self, draft: SessionRecordDraft) -> SessionRecord:
        """Atomically append one mutation and return its assigned envelope."""
