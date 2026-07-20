"""Linear Agent Session persistence built from lifecycle events."""

from simagentplg.session.codec import (
    SESSION_SCHEMA_VERSION,
    session_from_dict,
    session_to_dict,
)
from simagentplg.session.errors import (
    SessionError,
    SessionSerializationError,
    SessionStorageError,
)
from simagentplg.session.json_file import JsonFileSessionStorage
from simagentplg.session.memory import MemorySessionStorage
from simagentplg.session.recorder import SessionRecorder
from simagentplg.session.storage import SessionStorage
from simagentplg.session.types import (
    AgentSession,
    SessionCompaction,
    SessionMessage,
    SessionRun,
)

__all__ = [
    "AgentSession",
    "SessionMessage",
    "SessionRun",
    "SessionCompaction",
    "SessionStorage",
    "JsonFileSessionStorage",
    "MemorySessionStorage",
    "SessionRecorder",
    "SESSION_SCHEMA_VERSION",
    "session_to_dict",
    "session_from_dict",
    "SessionError",
    "SessionSerializationError",
    "SessionStorageError",
]
