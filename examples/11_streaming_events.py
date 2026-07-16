"""Render real provider text deltas from the Harness event stream."""

import asyncio

from simagentplg import (
    AgentEvent,
    AgentFinished,
    AssistantTextDelta,
    BaseAgent,
    MessageCompleted,
    ModelConfig,
    OpenAIModelAdapter,
)


class StreamingConsoleSink:
    def __init__(self) -> None:
        self.received_delta = False

    async def emit(self, event: AgentEvent) -> None:
        payload = event.payload
        if isinstance(payload, AssistantTextDelta):
            self.received_delta = True
            print(payload.delta, end="", flush=True)
        elif isinstance(payload, MessageCompleted) and self.received_delta:
            print()
        elif isinstance(payload, AgentFinished):
            print(
                f"\nfinished: {payload.result.status} / "
                f"{payload.result.stop_reason}"
            )


async def main() -> None:
    sink = StreamingConsoleSink()
    agent = BaseAgent(
        OpenAIModelAdapter(ModelConfig.from_env()),
        agent_id="streaming-demo",
        system_prompt="Answer concisely and stream the response normally.",
        event_sink=sink,
    )

    try:
        result = await agent.run(
            task=(
                "In three short sentences, explain why streaming improves "
                "an Agent Harness user experience."
            )
        )
        if not result.succeeded:
            result.raise_for_status()
    finally:
        await agent.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
