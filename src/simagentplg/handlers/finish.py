from collections.abc import Mapping
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
            "任务完成后调用。提交任务完成总结，并结束当前任务。"
            "调用该工具后不应继续执行其他工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "minLength": 1,
                    "description": "任务完成情况的简明总结。",
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
        logger.info("任务完成，summary=%s", summary[:80])
        return StepOutcome({"summary": summary}, should_exit=True)
