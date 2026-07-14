"""Compose an agent with a custom atomic math tool."""

import asyncio
from collections.abc import Mapping
from typing import Any

from simagentplg import (
    BaseAgent,
    MethodToolHandler,
    ModelConfig,
    OpenAIModelAdapter,
    StepOutcome,
    ToolControl,
)

ADD_TOOL = {
    "type": "function",
    "function": {
        "name": "add",
        "description": "Add two numbers.",
        "parameters": {
            "type": "object",
            "properties": {
                "left": {"type": "number"},
                "right": {"type": "number"},
            },
            "required": ["left", "right"],
        },
    },
}


class MathHandler(MethodToolHandler):
    def __init__(self) -> None:
        super().__init__((ADD_TOOL,))

    async def do_add(
        self,
        arguments: Mapping[str, Any],
    ) -> StepOutcome:
        left = arguments.get("left")
        right = arguments.get("right")
        if not isinstance(left, (int, float)) or not isinstance(
            right, (int, float)
        ):
            return StepOutcome(
                {"status": "error", "error": "left and right must be numbers"}
            )
        return StepOutcome(
            {"status": "success", "value": left + right},
            control=ToolControl.COMPLETE,
        )


async def main() -> None:
    agent = BaseAgent(
        OpenAIModelAdapter(ModelConfig.from_env()),
        agent_id="calculator",
        handlers=[MathHandler()],
    )

    try:
        result = await agent.runtime(
            task="Use the add tool to calculate 19.5 + 22.5."
        )
        print(result)
    finally:
        await agent.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
