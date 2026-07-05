import json
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from dotenv import load_dotenv
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessage

from simagentplg.agent.context import (
    convert_to_llm_messages,
    transform_context,
)
from simagentplg.agent.middleware import MiddleWare
from simagentplg.agent.tool_runtime import ToolRuntime
from simagentplg.agent.types import AgentMessage, StepOutcome
from simagentplg.logger import get_logger
from simagentplg.plugins.skill.skill_manager import SkillManager

if TYPE_CHECKING:
    from simagentplg.handlers.base import BaseHandler

TOOL_COMPLETION_PROMPT = """
工具模式下，只有调用一个会结束任务的工具才表示任务完成。
完成所有操作后，必须单独调用当前任务指定的完成工具。
不要用普通文本结束任务，也不要在完成后继续调用其他工具。
""".strip()

REACT_LOOP_PROMPT = """
你是一个有能力调用外部工具的智能助手。你必须严格遵循以下 ReAct 流程：

1. Thought: 分析当前问题，规划下一步行动。
2. Action: 调用一个工具来执行行动。
重要规则：
- 每轮只能调用一个或一组工具，不能同时输出思考内容和工具调用之外的文字。
- 工具执行结果会返回给你，请根据结果继续思考下一步。
- 不要重复相同的无效操作。
- 完成所有操作后，必须调用当前任务指定的完成工具来结束任务。
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
        model = os.getenv("CHAT_MODEL") or os.getenv("BASE_MODEL")
        api_key = os.getenv("MODEL_API_KEY")
        base_url = os.getenv("MODEL_URL")

        if not model or not api_key or not base_url:
            raise ValueError(
                "CHAT_MODEL, MODEL_API_KEY and MODEL_URL must be defined"
                " (BASE_MODEL is accepted as a legacy fallback)"
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
        system_prompt: str = REACT_LOOP_PROMPT,
        handlers: Iterable["BaseHandler"] | None = None,
        middlewares: Iterable[MiddleWare] | None = None,
        enable_tools: bool = False,
        skills_dir: str | Path | None = None,
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
        self.enable_tools = enable_tools
        self.max_steps = max_steps
        self.client = client or AsyncOpenAI(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            timeout=self.config.timeout,
        )
        self.handlers = list(handlers or ())
        self.middlewares = list(middlewares or ())
        self.messages: list[dict[str, Any]] = []
        self._started = False
        self._skill_manager = SkillManager(skills_dir) if skills_dir else None
        self._last_skill_name: str | None = None
        self.logger = get_logger(f"{self.agent_id}")
        self._tool_runtime = ToolRuntime(
            self.handlers,
            self.middlewares,
            logger=self.logger,
        )
        self.reset()

    @property
    def agent_id(self) -> str:
        """Return the immutable identity used by AgentManager."""

        return self._agent_id

    @property
    def tools(self) -> list[dict[str, Any]]:
        """Return the currently registered OpenAI tool definitions."""

        return list(self._tool_runtime.tools)

    def reset(
        self,
        history: Sequence[Mapping[str, Any]] | None = None,
    ) -> None:
        """Reset conversation memory while preserving the agent identity."""

        self.messages = [{"role": "system", "content": self.system_prompt}]
        if self.enable_tools and self.system_prompt != REACT_LOOP_PROMPT:
            self.messages.append(
                {"role": "system", "content": TOOL_COMPLETION_PROMPT}
            )
        if history:
            self.messages.extend(dict(message) for message in history)
        self._last_skill_name = None

    async def startup(self) -> None:
        """Start handlers and build an unambiguous tool routing table."""

        if self._started or not self.enable_tools:
            return

        try:
            await self._tool_runtime.startup()
            self.logger.info(
                "已装载 %d 个工具，注册工具: %s",
                len(self.handlers),
                ", ".join(
                    sorted(
                        tool["function"]["name"]
                        for tool in self._tool_runtime.tools
                    )
                ),
            )
            if self._skill_manager is not None:
                await self._skill_manager.discover()
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

        if not self.enable_tools:
            raise RuntimeError("tool execution is disabled for this agent")
        if not self._started:
            await self.startup()

        return await self._tool_runtime.dispatch(tool_name, arguments)

    async def chat_text(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None,
        response_format: dict[str, Any] | None = None,
    ) -> ChatCompletionMessage:
        """Call the configured model and return its first message."""

        try:
            kwargs: dict[str, Any] = {
                "model": self.config.model,
                "messages": cast(Any, messages),
                "temperature": self.config.temperature,
                "tools": cast(Any, tools)
            }
            if response_format is not None:
                kwargs["response_format"] = response_format
            response = await self.client.chat.completions.create(
                **kwargs,
            )
        except Exception as exc:
            raise RuntimeError(f"chat completion failed: {exc}") from exc
        return cast(ChatCompletionMessage, response.choices[0].message)

    async def chat_json(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Call the configured model and parse a JSON object response."""

        message = await self.chat_text(
            messages,
            tools=tools,
            response_format={"type": "json_object"},
        )
        if not message.content:
            raise RuntimeError("chat json completion returned empty content")
        try:
            payload = json.loads(message.content)
        except json.JSONDecodeError as exc:
            raise RuntimeError("chat json completion returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("chat json completion must return a JSON object")
        return payload

    async def runtime(self, *, task: str) -> str | None:
        """Run one task and keep the resulting conversation in memory."""

        if self.enable_tools:
            await self.startup()
            await self._tool_runtime.on_task_start()

        self.messages.append({"role": "user", "content": task})

        for turn in range(self.max_steps):
            self.logger.info("第 %d/%d 轮", turn + 1, self.max_steps)
            await self._inject_skill_messages()
            context_messages = self.transform_context(self.messages)
            llm_messages = self.convert_to_llm_messages(context_messages)

            message = await self.chat_text(
                llm_messages,
                tools=(self.tools or None) if self.enable_tools else None,
            )
            self.messages.append(message.model_dump())

            if not message.tool_calls:
                if not self.enable_tools and message.content:
                    return message.content
                if self.enable_tools:
                    self.messages.append(
                        {
                            "role": "system",
                            "content": TOOL_COMPLETION_PROMPT,
                        }
                    )
                continue

            tool_result = await self._tool_runtime.execute_tool_calls(message)
            self.messages.extend(tool_result.messages)
            if tool_result.exit_value is not None:
                return tool_result.exit_value

        if self.enable_tools:
            raise RuntimeError(
                f"agent {self.agent_id!r} did not finish within "
                f"{self.max_steps} steps"
            )
        return None

    def transform_context(
        self,
        messages: Sequence[Mapping[str, Any]],
    ) -> list[AgentMessage]:
        """Transform internal messages before provider conversion."""

        return transform_context(messages)

    def convert_to_llm_messages(
        self,
        messages: Sequence[Mapping[str, Any]],
    ) -> list[AgentMessage]:
        """Convert agent context messages into model provider messages."""

        return convert_to_llm_messages(messages)

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
