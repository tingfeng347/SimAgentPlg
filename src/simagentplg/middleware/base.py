from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from simagentplg.agent.types import StepOutcome


class Middleware:
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


class ToolMiddleware(Middleware):
    """Middleware hook that runs before a tool handler is dispatched."""

    async def before_tool_call(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> StepOutcome | None:
        """Return None to allow execution, or StepOutcome to short-circuit."""

        return None


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
