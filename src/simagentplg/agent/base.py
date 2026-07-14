import asyncio
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from simagentplg.agent.context_builder import AgentContextBuilder
from simagentplg.agent.orchestrator import AgentOrchestrator
from simagentplg.agent.result import AgentRunResult
from simagentplg.agent.runtime_policy import RuntimePolicy
from simagentplg.agent.state import AgentState
from simagentplg.agent.tool_runtime import ToolRuntime
from simagentplg.agent.types import StepOutcome
from simagentplg.logger import get_logger
from simagentplg.middleware import ToolMiddleware
from simagentplg.plugins.skill.skill_manager import SkillManager
from simagentplg.providers.base import ModelAdapter

if TYPE_CHECKING:
    from simagentplg.handlers.base import BaseHandler

DEFAULT_SYSTEM_PROMPT = "You are a helpful, concise assistant."

TOOL_PROTOCOL_PROMPT = """
You can call external tools when they are available.

Tool protocol:
- Use tool calls for actions that require a registered tool.
- Wait for tool results before deciding the next action.
- Do not repeat the same ineffective tool call.
""".strip()

EXPLICIT_FINISH_PROTOCOL_PROMPT = """
This agent requires explicit tool completion.
Plain text does not finish the task. After completing all work, call a tool
that returns the completion control signal.
""".strip()


class BaseAgent:
    """Stateful agent core composed with a model adapter and tool handlers."""

    def __init__(
        self,
        model: ModelAdapter,
        *,
        agent_id: str,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        handlers: Iterable["BaseHandler"] | None = None,
        middlewares: Iterable[ToolMiddleware] | None = None,
        skills_dir: str | Path | None = None,
        context_builder: AgentContextBuilder | None = None,
        runtime_policy: RuntimePolicy | None = None,
    ) -> None:
        self._agent_id = agent_id.strip()
        if not self._agent_id:
            raise ValueError("agent_id must not be empty")
        policy = runtime_policy or RuntimePolicy()
        self.model = model
        self.system_prompt = system_prompt
        self.runtime_policy = policy
        self.handlers = list(handlers or ())
        self.middlewares = list(middlewares or ())
        self._operation_lock = asyncio.Lock()
        self._started = False
        self._skill_manager = SkillManager(skills_dir) if skills_dir else None
        self.state = AgentState()
        self._context_builder = context_builder or AgentContextBuilder(
            skill_manager=self._skill_manager,
        )
        self.logger = get_logger(f"{self.agent_id}")
        self._tool_runtime = ToolRuntime(
            self.handlers,
            self.middlewares,
            state=self.state,
            logger=self.logger,
            max_repeated_tool_calls=policy.max_repeated_tool_calls,
        )
        self.orchestrator = AgentOrchestrator(
            agent_id=self.agent_id,
            state=self.state,
            context_builder=self._context_builder,
            model_call=self.model.complete,
            tool_runtime=self._tool_runtime,
            skill_manager=self._skill_manager,
            policy=self.runtime_policy,
            logger=self.logger,
        )
        self.reset()

    @property
    def agent_id(self) -> str:
        """Return the immutable identity of this agent."""

        return self._agent_id

    @property
    def tools(self) -> list[dict[str, Any]]:
        """Return the currently registered function tool definitions."""

        return self.orchestrator.tools

    @property
    def messages(self) -> list[dict[str, Any]]:
        """Return the agent's persistent conversation history."""

        return self.state.messages

    def reset(
        self,
        history: Sequence[Mapping[str, Any]] | None = None,
    ) -> None:
        """Reset conversation memory while preserving the agent identity."""

        messages = [{"role": "system", "content": self.system_prompt}]
        if self.handlers:
            messages.append({"role": "system", "content": TOOL_PROTOCOL_PROMPT})
        if self.runtime_policy.require_explicit_finish:
            messages.append(
                {
                    "role": "system",
                    "content": EXPLICIT_FINISH_PROTOCOL_PROMPT,
                }
            )
        if history:
            messages.extend(dict(message) for message in history)
        self.state.reset(messages)

    async def startup(self) -> None:
        """Start the model adapter, handlers, and middleware resources."""

        async with self._operation_lock:
            await self._startup()

    async def _startup(self) -> None:
        await self._ensure_skills_discovered()

        if self._started:
            return

        try:
            await self.model.startup()
            await self._tool_runtime.startup()
            if self._tool_runtime.tools:
                self.logger.info(
                    "Loaded %d handler(s); registered tools: %s",
                    len(self.handlers),
                    ", ".join(
                        sorted(
                            tool["function"]["name"]
                            for tool in self._tool_runtime.tools
                        )
                    ),
                )
        except Exception:
            try:
                await self._tool_runtime.shutdown()
            except Exception as shutdown_error:
                self.logger.warning(
                    "Tool runtime rollback shutdown failed: %s",
                    shutdown_error,
                )
            try:
                await self.model.shutdown()
            except Exception as shutdown_error:
                self.logger.warning(
                    "Model adapter rollback shutdown failed: %s",
                    shutdown_error,
                )
            raise

        self._started = True

    async def shutdown(self) -> None:
        """Release all resources owned by this agent."""

        async with self._operation_lock:
            await self._shutdown()

    async def _shutdown(self) -> None:
        if not self._started:
            return

        errors: list[Exception] = []
        try:
            await self._tool_runtime.shutdown()
        except Exception as exc:
            errors.append(exc)
        try:
            await self.model.shutdown()
        except Exception as exc:
            errors.append(exc)
        self._started = False
        if errors:
            raise RuntimeError(
                f"failed to shut down {len(errors)} agent resource(s)"
            ) from errors[0]

    async def dispatch(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> StepOutcome:
        """Dispatch a tool call to its explicitly registered handler."""

        async with self._operation_lock:
            await self._startup()
            return await self._tool_runtime.dispatch(tool_name, arguments)

    async def run(self, *, task: str) -> AgentRunResult:
        """Run one task and return a structured terminal result."""

        async with self._operation_lock:
            await self._startup()
            return await self.orchestrator.run(task=task)

    async def runtime(self, *, task: str) -> str | None:
        """Compatibility wrapper returning completed output as text."""

        result = await self.run(task=task)
        result.raise_for_status()
        return result.output

    async def _ensure_skills_discovered(self) -> None:
        if self._skill_manager is not None:
            await self._skill_manager.discover()
