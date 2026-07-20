from typing import Protocol, runtime_checkable

from simagentplg.session.journal import (
    DEFAULT_SESSION_BRANCH,
    SessionRecord,
    SessionRecordDraft,
)
from simagentplg.session.tree import SessionCheckout
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

    async def checkout(
        self,
        session_id: str,
        *,
        branch_id: str = DEFAULT_SESSION_BRANCH,
        record_id: str | None = None,
    ) -> SessionCheckout | None:
        """Project one branch head or an exact record."""

    async def append(
        self,
        draft: SessionRecordDraft,
        *,
        expected_head_id: str | None = None,
        check_head: bool = False,
    ) -> SessionRecord:
        """Atomically append one mutation and return its assigned envelope."""
