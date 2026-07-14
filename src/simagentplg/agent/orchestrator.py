from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from simagentplg.agent.context_builder import (
    AgentContextBuilder,
    ContextBuildResult,
)
from simagentplg.agent.result import AgentRunResult, RunStatus, StopReason
from simagentplg.agent.runtime_policy import RuntimePolicy
from simagentplg.agent.state import AgentState
from simagentplg.agent.tool_runtime import (
    RepeatedToolCallError,
    ToolCallResult,
    ToolRuntime,
)
from simagentplg.agent.types import ToolControl
from simagentplg.plugins.skill.skill_manager import (
    LOAD_SKILL_TOOL_NAME,
    SkillManager,
)
from simagentplg.providers.base import AssistantMessage, ModelToolCall

TOOL_COMPLETION_RETRY_PROMPT = """
Explicit-finish mode requires a completing tool call to finish the task.
If the work is complete, call a tool that returns completion control now.
Do not end with plain text.
""".strip()

ModelCall = Callable[[ContextBuildResult], Awaitable[AssistantMessage]]


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
        policy: RuntimePolicy,
        logger: logging.Logger,
    ) -> None:
        self.agent_id = agent_id
        self.state = state
        self.context_builder = context_builder
        self.model_call = model_call
        self.tool_runtime = tool_runtime
        self.skill_manager = skill_manager
        self.has_handler_tools = has_handler_tools
        self.policy = policy
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

    async def run(self, *, task: str) -> AgentRunResult:
        """Run one task and return its structured terminal result."""

        try:
            await self._prepare_task(task)
            result = await self._run_loop()
        except RepeatedToolCallError as exc:
            result = self._failure(StopReason.REPEATED_TOOL_CALL, str(exc))
        except Exception as exc:
            result = self._failure(StopReason.RUNTIME_ERROR, str(exc))
        self._commit_result(result)
        return result

    async def _prepare_task(self, task: str) -> None:
        self.state.begin_task(task)
        if self.has_handler_tools:
            await self.tool_runtime.on_task_start()
        self._activate_explicit_skill()

    async def _run_loop(self) -> AgentRunResult:
        for _ in range(self.policy.max_steps):
            message = await self._chat_next_turn()
            self.state.add_message(message.to_agent_message())

            if not message.tool_calls:
                if not self.policy.require_explicit_finish:
                    if message.content:
                        return AgentRunResult(
                            status=RunStatus.COMPLETED,
                            stop_reason=StopReason.TEXT_RESPONSE,
                            turns=self.state.turn,
                            output=message.content,
                        )
                    return self._failure(
                        StopReason.EMPTY_RESPONSE,
                        "chat completion returned empty content",
                    )

                self.state.no_tool_response_count += 1
                if (
                    self.state.no_tool_response_count
                    >= self.policy.max_no_tool_responses
                ):
                    return self._failure(
                        StopReason.MAX_NO_TOOL_RESPONSES,
                        "explicit-finish mode produced plain text without a "
                        "completing tool call "
                        f"{self.state.no_tool_response_count} consecutive times",
                    )
                continue

            self.state.no_tool_response_count = 0
            tool_result = await self._execute_tool_calls(message)
            self.state.add_messages(list(tool_result.messages))
            terminal_result = self._terminal_tool_result(tool_result)
            if terminal_result is not None:
                return terminal_result

        return self._failure(
            StopReason.MAX_STEPS,
            f"agent {self.agent_id!r} did not finish within "
            f"{self.policy.max_steps} steps",
        )

    async def _chat_next_turn(self) -> AssistantMessage:
        turn = self.state.advance_turn()
        self.logger.info("Turn %d/%d", turn, self.policy.max_steps)
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

    def _tool_completion_retry_prompt(
        self,
        no_tool_response_count: int,
    ) -> str:
        if no_tool_response_count <= 1:
            return TOOL_COMPLETION_RETRY_PROMPT
        return (
            TOOL_COMPLETION_RETRY_PROMPT
            + "\n\n"
            + (
                f"Retry {no_tool_response_count}/"
                f"{self.policy.max_no_tool_responses}: "
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
        message: AssistantMessage,
    ) -> ToolCallResult:
        result_messages: list[dict[str, Any]] = []

        for tool_call in message.tool_calls or []:
            if tool_call.name == LOAD_SKILL_TOOL_NAME:
                result_messages.append(self._execute_load_skill_call(tool_call))
                continue

            if not self.has_handler_tools:
                result_messages.append(
                    self._tool_error_message(
                        tool_call.id,
                        tool_call.name,
                        "tool execution is disabled for this agent",
                    )
                )
                continue

            tool_result = await self.tool_runtime.execute_tool_call(tool_call)
            result_messages.extend(tool_result.messages)
            if tool_result.control is not ToolControl.CONTINUE:
                return ToolCallResult(
                    tuple(result_messages),
                    control=tool_result.control,
                    output=tool_result.output,
                )

        return ToolCallResult(tuple(result_messages))

    def _terminal_tool_result(
        self,
        tool_result: ToolCallResult,
    ) -> AgentRunResult | None:
        if tool_result.control is ToolControl.CONTINUE:
            return None
        if tool_result.control is ToolControl.COMPLETE:
            return AgentRunResult(
                status=RunStatus.COMPLETED,
                stop_reason=StopReason.TOOL_COMPLETION,
                turns=self.state.turn,
                output=tool_result.output,
            )
        if tool_result.control is ToolControl.REJECT:
            return AgentRunResult(
                status=RunStatus.REJECTED,
                stop_reason=StopReason.TOOL_REJECTED,
                turns=self.state.turn,
                output=tool_result.output,
                error="tool execution was rejected",
            )
        return AgentRunResult(
            status=RunStatus.CANCELLED,
            stop_reason=StopReason.TOOL_CANCELLED,
            turns=self.state.turn,
            output=tool_result.output,
            error="tool execution was cancelled",
        )

    def _failure(
        self,
        stop_reason: StopReason,
        error: str,
    ) -> AgentRunResult:
        return AgentRunResult(
            status=RunStatus.FAILED,
            stop_reason=stop_reason,
            turns=self.state.turn,
            error=error,
        )

    def _commit_result(self, result: AgentRunResult) -> None:
        if result.status is RunStatus.COMPLETED:
            self.state.complete(result.output or "")
        elif result.status is RunStatus.REJECTED:
            self.state.reject(result.output)
        elif result.status is RunStatus.CANCELLED:
            self.state.cancel(result.output)
        else:
            self.state.fail(result.error or result.stop_reason.value)

    def _execute_load_skill_call(
        self,
        tool_call: ModelToolCall,
    ) -> dict[str, str]:
        if self.skill_manager is None:
            return self._tool_error_message(
                tool_call.id,
                LOAD_SKILL_TOOL_NAME,
                "skill loading is disabled for this agent",
            )

        try:
            arguments = json.loads(tool_call.arguments)
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
