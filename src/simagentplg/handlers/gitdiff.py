import asyncio
import os
from collections.abc import Mapping
from typing import Any

from simagentplg.agent.types import StepOutcome
from simagentplg.handlers.base import MethodToolHandler, ToolSchema
from simagentplg.logger import get_logger

logger = get_logger("GITDIFFHANDLER")

GITDIFF_TOOL: ToolSchema = {
    "type": "function",
    "function": {
        "name": "run_gitdiff",
        "description": (
            "Inspect Git working-tree changes before finishing a task. "
            "This tool can return status, diff stats, or the full diff. "
            "Calling it does not finish the task."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["status", "stat", "diff"],
                    "default": "status",
                    "description": (
                        "Change report mode: status returns git status --short; "
                        "stat returns git diff --stat; diff returns git diff."
                    ),
                },
            },
            "additionalProperties": False,
        },
    },
}

_GITDIFF_COMMANDS: dict[str, tuple[str, ...]] = {
    "status": ("status", "--short"),
    "stat": ("diff", "--stat"),
    "diff": ("diff",),
}


async def _run_git(
    cwd: str,
    arguments: tuple[str, ...],
) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        "git",
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    stdout, stderr = await process.communicate()
    return_code = process.returncode if process.returncode is not None else -1
    return (
        return_code,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


class GitDiffHandler(MethodToolHandler):
    """Built-in Git working-tree inspection handler."""

    def __init__(self, *, cwd: str | None = None) -> None:
        super().__init__((GITDIFF_TOOL,))
        self.cwd = cwd or os.getcwd()

    async def do_run_gitdiff(
        self,
        arguments: Mapping[str, Any],
    ) -> StepOutcome:
        mode = arguments.get("mode", "status")
        if not isinstance(mode, str) or mode not in _GITDIFF_COMMANDS:
            return StepOutcome(
                {
                    "status": "error",
                    "error": "mode must be one of: status, stat, diff",
                }
            )

        command = _GITDIFF_COMMANDS[mode]
        return_code, stdout, stderr = await _run_git(self.cwd, command)
        command_text = "git " + " ".join(command)
        if return_code != 0:
            return StepOutcome(
                {
                    "status": "error",
                    "mode": mode,
                    "command": command_text,
                    "error": stderr.strip() or stdout.strip(),
                }
            )

        logger.info("Git diff completed mode=%s", mode)
        return StepOutcome(
            {
                "status": "success",
                "mode": mode,
                "command": command_text,
                "output": stdout,
            }
        )
