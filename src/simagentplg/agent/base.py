import asyncio
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from dotenv import load_dotenv
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessage

from simagentplg.agent.context_builder import (
    AgentContextBuilder,
    ContextBuildResult,
)
from simagentplg.agent.orchestrator import AgentOrchestrator
from simagentplg.agent.result import AgentRunResult
from simagentplg.agent.runtime_policy import RuntimePolicy
from simagentplg.agent.state import AgentState
from simagentplg.agent.tool_runtime import ToolRuntime
from simagentplg.agent.types import StepOutcome
from simagentplg.logger import get_logger
from simagentplg.middleware import ToolMiddleware
from simagentplg.plugins.skill.skill_manager import SkillManager

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

@dataclass(frozen=True, slots=True)
class ModelConfig:
    """Connection and generation settings for an OpenAI-compatible model."""

    model: str
    api_key: str
    base_url: str
    timeout: int = 60
    temperature: float = 0.7

    def __post_init__(self) -> None:
        if not self.model:
            raise ValueError("model must not be empty")
        if not self.api_key:
            raise ValueError("api_key must not be empty")
        if not self.base_url:
            raise ValueError("base_url must not be empty")
        if self.timeout <= 0:
            raise ValueError("timeout must be greater than zero")

    @classmethod
    def from_env(cls) -> "ModelConfig":
        """Build a config from the configured model environment variables."""

        load_dotenv()
        model = os.getenv("CHAT_MODEL")
        api_key = os.getenv("MODEL_API_KEY")
        base_url = os.getenv("MODEL_URL")

        if not model or not api_key or not base_url:
            raise ValueError(
                "CHAT_MODEL, MODEL_API_KEY and MODEL_URL must be defined"
            )

        try:
            timeout = int(os.getenv("LLM_TIMEOUT", "60"))
            temperature = float(os.getenv("LLM_TEMPERATURE", "0.7"))
        except ValueError as exc:
            raise ValueError(
                "LLM_TIMEOUT and LLM_TEMPERATURE must be numeric"
            ) from exc

        return cls(
            model=model,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            temperature=temperature,
        )


class BaseAgent:
    """Stateful OpenAI-compatible agent with composable tool handlers."""

    def __init__(
        self,
        config: ModelConfig | None = None,
        *,
        agent_id: str,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        handlers: Iterable["BaseHandler"] | None = None,
        middlewares: Iterable[ToolMiddleware] | None = None,
        skills_dir: str | Path | None = None,
        context_builder: AgentContextBuilder | None = None,
        runtime_policy: RuntimePolicy | None = None,
        max_steps: int | None = None,
        client: Any | None = None,
    ) -> None:
        self._agent_id = agent_id.strip()
        if not self._agent_id:
            raise ValueError("agent_id must not be empty")
        if max_steps is not None and max_steps <= 0:
            raise ValueError("max_steps must be greater than zero")

        policy = runtime_policy or RuntimePolicy()
        if max_steps is not None:
            if runtime_policy is not None and max_steps != policy.max_steps:
                raise ValueError(
                    "max_steps conflicts with runtime_policy.max_steps"
                )
            policy = RuntimePolicy(
                max_steps=max_steps,
                max_no_tool_responses=policy.max_no_tool_responses,
                max_repeated_tool_calls=policy.max_repeated_tool_calls,
                require_explicit_finish=policy.require_explicit_finish,
            )

        self.config = config or ModelConfig.from_env()
        self.system_prompt = system_prompt
        self.runtime_policy = policy
        self.max_steps = policy.max_steps
        self.client = client or AsyncOpenAI(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            timeout=self.config.timeout,
        )
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
            model_call=self.chat_text,
            tool_runtime=self._tool_runtime,
            skill_manager=self._skill_manager,
            has_handler_tools=self.has_handler_tools,
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
        """Return the currently registered OpenAI tool definitions."""

        return self.orchestrator.tools

    @property
    def messages(self) -> list[dict[str, Any]]:
        """Return the agent's persistent conversation history."""

        return self.state.messages

    @property
    def has_handler_tools(self) -> bool:
        """Return whether this agent has executable handler tools."""

        return bool(self.handlers)

    def reset(
        self,
        history: Sequence[Mapping[str, Any]] | None = None,
    ) -> None:
        """Reset conversation memory while preserving the agent identity."""

        messages = [{"role": "system", "content": self.system_prompt}]
        if self.has_handler_tools:
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
        """Start handlers and build an unambiguous tool routing table."""

        async with self._operation_lock:
            await self._startup()

    async def _startup(self) -> None:
        await self._ensure_skills_discovered()

        if self._started or not self.has_handler_tools:
            return

        try:
            await self._tool_runtime.startup()
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
            raise

        self._started = True

    async def shutdown(self) -> None:
        """Release resources owned by all started handlers."""

        async with self._operation_lock:
            await self._shutdown()

    async def _shutdown(self) -> None:
        if not self._started:
            return

        try:
            await self._tool_runtime.shutdown()
        finally:
            self._started = False

    async def dispatch(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> StepOutcome:
        """Dispatch a tool call to its explicitly registered handler."""

        if not self.has_handler_tools:
            raise RuntimeError("tool execution is disabled for this agent")

        async with self._operation_lock:
            await self._startup()
            return await self._tool_runtime.dispatch(tool_name, arguments)

    async def chat_text(
        self,
        context: ContextBuildResult,
    ) -> ChatCompletionMessage:
        """Send one complete provider request and return its first message."""

        try:
            kwargs: dict[str, Any] = {
                "model": self.config.model,
                "messages": cast(Any, context.llm_messages),
                "temperature": self.config.temperature,
                "tools": cast(Any, context.tools) or None,
            }
            response = await self.client.chat.completions.create(
                **kwargs,
            )
        except Exception as exc:
            raise RuntimeError(f"chat completion failed: {exc}") from exc
        return cast(ChatCompletionMessage, response.choices[0].message)

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
