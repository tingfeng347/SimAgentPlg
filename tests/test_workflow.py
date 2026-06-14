import asyncio
import unittest
from collections.abc import Callable

from simagentplg import (
    AgentManager,
    AgentNotFoundError,
    AgentWorkflow,
    WorkflowExecutionError,
    WorkflowStep,
)


class WorkflowAgent:
    def __init__(
        self,
        response: str | None | Callable[[str], str | None],
        *,
        delay: float = 0,
    ) -> None:
        self.response = response
        self.delay = delay
        self.history: list[str] = []
        self.seen_histories: list[tuple[str, ...]] = []
        self.reset_count = 0
        self.active = 0
        self.maximum_active = 0

    def reset(self) -> None:
        self.reset_count += 1
        self.history = []

    async def runtime(self, *, task: str) -> str | None:
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        self.history.append(task)
        self.seen_histories.append(tuple(self.history))
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if callable(self.response):
                return self.response(task)
            return self.response
        finally:
            self.active -= 1

    async def startup(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass


class FailingWorkflowAgent(WorkflowAgent):
    async def runtime(self, *, task: str) -> str:
        raise RuntimeError(f"failed:{task}")


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_pipeline_renders_previous_and_named_outputs(self) -> None:
        manager = AgentManager()
        planner = WorkflowAgent("PLAN")
        executor = WorkflowAgent("IMPLEMENTATION")
        reviewer = WorkflowAgent("APPROVED")
        manager.register("planner", planner)  # type: ignore[arg-type]
        manager.register("executor", executor)  # type: ignore[arg-type]
        manager.register("reviewer", reviewer)  # type: ignore[arg-type]
        workflow = AgentWorkflow(
            manager,
            [
                WorkflowStep("plan", "planner", "Plan: {input}"),
                WorkflowStep(
                    "execute",
                    "executor",
                    "Goal: {original_task}\nPlan: {input}",
                ),
                WorkflowStep(
                    "review",
                    "reviewer",
                    "Review plan={plan}; result={execute}",
                ),
            ],
        )

        result = await workflow.run("build login")

        self.assertEqual(result.original_task, "build login")
        self.assertEqual(result.final_output, "APPROVED")
        self.assertEqual(
            [step.task for step in result.steps],
            [
                "Plan: build login",
                "Goal: build login\nPlan: PLAN",
                "Review plan=PLAN; result=IMPLEMENTATION",
            ],
        )
        self.assertEqual(
            [step.output for step in result.steps],
            ["PLAN", "IMPLEMENTATION", "APPROVED"],
        )

    async def test_reused_agent_is_reset_for_every_step(self) -> None:
        manager = AgentManager()
        agent = WorkflowAgent(lambda task: f"done:{task}")
        manager.register("worker", agent)  # type: ignore[arg-type]
        workflow = AgentWorkflow(
            manager,
            [
                WorkflowStep("first", "worker", "first:{input}"),
                WorkflowStep("second", "worker", "second:{input}"),
            ],
        )

        result = await workflow.run("task")

        self.assertEqual(agent.reset_count, 2)
        self.assertEqual(
            agent.seen_histories,
            [
                ("first:task",),
                ("second:done:first:task",),
            ],
        )
        self.assertEqual(result.final_output, "done:second:done:first:task")

    async def test_concurrent_workflows_isolate_a_shared_agent(self) -> None:
        manager = AgentManager()
        agent = WorkflowAgent(lambda task: task.upper(), delay=0.01)
        manager.register("shared", agent)  # type: ignore[arg-type]
        workflow = AgentWorkflow(
            manager,
            [WorkflowStep("work", "shared", "process:{input}")],
        )

        first, second = await asyncio.gather(
            workflow.run("alpha"),
            workflow.run("beta"),
        )

        self.assertEqual(
            {first.final_output, second.final_output},
            {"PROCESS:ALPHA", "PROCESS:BETA"},
        )
        self.assertEqual(agent.maximum_active, 1)
        self.assertEqual(agent.reset_count, 2)
        self.assertEqual(
            set(agent.seen_histories),
            {("process:alpha",), ("process:beta",)},
        )

    async def test_failure_stops_and_preserves_completed_steps(self) -> None:
        manager = AgentManager()
        manager.register(
            "planner",
            WorkflowAgent("PLAN"),  # type: ignore[arg-type]
        )
        manager.register(
            "executor",
            FailingWorkflowAgent("unused"),  # type: ignore[arg-type]
        )
        workflow = AgentWorkflow(
            manager,
            [
                WorkflowStep("plan", "planner", "plan:{input}"),
                WorkflowStep("execute", "executor", "execute:{input}"),
                WorkflowStep("review", "missing", "review:{input}"),
            ],
        )

        with self.assertRaises(WorkflowExecutionError) as caught:
            await workflow.run("task")

        error = caught.exception
        self.assertEqual(error.step.name, "execute")
        self.assertEqual(len(error.completed_steps), 1)
        self.assertEqual(error.completed_steps[0].output, "PLAN")
        self.assertIsInstance(error.cause, RuntimeError)

    async def test_missing_agent_and_none_output_are_failures(self) -> None:
        manager = AgentManager()
        missing_workflow = AgentWorkflow(
            manager,
            [WorkflowStep("missing", "unknown", "{input}")],
        )

        with self.assertRaises(WorkflowExecutionError) as missing:
            await missing_workflow.run("task")
        self.assertIsInstance(missing.exception.cause, AgentNotFoundError)

        manager.register(
            "empty",
            WorkflowAgent(None),  # type: ignore[arg-type]
        )
        empty_workflow = AgentWorkflow(
            manager,
            [WorkflowStep("empty", "empty", "{input}")],
        )

        with self.assertRaises(WorkflowExecutionError) as empty:
            await empty_workflow.run("task")
        self.assertIn("no output", str(empty.exception.cause))

    def test_invalid_definitions_are_rejected(self) -> None:
        manager = AgentManager()

        invalid_workflows = [
            lambda: AgentWorkflow(manager, []),
            lambda: AgentWorkflow(
                manager,
                [
                    WorkflowStep("same", "one", "{input}"),
                    WorkflowStep("same", "two", "{input}"),
                ],
            ),
            lambda: AgentWorkflow(
                manager,
                [WorkflowStep("first", "one", "{unknown}")],
            ),
            lambda: AgentWorkflow(
                manager,
                [
                    WorkflowStep("first", "one", "{second}"),
                    WorkflowStep("second", "two", "{input}"),
                ],
            ),
            lambda: AgentWorkflow(
                manager,
                [WorkflowStep("first", "one", "{input.value}")],
            ),
        ]

        for factory in invalid_workflows:
            with self.subTest(factory=factory):
                with self.assertRaises(ValueError):
                    factory()

        with self.assertRaises(ValueError):
            WorkflowStep("", "agent", "{input}")
        with self.assertRaises(ValueError):
            WorkflowStep("step", "", "{input}")
        with self.assertRaises(ValueError):
            WorkflowStep("input", "agent", "{input}")


if __name__ == "__main__":
    unittest.main()
