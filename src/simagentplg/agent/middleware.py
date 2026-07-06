from __future__ import annotations

import json
import shlex
from collections.abc import Mapping
from typing import Any, Literal

from simagentplg.agent.types import StepOutcome


DEFAULT_APPROVAL_PREVIEW_CHARS = 2_000
BashApprovalPolicy = Literal["always", "unless_safe", "on_review_hint", "never"]
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


class MiddleWare:
    """Base class for reusable agent middleware."""

    def __init__(self, *, name: str | None = None, enabled: bool = True) -> None:
        self.name = name or type(self).__name__
        self.enabled = enabled

    async def startup(self) -> None:
        """Initialize optional middleware resources."""

    async def shutdown(self) -> None:
        """Release optional middleware resources."""

    async def on_task_start(self) -> None:
        """Prepare middleware state for one new agent task."""


class ToolMiddleware(MiddleWare):
    """Middleware hook that runs before a tool handler is dispatched."""

    async def before_tool_call(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> StepOutcome | None:
        """Return None to allow execution, or StepOutcome to short-circuit."""

        return None


class HumanApproval:
    """Console based y/n approval helper reusable by middleware."""

    def __init__(
        self,
        *,
        max_preview_chars: int = DEFAULT_APPROVAL_PREVIEW_CHARS,
    ) -> None:
        if max_preview_chars <= 0:
            raise ValueError("max_preview_chars must be greater than zero")
        self.max_preview_chars = max_preview_chars

    async def approve(self, text: str) -> bool:
        preview = self.truncate(text)
        print(preview)
        while True:
            answer = input("Approve tool execution? [y/n]: ").strip().lower()
            if answer == "y":
                return True
            if answer == "n":
                return False
            print("Please enter 'y' or 'n'.")

    def truncate(self, text: str) -> str:
        if len(text) <= self.max_preview_chars:
            return text
        omitted = len(text) - self.max_preview_chars
        return f"{text[:self.max_preview_chars]}...<truncated {omitted} chars>"


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
        if approval_policy == "on_review_hint":
            approval_policy = "unless_safe"
        if approval_policy not in ("always", "unless_safe", "never"):
            raise ValueError(
                "approval_policy must be one of: always, unless_safe, never"
            )
        self.approval = approval or HumanApproval()
        self.approval_policy = approval_policy

    async def before_tool_call(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> StepOutcome | None:
        if tool_name != "bash_run":
            return None

        review_reason = self._review_reason(arguments)
        if review_reason is None:
            return None

        approved = await self.approval.approve(
            format_tool_call_preview(
                tool_name,
                arguments,
                review=review_reason,
            )
        )
        if approved:
            return None

        return StepOutcome(
            {
                "status": "rejected",
                "tool": tool_name,
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


def format_tool_call_preview(
    tool_name: str,
    arguments: Mapping[str, Any],
    *,
    risk: str | None = None,
    review: str | None = None,
) -> str:
    """Build a readable approval preview from one tool call."""

    try:
        payload = json.dumps(
            dict(arguments),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            default=str,
        )
    except TypeError:
        payload = repr(dict(arguments))
    review_text = review or risk
    if review_text:
        return f"Tool: {tool_name}\nReview: {review_text}\nArguments:\n{payload}"
    return f"Tool: {tool_name}\nArguments:\n{payload}"
