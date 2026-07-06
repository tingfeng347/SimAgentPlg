from collections.abc import Mapping
from typing import Any

from simagentplg.agent.types import StepOutcome
from simagentplg.handlers.base import MethodToolHandler, ToolSchema
from simagentplg.logger import get_logger

logger = get_logger("FINISHHANDLER")


FINISH_TOOL: ToolSchema = {
    "type": "function",
    "function": {
        "name": "run_finish",
        "description": (
            "Call this when the task is complete. Submit a completion summary "
            "and end the current task. Do not call additional tools after this."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "minLength": 1,
                    "description": "A concise summary of the completed task.",
                },
            },
            "required": ["summary"],
            "additionalProperties": False,
        },
    },
}


class FinishHandler(MethodToolHandler):
    """Built-in task completion handler."""

    def __init__(self) -> None:
        super().__init__((FINISH_TOOL,))

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

        summary = summary.strip()
        logger.info("Task completed summary=%s", summary[:80])
        return StepOutcome({"summary": summary}, should_exit=True)
