import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field

from simagentplg.agent.base import BaseAgent


class AgentManagerError(RuntimeError):
    """Base error for agent registry and lifecycle failures."""


class AgentAlreadyExistsError(AgentManagerError):
    """Raised when registering an ID that is already in use."""


class AgentNotFoundError(AgentManagerError):
    """Raised when an agent ID is not registered."""


@dataclass(slots=True)
class _AgentEntry:
    agent: BaseAgent
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    active: bool = True


class AgentManager:
    """Registry and concurrency coordinator for stateful agents."""

    def __init__(self) -> None:
        self._entries: dict[str, _AgentEntry] = {}

    def register(self, agent_id: str, agent: BaseAgent) -> None:
        if not agent_id:
            raise ValueError("agent_id must not be empty")
        if agent_id in self._entries:
            raise AgentAlreadyExistsError(
                f"agent {agent_id!r} is already registered"
            )
        self._entries[agent_id] = _AgentEntry(agent)

    def get(self, agent_id: str) -> BaseAgent:
        return self._get_entry(agent_id).agent

    async def remove(
        self,
        agent_id: str,
        *,
        shutdown: bool = True,
    ) -> BaseAgent:
        entry = self._get_entry(agent_id)
        entry.active = False
        del self._entries[agent_id]

        async with entry.lock:
            if shutdown:
                await entry.agent.shutdown()
        return entry.agent

    async def run(self, agent_id: str, task: str) -> str | None:
        entry = self._get_entry(agent_id)
        async with entry.lock:
            if not entry.active:
                raise AgentNotFoundError(
                    f"agent {agent_id!r} is no longer registered"
                )
            return await entry.agent.runtime(task=task)

    async def run_many(
        self,
        tasks: Mapping[str, str],
    ) -> dict[str, str | None | Exception]:
        async def run_one(agent_id: str, task: str) -> str | None | Exception:
            try:
                return await self.run(agent_id, task)
            except Exception as exc:
                return exc

        results = await asyncio.gather(
            *(run_one(agent_id, task) for agent_id, task in tasks.items())
        )
        return dict(zip(tasks, results, strict=True))

    async def startup(self) -> None:
        async def start_one(entry: _AgentEntry) -> None:
            async with entry.lock:
                if entry.active:
                    await entry.agent.startup()

        await asyncio.gather(
            *(start_one(entry) for entry in tuple(self._entries.values()))
        )

    async def shutdown(self) -> None:
        async def stop_one(entry: _AgentEntry) -> Exception | None:
            try:
                async with entry.lock:
                    await entry.agent.shutdown()
            except Exception as exc:
                return exc
            return None

        errors = [
            error
            for error in await asyncio.gather(
                *(stop_one(entry) for entry in tuple(self._entries.values()))
            )
            if error is not None
        ]
        if errors:
            raise AgentManagerError(
                f"failed to shut down {len(errors)} agent(s)"
            ) from errors[0]

    def _get_entry(self, agent_id: str) -> _AgentEntry:
        try:
            return self._entries[agent_id]
        except KeyError as exc:
            raise AgentNotFoundError(
                f"agent {agent_id!r} is not registered"
            ) from exc
