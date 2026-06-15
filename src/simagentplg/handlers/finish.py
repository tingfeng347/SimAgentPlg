import asyncio
import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from simagentplg.agent.base import StepOutcome
from simagentplg.handlers.base import MethodToolHandler, ToolSchema
from simagentplg.logger import get_logger

logger = get_logger("FINISHHANDLER")



FINISH_TOOL: ToolSchema = {
    "type": "function",
    "function": {
        "name": "run_finish",
        "description": (
            "任务完成后调用。提交完成总结，返回本次任务产生的 Git 文件变化，"
            "并结束当前任务。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "任务完成情况的简明总结",
                },
            },
            "required": ["summary"],
        },
    },
}


@dataclass(frozen=True, slots=True)
class _GitSnapshot:
    repository: Path
    tracked: frozenset[str]
    status: dict[str, str]
    fingerprints: dict[str, str | None]


async def _run_git(cwd: str, *arguments: str) -> tuple[int, bytes]:
    process = await asyncio.create_subprocess_exec(
        "git",
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    stdout, _ = await process.communicate()
    return (
        process.returncode if process.returncode is not None else -1,
        stdout,
    )


def _parse_git_status(output: bytes) -> dict[str, str]:
    entries = output.split(b"\0")
    status: dict[str, str] = {}
    index = 0

    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue

        text = entry.decode("utf-8", errors="surrogateescape")
        code = text[:2]
        path = text[3:]
        status[path] = code

        if "R" in code or "C" in code:
            if index < len(entries) and entries[index]:
                old_path = entries[index].decode(
                    "utf-8",
                    errors="surrogateescape",
                )
                status[old_path] = "D "
                index += 1

    return status


def _fingerprint(path: Path) -> str | None:
    try:
        if path.is_symlink():
            return hashlib.sha256(
                os.readlink(path).encode("utf-8", errors="surrogateescape")
            ).hexdigest()
        if not path.is_file():
            return None

        digest = hashlib.sha256()
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(64 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


async def _capture_git_snapshot(cwd: str) -> _GitSnapshot:
    return_code, root_output = await _run_git(
        cwd,
        "rev-parse",
        "--show-toplevel",
    )
    if return_code != 0:
        raise RuntimeError("working directory is not inside a Git repository")

    repository = Path(
        root_output.decode("utf-8", errors="replace").strip()
    )
    return_code, tracked_output = await _run_git(
        str(repository),
        "ls-files",
        "-z",
    )
    if return_code != 0:
        raise RuntimeError("failed to list tracked Git files")

    return_code, status_output = await _run_git(
        str(repository),
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    )
    if return_code != 0:
        raise RuntimeError("failed to read Git working tree status")

    tracked = frozenset(
        path.decode("utf-8", errors="surrogateescape")
        for path in tracked_output.split(b"\0")
        if path
    )
    status = _parse_git_status(status_output)
    fingerprints = {
        path: _fingerprint(repository / path)
        for path in status
    }
    return _GitSnapshot(repository, tracked, status, fingerprints)


def _classify_changes(
    before: _GitSnapshot,
    after: _GitSnapshot,
) -> dict[str, list[str]]:
    added: set[str] = set()
    modified: set[str] = set()
    deleted: set[str] = set()

    for path, code in after.status.items():
        before_code = before.status.get(path)
        after_fingerprint = after.fingerprints.get(path)

        if before_code is None:
            if path not in before.tracked:
                added.add(path)
            elif "D" in code:
                deleted.add(path)
            else:
                modified.add(path)
            continue

        if before.fingerprints.get(path) == after_fingerprint:
            continue
        if "D" in code or after_fingerprint is None:
            deleted.add(path)
        elif path not in before.tracked and before_code == "??":
            modified.add(path)
        else:
            modified.add(path)

    for path in before.status:
        if path in after.status:
            continue
        current_fingerprint = _fingerprint(after.repository / path)
        if before.fingerprints.get(path) == current_fingerprint:
            continue
        if current_fingerprint is None:
            deleted.add(path)
        else:
            modified.add(path)

    return {
        "added": sorted(added),
        "modified": sorted(modified),
        "deleted": sorted(deleted),
    }


class FinishHandler(MethodToolHandler):
    """Built-in task completion handler with Git change reporting."""

    def __init__(self, *, cwd: str | None = None) -> None:
        super().__init__((FINISH_TOOL,))
        self.cwd = cwd or os.getcwd()
        self._task_snapshot: _GitSnapshot | None = None
        self._snapshot_error: str | None = None

    async def on_task_start(self) -> None:
        try:
            self._task_snapshot = await _capture_git_snapshot(self.cwd)
            self._snapshot_error = None
        except Exception as exc:
            self._task_snapshot = None
            self._snapshot_error = str(exc)

    async def do_run_finish(
        self,
        arguments: Mapping[str, Any],
    ) -> StepOutcome:
        summary = arguments.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            return StepOutcome(
                {
                    "status": "error",
                    "error": "summary must be a non-empty string",
                }
            )

        if self._task_snapshot is None:
            changes: dict[str, Any] = {
                "available": False,
                "reason": self._snapshot_error or "Git snapshot unavailable",
                "added": [],
                "modified": [],
                "deleted": [],
            }
        else:
            try:
                current = await _capture_git_snapshot(self.cwd)
                if current.repository != self._task_snapshot.repository:
                    raise RuntimeError("Git repository changed during the task")
                changes = {
                    "available": True,
                    "repository": str(current.repository),
                    **_classify_changes(self._task_snapshot, current),
                }
            except Exception as exc:
                changes = {
                    "available": False,
                    "reason": str(exc),
                    "added": [],
                    "modified": [],
                    "deleted": [],
                }
        logger.info("任务完成，summary=%s, changes=%s", summary[:80], bool(changes.get("available")))
        return StepOutcome(
            {
                "summary": summary.strip(),
                "changes": changes,
            },
            should_exit=True,
        )
