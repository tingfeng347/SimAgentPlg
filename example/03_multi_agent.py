"""Run independent stateful agents concurrently."""

import asyncio

from simagentplg import AgentManager, BaseAgent, ModelConfig


async def main() -> None:
    config = ModelConfig.from_env()
    manager = AgentManager()
    manager.register(
        BaseAgent(
            config=config,
            agent_id="writer",
            system_prompt="You write concise release notes.",
            enable_tools=False,
        ),
    )
    manager.register(
        BaseAgent(
            config=config,
            agent_id="reviewer",
            system_prompt="You review software changes for risk.",
            enable_tools=False,
        ),
    )

    try:
        results = await manager.run_many(
            {
                "writer": "Write a release note for adding agent memory.",
                "reviewer": "List two risks of persistent conversation memory.",
            }
        )
        for agent_id, result in results.items():
            if isinstance(result, Exception):
                print(f"{agent_id} failed: {result}")
            else:
                print(f"{agent_id}: {result}")
    finally:
        await manager.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
