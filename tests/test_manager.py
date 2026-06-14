import asyncio
import unittest
from dataclasses import dataclass

from simagentplg import (
    AgentAlreadyExistsError,
    AgentManager,
    AgentNotFoundError,
)


@dataclass
class Activity:
    active: int = 0
    maximum: int = 0


class StubAgent:
    def __init__(
        self,
        name: str,
        *,
        activity: Activity | None = None,
        delay: float = 0.01,
    ) -> None:
        self.name = name
        self.activity = activity or Activity()
        self.delay = delay
        self.started = 0
        self.stopped = 0

    async def startup(self) -> None:
        self.started += 1

    async def shutdown(self) -> None:
        self.stopped += 1

    async def runtime(self, *, task: str) -> str:
        self.activity.active += 1
        self.activity.maximum = max(
            self.activity.maximum,
            self.activity.active,
        )
        try:
            await asyncio.sleep(self.delay)
            return f"{self.name}:{task}"
        finally:
            self.activity.active -= 1


class FailingAgent(StubAgent):
    async def runtime(self, *, task: str) -> str:
        raise RuntimeError(f"failed:{task}")


class BlockingAgent(StubAgent):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def runtime(self, *, task: str) -> str:
        self.entered.set()
        await self.release.wait()
        return f"{self.name}:{task}"


class AgentManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_registry_errors_are_explicit(self) -> None:
        manager = AgentManager()
        agent = StubAgent("one")
        manager.register("one", agent)  # type: ignore[arg-type]

        with self.assertRaises(AgentAlreadyExistsError):
            manager.register("one", agent)  # type: ignore[arg-type]
        with self.assertRaises(AgentNotFoundError):
            manager.get("missing")

    async def test_same_agent_runs_serially(self) -> None:
        manager = AgentManager()
        agent = StubAgent("one")
        manager.register("one", agent)  # type: ignore[arg-type]

        results = await asyncio.gather(
            manager.run("one", "a"),
            manager.run("one", "b"),
        )

        self.assertEqual(results, ["one:a", "one:b"])
        self.assertEqual(agent.activity.maximum, 1)

    async def test_different_agents_run_concurrently(self) -> None:
        manager = AgentManager()
        activity = Activity()
        manager.register(
            "one",
            StubAgent("one", activity=activity),  # type: ignore[arg-type]
        )
        manager.register(
            "two",
            StubAgent("two", activity=activity),  # type: ignore[arg-type]
        )

        results = await manager.run_many({"one": "a", "two": "b"})

        self.assertEqual(results, {"one": "one:a", "two": "two:b"})
        self.assertEqual(activity.maximum, 2)

    async def test_run_many_isolates_failures(self) -> None:
        manager = AgentManager()
        manager.register("ok", StubAgent("ok"))  # type: ignore[arg-type]
        manager.register(
            "bad",
            FailingAgent("bad"),  # type: ignore[arg-type]
        )

        results = await manager.run_many(
            {"ok": "work", "bad": "work", "missing": "work"}
        )

        self.assertEqual(results["ok"], "ok:work")
        self.assertIsInstance(results["bad"], RuntimeError)
        self.assertIsInstance(results["missing"], AgentNotFoundError)

    async def test_startup_remove_and_shutdown_manage_lifecycle(self) -> None:
        manager = AgentManager()
        first = StubAgent("first")
        second = StubAgent("second")
        manager.register("first", first)  # type: ignore[arg-type]
        manager.register("second", second)  # type: ignore[arg-type]

        await manager.startup()
        removed = await manager.remove("first")
        await manager.shutdown()

        self.assertIs(removed, first)
        self.assertEqual(first.started, 1)
        self.assertEqual(first.stopped, 1)
        self.assertEqual(second.started, 1)
        self.assertEqual(second.stopped, 1)
        with self.assertRaises(AgentNotFoundError):
            manager.get("first")

    async def test_remove_rejects_a_queued_run(self) -> None:
        manager = AgentManager()
        agent = BlockingAgent("one")
        manager.register("one", agent)  # type: ignore[arg-type]

        running = asyncio.create_task(manager.run("one", "running"))
        await agent.entered.wait()
        queued = asyncio.create_task(manager.run("one", "queued"))
        await asyncio.sleep(0)
        removing = asyncio.create_task(manager.remove("one"))
        await asyncio.sleep(0)

        agent.release.set()

        self.assertEqual(await running, "one:running")
        with self.assertRaises(AgentNotFoundError):
            await queued
        self.assertIs(await removing, agent)


if __name__ == "__main__":
    unittest.main()
