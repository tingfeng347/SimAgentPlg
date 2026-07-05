from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from simagentplg.agent.types import StepOutcome



DEFAULT_APPROVAL_PREVIEW_CHARS = 2_000
BASH_DANGEROUS_PATTERNS = (
    "rm ",
    "rm\n",
    "rm\t",
    "rm(",
    "rm;",
    "rm\\",
    "rm|",
    "rm&",
    "rm<",
    "rm>",
    "sudo ",
    "mkfs.",
    "dd if=",
    ":(){ :|:& };:",
    "> /dev/sda",
    "/dev/null",
    "chmod 777",
)


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
    """Require approval for risky bash_run commands before execution."""

    def __init__(
        self,
        approval: HumanApproval | None = None,
        *,
        name: str | None = None,
        enabled: bool = True,
    ) -> None:
        super().__init__(name=name, enabled=enabled)
        self.approval = approval or HumanApproval()

    async def before_tool_call(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> StepOutcome | None:
        if tool_name != "bash_run":
            return None

        matched_pattern = self._matched_pattern(arguments)
        if matched_pattern is None:
            return None

        approved = await self.approval.approve(
            format_tool_call_preview(
                tool_name,
                arguments,
                risk=f"matched bash pattern: {matched_pattern.strip()}",
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

    def _matched_pattern(self, arguments: Mapping[str, Any]) -> str | None:
        code = arguments.get("code")
        if not isinstance(code, str):
            return None
        for pattern in BASH_DANGEROUS_PATTERNS:
            if pattern in code:
                return pattern
        return None


def format_tool_call_preview(
    tool_name: str,
    arguments: Mapping[str, Any],
    *,
    risk: str | None = None,
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
    if risk:
        return f"Tool: {tool_name}\nRisk: {risk}\nArguments:\n{payload}"
    return f"Tool: {tool_name}\nArguments:\n{payload}"
