"""Plan, execute, and review a task with separate agent roles."""

import asyncio

from simagentplg import (
    AgentManager,
    AgentWorkflow,
    BaseAgent,
    ModelConfig,
    WorkflowStep,
)


async def main() -> None:
    config = ModelConfig.from_env()
    manager = AgentManager()
    manager.register(
        "planner",
        BaseAgent(
            config=config,
            system_prompt="You create concise, actionable implementation plans.",
            enable_tools=False,
        ),
    )
    manager.register(
        "executor",
        BaseAgent(
            config=config,
            system_prompt="You execute a provided plan and report the result.",
        ),
    )
    manager.register(
        "reviewer",
        BaseAgent(
            config=config,
            system_prompt="You review completed work for correctness and risk.",
            enable_tools=False,
        ),
    )

    workflow = AgentWorkflow(
        manager,
        [
            WorkflowStep(
                name="plan",
                agent_id="planner",
                prompt="Create a plan for this task:\n{input}",
            ),
            WorkflowStep(
                name="execute",
                agent_id="executor",
                prompt=(
                    "Original task:\n{original_task}\n\n"
                    "Execute this plan:\n{input}"
                ),
            ),
            WorkflowStep(
                name="review",
                agent_id="reviewer",
                prompt=(
                    "Review the implementation below against the original "
                    "plan.\n\nPlan:\n{plan}\n\nImplementation:\n{execute}"
                ),
            ),
        ],
    )

    try:
        result = await workflow.run(
            "Create a Python script that validates email addresses."
        )
        for step in result.steps:
            print(f"\n[{step.step_name}]\n{step.output}")
        print(f"\nFinal output:\n{result.final_output}")
    finally:
        await manager.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
