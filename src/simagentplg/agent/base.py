import asyncio
import json
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from dotenv import load_dotenv
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessage

from simagentplg.agent.context_builder import AgentContextBuilder
from simagentplg.agent.middleware import Middleware
from simagentplg.agent.state import AgentState
from simagentplg.agent.tool_runtime import ToolCallResult, ToolRuntime
from simagentplg.agent.types import StepOutcome
from simagentplg.logger import get_logger
from simagentplg.plugins.skill.skill_manager import (
    LOAD_SKILL_TOOL_NAME,
    SkillManager,
)

if TYPE_CHECKING:
    from simagentplg.handlers.base import BaseHandler

DEFAULT_SYSTEM_PROMPT = "You are a helpful, concise assistant."

TOOL_PROTOCOL_PROMPT = """
You can call external tools when they are available.

Tool protocol:
- Use tool calls for actions that require a registered tool.
- Wait for tool results before deciding the next action.
- Do not repeat the same ineffective tool call.
- In tool mode, plain text does not finish the task.
- After completing all work, call the task's finishing tool.
""".strip()

TOOL_COMPLETION_RETRY_PROMPT = """
Tool mode requires a finishing tool call to complete the task.
If the work is complete, call the task's finishing tool now.
Do not end with plain text.
""".strip()

DEFAULT_MAX_STEPS = 20
MAX_NO_TOOL_RESPONSES = 3


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
        middlewares: Iterable[Middleware] | None = None,
        skills_dir: str | Path | None = None,
        context_builder: AgentContextBuilder | None = None,
        max_steps: int = DEFAULT_MAX_STEPS,
        client: Any | None = None,
    ) -> None:
        self._agent_id = agent_id.strip()
        if not self._agent_id:
            raise ValueError("agent_id must not be empty")
        if max_steps <= 0:
            raise ValueError("max_steps must be greater than zero")

        self.config = config or ModelConfig.from_env()
        self.system_prompt = system_prompt
        self.max_steps = max_steps
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

        return self._llm_tools()

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
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None,
    ) -> ChatCompletionMessage:
        """Call the configured model and return its first message."""

        try:
            kwargs: dict[str, Any] = {
                "model": self.config.model,
                "messages": cast(Any, messages),
                "temperature": self.config.temperature,
                "tools": cast(Any, tools)
            }
            response = await self.client.chat.completions.create(
                **kwargs,
            )
        except Exception as exc:
            raise RuntimeError(f"chat completion failed: {exc}") from exc
        return cast(ChatCompletionMessage, response.choices[0].message)

    async def runtime(self, *, task: str) -> str | None:
        """Run one task and keep the resulting conversation in memory."""

        async with self._operation_lock:
            await self._startup()
            await self._prepare_task(task)
            try:
                result = await self._run_loop()
            except Exception as exc:
                self.state.fail(exc)
                raise
            self.state.complete(result)
            return result

    async def _ensure_skills_discovered(self) -> None:
        if self._skill_manager is not None:
            await self._skill_manager.discover()

    async def _prepare_task(self, task: str) -> None:
        self.state.begin_task(task)
        if self.has_handler_tools:
            await self._tool_runtime.on_task_start()

        self._activate_explicit_skill()

    async def _run_loop(self) -> str:
        for _ in range(self.max_steps):
            message = await self._chat_next_turn()
            self.state.add_message(message.model_dump())

            if not message.tool_calls:
                if not self.has_handler_tools:
                    if message.content:
                        return message.content
                    raise RuntimeError("plain chat completion returned empty content")

                self.state.no_tool_response_count += 1
                if self.state.no_tool_response_count >= MAX_NO_TOOL_RESPONSES:
                    raise RuntimeError(
                        "tool mode produced plain text without a finishing "
                        "tool call "
                        f"{self.state.no_tool_response_count} consecutive times"
                    )
                continue

            self.state.no_tool_response_count = 0
            tool_result = await self._execute_tool_calls(message)
            self.state.add_messages(list(tool_result.messages))
            if tool_result.exit_value is not None:
                return tool_result.exit_value

        if not self.has_handler_tools:
            raise RuntimeError(
                f"agent {self.agent_id!r} did not finish plain chat within "
                f"{self.max_steps} steps"
            )
        raise RuntimeError(
            f"agent {self.agent_id!r} did not finish within "
            f"{self.max_steps} steps"
        )

    async def _chat_next_turn(self) -> ChatCompletionMessage:
        turn = self.state.advance_turn()
        self.logger.info("Turn %d/%d", turn, self.max_steps)
        context = self._context_builder.build(
            self.state,
            transient_messages=self._runtime_context_messages(),
        )
        return await self.chat_text(
            list(context.llm_messages),
            tools=self._llm_tools() or None,
        )

    def _runtime_context_messages(self) -> list[dict[str, str]]:
        if self.state.no_tool_response_count == 0:
            return []
        return [
            {
                "role": "system",
                "content": self._tool_completion_retry_prompt(
                    self.state.no_tool_response_count
                ),
            }
        ]

    @staticmethod
    def _tool_completion_retry_prompt(no_tool_response_count: int) -> str:
        if no_tool_response_count <= 1:
            return TOOL_COMPLETION_RETRY_PROMPT
        return (
            TOOL_COMPLETION_RETRY_PROMPT
            + "\n\n"
            + (
                f"Retry {no_tool_response_count}/{MAX_NO_TOOL_RESPONSES}: "
                "the previous response still did not include a tool call."
            )
        )

    def _activate_explicit_skill(self) -> None:
        if self._skill_manager is None:
            return

        skill_name = self._skill_manager.select_explicit_skill(self.state.messages)
        if skill_name is not None:
            self.state.active_skill_name = skill_name

    def _llm_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        if self.has_handler_tools:
            tools.extend(self._tool_runtime.tools)
        if self._skill_manager is not None:
            load_skill_tool = self._skill_manager.build_load_skill_tool()
            if load_skill_tool is not None:
                tools.append(load_skill_tool)
        return tools

    async def _execute_tool_calls(
        self,
        message: ChatCompletionMessage,
    ) -> ToolCallResult:
        result_messages: list[dict[str, Any]] = []

        for tool_call in message.tool_calls or []:
            if tool_call.type != "function":
                continue
            if tool_call.function.name == LOAD_SKILL_TOOL_NAME:
                result_messages.append(self._execute_load_skill_call(tool_call))
                continue

            if not self.has_handler_tools:
                result_messages.append(
                    self._tool_error_message(
                        tool_call.id,
                        tool_call.function.name,
                        "tool execution is disabled for this agent",
                    )
                )
                continue

            tool_result = await self._tool_runtime.execute_tool_call(tool_call)
            result_messages.extend(tool_result.messages)
            if tool_result.exit_value is not None:
                return ToolCallResult(
                    tuple(result_messages),
                    exit_value=tool_result.exit_value,
                )

        return ToolCallResult(tuple(result_messages))

    def _execute_load_skill_call(self, tool_call: Any) -> dict[str, str]:
        if self._skill_manager is None:
            return self._tool_error_message(
                tool_call.id,
                LOAD_SKILL_TOOL_NAME,
                "skill loading is disabled for this agent",
            )

        try:
            arguments = json.loads(tool_call.function.arguments)
            if not isinstance(arguments, dict):
                raise TypeError("tool arguments must be a JSON object")
            skill_name = arguments.get("skill_name")
            if not isinstance(skill_name, str) or not skill_name.strip():
                raise TypeError("skill_name must be a non-empty string")
            result = self._skill_manager.load_skill(skill_name.strip())
            self.state.active_skill_name = result["skill_name"]
        except Exception as exc:
            payload: dict[str, Any] = {
                "status": "error",
                "tool": LOAD_SKILL_TOOL_NAME,
                "error": str(exc),
            }
        else:
            payload = result

        return {
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": json.dumps(payload, ensure_ascii=False, default=str),
        }

    @staticmethod
    def _tool_error_message(
        tool_call_id: str,
        tool_name: str,
        error: str,
    ) -> dict[str, str]:
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": json.dumps(
                {
                    "status": "error",
                    "tool": tool_name,
                    "error": error,
                },
                ensure_ascii=False,
            ),
        }
