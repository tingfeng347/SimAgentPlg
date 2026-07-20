from __future__ import annotations

import asyncio
import json
import os
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from simagentplg.session.errors import (
    SessionSerializationError,
    SessionStorageError,
)
from simagentplg.session.journal import (
    DEFAULT_SESSION_BRANCH,
    SessionRecord,
    SessionRecordDraft,
    apply_session_record,
)
from simagentplg.session.types import AgentSession


class JsonlSessionStorage:
    """Append immutable, tree-addressable Session records to JSONL journals.

    Version 1 writes only the ``main`` branch. The record envelope already
    carries parent and branch identity so future forks do not require a file
    format migration. Concurrent writers from separate processes are not yet
    coordinated.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser()
        self._lock = asyncio.Lock()

    async def load(self, session_id: str) -> AgentSession | None:
        normalized_id = self._normalize_session_id(session_id)
        path = self._path_for(normalized_id)
        async with self._lock:
            _, session, _ = await asyncio.to_thread(
                self._read_sync,
                path,
                normalized_id,
            )
            return session.snapshot() if session is not None else None

    async def save(self, session: AgentSession) -> None:
        """Append a full logical Checkpoint for imports and explicit saves."""

        await self.append(SessionRecordDraft.checkpoint(session.snapshot()))

    async def append(self, draft: SessionRecordDraft) -> SessionRecord:
        if draft.branch_id != DEFAULT_SESSION_BRANCH:
            raise ValueError("JSONL journal v1 only supports the main branch")
        path = self._path_for(draft.session_id)
        async with self._lock:
            return await asyncio.to_thread(self._append_sync, path, draft)

    async def records(self, session_id: str) -> tuple[SessionRecord, ...]:
        """Return detached immutable records for audit and tree projection."""

        normalized_id = self._normalize_session_id(session_id)
        path = self._path_for(normalized_id)
        async with self._lock:
            records, _, _ = await asyncio.to_thread(
                self._read_sync,
                path,
                normalized_id,
            )
            return tuple(records)

    @staticmethod
    def _normalize_session_id(session_id: str) -> str:
        if not isinstance(session_id, str):
            raise TypeError("session_id must be a string")
        normalized = session_id.strip()
        if not normalized:
            raise ValueError("session_id must not be empty")
        return normalized

    def _path_for(self, session_id: str) -> Path:
        normalized = self._normalize_session_id(session_id)
        digest = sha256(normalized.encode("utf-8")).hexdigest()
        return self.root / f"{digest}.jsonl"

    def _append_sync(
        self,
        path: Path,
        draft: SessionRecordDraft,
    ) -> SessionRecord:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SessionStorageError(
                f"failed to create Session journal directory {self.root}"
            ) from exc
        records, session, valid_length = self._read_sync(path, draft.session_id)
        previous = records[-1] if records else None
        record = SessionRecord(
            record_id=uuid4().hex,
            parent_id=previous.record_id if previous is not None else None,
            branch_id=draft.branch_id,
            revision=(previous.revision + 1 if previous is not None else 1),
            session_id=draft.session_id,
            agent_id=draft.agent_id,
            sequence=draft.sequence,
            kind=draft.kind,
            data=draft.data,
        )
        if (
            session is not None
            and session.agent_id is not None
            and record.agent_id != session.agent_id
        ):
            raise SessionSerializationError(
                f"Session {record.session_id!r} belongs to agent "
                f"{session.agent_id!r}, not {record.agent_id!r}"
            )
        apply_session_record(session, record)
        try:
            line = (
                json.dumps(
                    record.to_dict(),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                    allow_nan=False,
                )
                + "\n"
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise SessionSerializationError(
                f"Session record is not JSON-compatible: {exc}"
            ) from exc

        descriptor: int | None = None
        try:
            descriptor = os.open(
                path,
                os.O_RDWR | os.O_CREAT | os.O_APPEND,
                0o600,
            )
            current_size = os.fstat(descriptor).st_size
            if valid_length < current_size:
                os.ftruncate(descriptor, valid_length)
            os.lseek(descriptor, 0, os.SEEK_END)
            written = os.write(descriptor, line)
            if written != len(line):
                raise OSError(
                    f"partial Session journal write: {written}/{len(line)} bytes"
                )
            os.fsync(descriptor)
        except OSError as exc:
            raise SessionStorageError(
                f"failed to append Session journal {path}"
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
        self._fsync_directory()
        return record

    def _read_sync(
        self,
        path: Path,
        expected_session_id: str,
    ) -> tuple[list[SessionRecord], AgentSession | None, int]:
        try:
            content = path.read_bytes()
        except FileNotFoundError:
            return [], None, 0
        except OSError as exc:
            raise SessionStorageError(f"failed to read Session journal {path}") from exc

        records: list[SessionRecord] = []
        record_ids: set[str] = set()
        session: AgentSession | None = None
        valid_length = 0
        lines = content.splitlines(keepends=True)
        for index, encoded_line in enumerate(lines):
            line_number = index + 1
            if not encoded_line.endswith(b"\n"):
                if records and index == len(lines) - 1:
                    break
                raise SessionSerializationError(
                    f"Session journal {path} has an incomplete first record"
                )
            try:
                raw: Any = json.loads(encoded_line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SessionSerializationError(
                    f"Session journal {path} has invalid JSON at line {line_number}"
                ) from exc
            if not isinstance(raw, dict):
                raise SessionSerializationError(
                    f"Session journal {path} line {line_number} must be an object"
                )
            record = SessionRecord.from_dict(raw)
            if record.record_id in record_ids:
                raise SessionSerializationError(
                    f"Session journal {path} repeats record_id "
                    f"{record.record_id!r} at line {line_number}"
                )
            self._validate_record_chain(
                record,
                previous=records[-1] if records else None,
                expected_session_id=expected_session_id,
                path=path,
                line_number=line_number,
            )
            try:
                if (
                    session is not None
                    and session.agent_id is not None
                    and record.agent_id != session.agent_id
                ):
                    raise SessionSerializationError(
                        f"Session journal {path} changes agent_id at line {line_number}"
                    )
                session = apply_session_record(session, record)
            except (KeyError, TypeError, ValueError) as exc:
                if isinstance(exc, SessionSerializationError):
                    raise
                raise SessionSerializationError(
                    f"invalid Session mutation at {path}:{line_number}: {exc}"
                ) from exc
            records.append(record)
            record_ids.add(record.record_id)
            valid_length += len(encoded_line)
        return records, session, valid_length

    @staticmethod
    def _validate_record_chain(
        record: SessionRecord,
        *,
        previous: SessionRecord | None,
        expected_session_id: str,
        path: Path,
        line_number: int,
    ) -> None:
        if record.session_id != expected_session_id:
            raise SessionSerializationError(
                f"Session journal {path} line {line_number} contains id "
                f"{record.session_id!r}, expected {expected_session_id!r}"
            )
        if record.branch_id != DEFAULT_SESSION_BRANCH:
            raise SessionSerializationError(
                "JSONL journal v1 only supports the main branch"
            )
        expected_revision = previous.revision + 1 if previous is not None else 1
        expected_parent = previous.record_id if previous is not None else None
        if record.revision != expected_revision:
            raise SessionSerializationError(
                f"Session journal revision jumped at {path}:{line_number}"
            )
        if record.parent_id != expected_parent:
            raise SessionSerializationError(
                f"Session journal parent changed at {path}:{line_number}"
            )

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
