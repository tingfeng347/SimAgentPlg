import json
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from dotenv import load_dotenv
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessage

from simagentplg.logger import get_logger
from simagentplg.plugins.skill.skill_manager import SkillManager

if TYPE_CHECKING:
    from simagentplg.handlers.base import BaseHandler

logger = get_logger("BASEAGENT")

REACT_LOOP_PROMPT = """
你是一个有能力调用外部工具的智能助手。你必须严格遵循以下 ReAct 流程：

1. Thought: 分析当前问题，规划下一步行动。
2. Action: 调用一个工具来执行行动。
重要规则：
- 每轮只能调用一个或一组工具，不能同时输出思考内容和工具调用之外的文字。
- 工具执行结果会返回给你，请根据结果继续思考下一步。
- 不要重复相同的无效操作。
""".strip()

DEFAULT_MAX_STEPS = 20


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
        """Build a config from the environment used by SimAgentPlg 0.1.x."""

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


@dataclass(slots=True)
class StepOutcome:
    """Normalized result returned by every tool handler."""

    data: Any
    should_exit: bool = False


class BaseAgent:
    """Stateful OpenAI-compatible agent with composable tool handlers."""

    def __init__(
        self,
        config: ModelConfig | None = None,
        *,
        system_prompt: str = REACT_LOOP_PROMPT,
        handlers: Iterable["BaseHandler"] | None = None,
        enable_tools: bool = True,
        skills_dir: str | Path | None = None,
        max_steps: int = DEFAULT_MAX_STEPS,
        client: Any | None = None,
    ) -> None:
        if max_steps <= 0:
            raise ValueError("max_steps must be greater than zero")

        self.config = config or ModelConfig.from_env()
        self.system_prompt = system_prompt
        self.enable_tools = enable_tools
        self.max_steps = max_steps
        self.client = client or AsyncOpenAI(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            timeout=self.config.timeout,
        )

        if handlers is None:
            from simagentplg.handlers.bash import BashHandler

            handlers = (BashHandler(),)

        self.handlers = list(handlers)
        self.messages: list[dict[str, Any]] = []
        self._tool_routes: dict[str, BaseHandler] = {}
        self._started = False
        self._skill_manager = SkillManager(skills_dir) if skills_dir else None
        self._last_skill_name: str | None = None
        self.reset()

    @property
    def tools(self) -> list[dict[str, Any]]:
        """Return the currently registered OpenAI tool definitions."""

        return [
            tool
            for handler in self.handlers
            for tool in handler.tools
        ]

    def reset(
        self,
        history: Sequence[Mapping[str, Any]] | None = None,
    ) -> None:
        """Reset conversation memory while preserving the agent identity."""

        self.messages = [{"role": "system", "content": self.system_prompt}]
        if history:
            self.messages.extend(dict(message) for message in history)
        self._last_skill_name = None

    async def startup(self) -> None:
        """Start handlers and build an unambiguous tool routing table."""

        if self._started or not self.enable_tools:
            return

        started_handlers: list[BaseHandler] = []
        try:
            for handler in self.handlers:
                await handler.startup()
                started_handlers.append(handler)
            self._tool_routes = self._build_tool_routes()
            if self._skill_manager is not None:
                await self._skill_manager.discover()
        except Exception:
            for handler in reversed(started_handlers):
                try:
                    await handler.shutdown()
                except Exception as shutdown_error:
                    logger.warning(
                        "Handler %s 回滚关闭失败: %s",
                        type(handler).__name__,
                        shutdown_error,
                    )
            self._tool_routes.clear()
            raise

        self._started = True

    async def shutdown(self) -> None:
        """Release resources owned by all started handlers."""

        if not self._started:
            return

        errors: list[Exception] = []
        for handler in reversed(self.handlers):
            try:
                await handler.shutdown()
            except Exception as exc:
                errors.append(exc)

        self._tool_routes.clear()
        self._started = False
        if errors:
            raise RuntimeError(
                f"failed to shut down {len(errors)} handler(s)"
            ) from errors[0]

    async def dispatch(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> StepOutcome:
        """Dispatch a tool call to its explicitly registered handler."""

        if not self.enable_tools:
            raise RuntimeError("tool execution is disabled for this agent")
        if not self._started:
            await self.startup()

        try:
            handler = self._tool_routes[tool_name]
        except KeyError as exc:
            available = ", ".join(sorted(self._tool_routes)) or "none"
            raise KeyError(
                f"unknown tool {tool_name!r}; available tools: {available}"
            ) from exc

        return await handler.dispatch(tool_name, arguments)

    async def chat_text(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None,
    ) -> ChatCompletionMessage:
        """Call the configured model and return its first message."""

        try:
            response = await self.client.chat.completions.create(
                model=self.config.model,
                messages=cast(Any, messages),
                temperature=self.config.temperature,
                tools=cast(Any, tools),
            )
        except Exception as exc:
            raise RuntimeError(f"chat completion failed: {exc}") from exc
        return cast(ChatCompletionMessage, response.choices[0].message)

    async def runtime(self, *, task: str) -> str | None:
        """Run one task and keep the resulting conversation in memory."""

        if self.enable_tools:
            await self.startup()

        self.messages.append({"role": "user", "content": task})

        for turn in range(self.max_steps):
            logger.info("第 %d/%d 轮", turn + 1, self.max_steps)
            await self._inject_skill_messages()

            message = await self.chat_text(
                self.messages,
                tools=self.tools if self.enable_tools else None,
            )
            self.messages.append(message.model_dump())

            if not message.tool_calls:
                if message.content:
                    return message.content
                continue

            exit_value = await self._execute_tool_calls(message)
            if exit_value is not None:
                return exit_value

        return None

    def _build_tool_routes(self) -> dict[str, "BaseHandler"]:
        routes: dict[str, BaseHandler] = {}
        for handler in self.handlers:
            for tool_name in handler.tool_names:
                if tool_name in routes:
                    first = type(routes[tool_name]).__name__
                    second = type(handler).__name__
                    raise ValueError(
                        f"duplicate tool {tool_name!r} in {first} and {second}"
                    )
                routes[tool_name] = handler
        return routes

    async def _inject_skill_messages(self) -> None:
        if self._skill_manager is None:
            return

        skill_dispatch = await self._skill_manager.dispatch(self.messages)
        if not skill_dispatch:
            return

        skill_name = skill_dispatch.get("skill_name", "")
        if skill_name and skill_name != self._last_skill_name:
            self._last_skill_name = skill_name
            self.messages.extend(skill_dispatch["messages"])

    async def _execute_tool_calls(
        self,
        message: ChatCompletionMessage,
    ) -> str | None:
        function_calls = [
            tool_call
            for tool_call in message.tool_calls or []
            if tool_call.type == "function"
        ]

        for tool_call in function_calls:
            outcome = await self._execute_tool_call(
                tool_call.function.name,
                tool_call.function.arguments,
            )
            self.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": self._serialize_tool_result(outcome.data),
                }
            )
            if outcome.should_exit:
                return self._serialize_tool_result(outcome.data)
        return None

    async def _execute_tool_call(
        self,
        tool_name: str,
        raw_arguments: str,
    ) -> StepOutcome:
        try:
            arguments = json.loads(raw_arguments)
            if not isinstance(arguments, dict):
                raise TypeError("tool arguments must be a JSON object")
            return await self.dispatch(tool_name, arguments)
        except Exception as exc:
            logger.warning("工具 %s 执行失败: %s", tool_name, exc)
            return StepOutcome(
                {
                    "status": "error",
                    "tool": tool_name,
                    "error": str(exc),
                }
            )

    @staticmethod
    def _serialize_tool_result(data: Any) -> str:
        if isinstance(data, str):
            return data
        return json.dumps(data, ensure_ascii=False, default=str)
