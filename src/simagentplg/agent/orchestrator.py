from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from openai.types.chat import ChatCompletionMessage

from simagentplg.agent.context_builder import (
    AgentContextBuilder,
    ContextBuildResult,
)
from simagentplg.agent.state import AgentState
from simagentplg.agent.tool_runtime import ToolCallResult, ToolRuntime
from simagentplg.plugins.skill.skill_manager import (
    LOAD_SKILL_TOOL_NAME,
    SkillManager,
)

TOOL_COMPLETION_RETRY_PROMPT = """
Tool mode requires a finishing tool call to complete the task.
If the work is complete, call the task's finishing tool now.
Do not end with plain text.
""".strip()

MAX_NO_TOOL_RESPONSES = 3

ModelCall = Callable[[ContextBuildResult], Awaitable[ChatCompletionMessage]]


class AgentOrchestrator:
    """Coordinate one agent task across model, state, and tool runtimes."""

    def __init__(
        self,
        *,
        agent_id: str,
        state: AgentState,
        context_builder: AgentContextBuilder,
        model_call: ModelCall,
        tool_runtime: ToolRuntime,
        skill_manager: SkillManager | None,
        has_handler_tools: bool,
        max_steps: int,
        logger: logging.Logger,
    ) -> None:
        self.agent_id = agent_id
        self.state = state
        self.context_builder = context_builder
        self.model_call = model_call
        self.tool_runtime = tool_runtime
        self.skill_manager = skill_manager
        self.has_handler_tools = has_handler_tools
        self.max_steps = max_steps
        self.logger = logger

    @property
    def tools(self) -> list[dict[str, Any]]:
        """Return every tool definition available to the model."""

        tools: list[dict[str, Any]] = []
        if self.has_handler_tools:
            tools.extend(self.tool_runtime.tools)
        if self.skill_manager is not None:
            load_skill_tool = self.skill_manager.build_load_skill_tool()
            if load_skill_tool is not None:
                tools.append(load_skill_tool)
        return tools

    async def run(self, *, task: str) -> str:
        """Run one task and commit its terminal status to agent state."""

        await self._prepare_task(task)
        try:
            result = await self._run_loop()
        except Exception as exc:
            self.state.fail(exc)
            raise
        self.state.complete(result)
        return result

    async def _prepare_task(self, task: str) -> None:
        self.state.begin_task(task)
        if self.has_handler_tools:
            await self.tool_runtime.on_task_start()
        self._activate_explicit_skill()

    async def _run_loop(self) -> str:
        for _ in range(self.max_steps):
            message = await self._chat_next_turn()
            self.state.add_message(message.model_dump())

            if not message.tool_calls:
                if not self.has_handler_tools:
                    if message.content:
                        return message.content
                    raise RuntimeError(
                        "plain chat completion returned empty content"
                    )

                self.state.no_tool_response_count += 1
                if (
                    self.state.no_tool_response_count
                    >= MAX_NO_TOOL_RESPONSES
                ):
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
        context = self.context_builder.build(
            self.state,
            tools=self.tools,
            transient_messages=self._runtime_context_messages(),
        )
        return await self.model_call(context)

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
        if self.skill_manager is None:
            return

        skill_name = self.skill_manager.select_explicit_skill(
            self.state.messages
        )
        if skill_name is not None:
            self.state.active_skill_name = skill_name

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

            tool_result = await self.tool_runtime.execute_tool_call(tool_call)
            result_messages.extend(tool_result.messages)
            if tool_result.exit_value is not None:
                return ToolCallResult(
                    tuple(result_messages),
                    exit_value=tool_result.exit_value,
                )

        return ToolCallResult(tuple(result_messages))

    def _execute_load_skill_call(self, tool_call: Any) -> dict[str, str]:
        if self.skill_manager is None:
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
            result = self.skill_manager.load_skill(skill_name.strip())
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
