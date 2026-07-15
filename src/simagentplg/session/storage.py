from typing import Protocol

from simagentplg.session.types import AgentSession


class SessionStorage(Protocol):
    """Persistence boundary for detached Agent Session snapshots."""

    async def load(self, session_id: str) -> AgentSession | None:
        """Load a detached Session or return ``None`` when it does not exist."""

    async def save(self, session: AgentSession) -> None:
        """Create or replace one Session snapshot."""
