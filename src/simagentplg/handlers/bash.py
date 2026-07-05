import asyncio
import os
from collections.abc import Mapping
from typing import Any

from simagentplg.agent.types import StepOutcome
from simagentplg.handlers.base import MethodToolHandler, ToolSchema
from simagentplg.logger import get_logger

logger = get_logger("BASHHANDLER")

BASH_TOOL: ToolSchema = {
    "type": "function",
    "function": {
        "name": "bash_run",
        "description": (
            "在 Bash 环境中执行脚本，返回 stdout、退出码和状态。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "要执行的 Bash 脚本，支持多行",
                },
                "timeout": {
                    "type": "integer",
                    "description": "最长等待秒数",
                    "minimum": 1,
                },
            },
            "required": ["code"],
        },
    },
}

async def run_bash(
    code: str,
    *,
    timeout: int = 60,
    cwd: str | None = None,
    max_output: int = 10_000,
) -> dict[str, Any]:
    """Execute Bash asynchronously and return a bounded structured result."""

    try:
        process = await asyncio.create_subprocess_exec(
            "bash",
            "-c",
            code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        stdout, _ = await process.communicate()
        return {
            "status": "error",
            "stdout": _decode_output(stdout)[-max_output:],
            "error": f"command timed out after {timeout} seconds",
            "exit_code": -1,
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc), "exit_code": -1}

    exit_code = process.returncode if process.returncode is not None else -1
    return {
        "status": "success" if exit_code == 0 else "error",
        "stdout": _decode_output(stdout)[-max_output:],
        "exit_code": exit_code,
    }


def _decode_output(output: bytes) -> str:
    try:
        return output.decode("utf-8")
    except UnicodeDecodeError:
        return output.decode("gbk", errors="replace")


class BashHandler(MethodToolHandler):
    """Built-in atomic handler for bounded Bash execution."""

    def __init__(
        self,
        *,
        cwd: str | None = None,
        default_timeout: int = 60,
        max_output: int = 10_000,
    ) -> None:
        if default_timeout <= 0:
            raise ValueError("default_timeout must be greater than zero")
        if max_output <= 0:
            raise ValueError("max_output must be greater than zero")

        super().__init__((BASH_TOOL,))
        self.cwd = cwd or os.getcwd()
        self.default_timeout = default_timeout
        self.max_output = max_output

    async def do_bash_run(
        self,
        arguments: Mapping[str, Any],
    ) -> StepOutcome:
        code = arguments.get("code")
        if not isinstance(code, str) or not code.strip():
            return StepOutcome(
                {
                    "status": "error",
                    "error": "code must be a non-empty string",
                    "exit_code": -1,
                }
            )

        timeout = arguments.get("timeout", self.default_timeout)
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            return StepOutcome(
                {
                    "status": "error",
                    "error": "timeout must be a positive integer",
                    "exit_code": -1,
                }
            )

        logger.info("执行 bash_run, timeout=%d, cwd=%s", timeout, self.cwd)
        result = await run_bash(
            code,
            timeout=timeout,
            cwd=self.cwd,
            max_output=self.max_output,
        )
        return StepOutcome(result)
