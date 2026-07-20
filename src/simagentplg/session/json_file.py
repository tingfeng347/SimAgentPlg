from __future__ import annotations

import asyncio
import json
import os
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Any

from simagentplg.session.codec import session_from_dict, session_to_dict
from simagentplg.session.errors import (
    SessionSerializationError,
    SessionStorageError,
)
from simagentplg.session.types import AgentSession


class JsonFileSessionStorage:
    """Persist versioned Session documents as atomic JSON file replacements.

    Separate instances and processes can load the same completed snapshot.
    Concurrent writers to one Session are intentionally not coordinated; the
    last successful atomic replacement wins.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser()
        self._lock = asyncio.Lock()

    async def load(self, session_id: str) -> AgentSession | None:
        normalized_id = self._normalize_session_id(session_id)
        path = self._path_for(normalized_id)
        async with self._lock:
            return await asyncio.to_thread(
                self._load_sync,
                path,
                normalized_id,
            )

    async def save(self, session: AgentSession) -> None:
        snapshot = session.snapshot()
        path = self._path_for(snapshot.session_id)
        payload = session_to_dict(snapshot)
        async with self._lock:
            await asyncio.to_thread(self._save_sync, path, payload)

    @staticmethod
    def _normalize_session_id(session_id: str) -> str:
        if not isinstance(session_id, str):
            raise TypeError("session_id must be a string")
        normalized = session_id.strip()
        if not normalized:
            raise ValueError("session_id must not be empty")
        return normalized

    def _path_for(self, session_id: str) -> Path:
        digest = sha256(session_id.encode("utf-8")).hexdigest()
        return self.root / f"{digest}.json"

    @staticmethod
    def _load_sync(path: Path, expected_session_id: str) -> AgentSession | None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except json.JSONDecodeError as exc:
            raise SessionSerializationError(
                f"session file {path} contains invalid JSON: {exc.msg}"
            ) from exc
        except OSError as exc:
            raise SessionStorageError(f"failed to read session file {path}") from exc
        if not isinstance(raw, dict):
            raise SessionSerializationError(
                f"session file {path} must contain a JSON object"
            )
        session = session_from_dict(raw)
        if session.session_id != expected_session_id:
            raise SessionSerializationError(
                f"session file {path} contains id {session.session_id!r}, "
                f"expected {expected_session_id!r}"
            )
        return session

    def _save_sync(self, path: Path, payload: dict[str, Any]) -> None:
        try:
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise SessionSerializationError(
                f"session is not JSON-compatible: {exc}"
            ) from exc

        temporary_path: Path | None = None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                dir=self.root,
                prefix=f".{path.stem}.",
                suffix=".tmp",
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(encoded)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, path)
            temporary_path = None
            self._fsync_directory()
        except OSError as exc:
            raise SessionStorageError(
                f"failed to atomically save session file {path}"
            ) from exc
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _fsync_directory(self) -> None:
        try:
            descriptor = os.open(self.root, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)
