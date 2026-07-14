from __future__ import annotations

import shlex
from collections.abc import Mapping
from typing import Any, Literal

from simagentplg.agent.types import StepOutcome
from simagentplg.middleware.approval import HumanApproval
from simagentplg.middleware.base import (
    ToolCallContext,
    ToolMiddleware,
    ToolNext,
    format_tool_call_preview,
)

BashApprovalPolicy = Literal["always", "unless_safe", "never"]
BASH_SAFE_COMMAND_PREFIXES = (
    ("pwd",),
    ("ls",),
    ("git", "status"),
    ("git", "diff"),
    ("git", "log"),
    ("rg",),
    ("sed", "-n"),
    ("cat",),
    ("python", "-m", "unittest"),
    ("python3", "-m", "unittest"),
    ("uv", "run", "python", "-m", "unittest"),
)
BASH_UNSAFE_SHELL_TOKENS = frozenset("|&;<>()`$")


class BashApprovalMiddleware(ToolMiddleware):
    """Require approval for bash_run according to an explicit review policy.

    This middleware is an approval gate, not a shell sandbox or security
    boundary. The safe-command policy is a conservative allowlist; commands
    that cannot be confidently parsed as safe still require review.
    """

    def __init__(
        self,
        approval: HumanApproval | None = None,
        *,
        approval_policy: BashApprovalPolicy = "unless_safe",
        name: str | None = None,
        enabled: bool = True,
    ) -> None:
        super().__init__(name=name, enabled=enabled)
        if approval_policy not in ("always", "unless_safe", "never"):
            raise ValueError(
                "approval_policy must be one of: always, unless_safe, never"
            )
        self.approval = approval or HumanApproval()
        self.approval_policy = approval_policy

    async def __call__(
        self,
        context: ToolCallContext,
        call_next: ToolNext,
    ) -> StepOutcome:
        if context.tool_name != "bash_run":
            return await call_next(context)

        review_reason = self._review_reason(context.arguments)
        if review_reason is None:
            return await call_next(context)

        approved = await self.approval.approve(
            format_tool_call_preview(
                context.tool_name,
                context.arguments,
                review=review_reason,
            )
        )
        if approved:
            return await call_next(context)

        return StepOutcome(
            {
                "status": "rejected",
                "tool": context.tool_name,
                "reason": "human rejected tool execution",
            },
            should_exit=True,
        )

    def _review_reason(self, arguments: Mapping[str, Any]) -> str | None:
        if self.approval_policy == "never":
            return None
        if self.approval_policy == "always":
            return "approval policy requires review for every bash_run command"

        code = arguments.get("code")
        if not isinstance(code, str):
            return None
        if _is_safe_bash_command(code):
            return None
        return "bash_run command is not in the safe command allowlist"


def _is_safe_bash_command(code: str) -> bool:
    code = code.strip()
    if not code:
        return False
    if any(token in code for token in BASH_UNSAFE_SHELL_TOKENS):
        return False
    if "\n" in code or "\r" in code:
        return False

    try:
        parts = shlex.split(code, comments=False, posix=True)
    except ValueError:
        return False
    if not parts:
        return False

    return any(_starts_with(parts, prefix) for prefix in BASH_SAFE_COMMAND_PREFIXES)


def _starts_with(parts: list[str], prefix: tuple[str, ...]) -> bool:
    return len(parts) >= len(prefix) and tuple(parts[: len(prefix)]) == prefix
