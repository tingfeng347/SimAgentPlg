from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any, Protocol

from simagentplg.agent.cancellation import (
    AgentCancelledError,
    CancellationSource,
    CancellationToken,
)
from simagentplg.agent.context_builder import (
    AgentContextBuilder,
    ContextBuildResult,
)
from simagentplg.agent.events import (
    AgentEventEmitter,
    AgentFinished,
    AgentStarted,
    MessageCompleted,
    TurnCompleted,
    TurnStarted,
)
from simagentplg.agent.result import AgentRunResult, RunStatus, StopReason
from simagentplg.agent.runtime_policy import RuntimePolicy
from simagentplg.agent.state import AgentState
from simagentplg.agent.tool_runtime import (
    RepeatedToolCallError,
    ToolRuntime,
)
from simagentplg.agent.types import ToolCallResult, ToolControl
from simagentplg.plugins.skill.skill_manager import SkillManager
from simagentplg.providers.base import AssistantMessage

TOOL_COMPLETION_RETRY_PROMPT = """
Explicit-finish mode requires a completing tool call to finish the task.
If the work is complete, call a tool that returns completion control now.
Do not end with plain text.
""".strip()


class ModelCall(Protocol):
    """Provider call shape consumed by the orchestrator."""

    def __call__(
        self,
        context: ContextBuildResult,
        *,
        cancellation: CancellationToken | None = None,
    ) -> Awaitable[AssistantMessage]: ...


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
        policy: RuntimePolicy,
        event_emitter: AgentEventEmitter,
    ) -> None:
        self.agent_id = agent_id
        self.state = state
        self.context_builder = context_builder
        self.model_call = model_call
        self.tool_runtime = tool_runtime
        self.skill_manager = skill_manager
        self.policy = policy
        self.event_emitter = event_emitter

    @property
    def tools(self) -> list[dict[str, Any]]:
        """Return every tool definition available to the model."""

        return self.tool_runtime.tools

    async def run(
        self,
        *,
        task: str,
        cancellation: CancellationToken | None = None,
    ) -> AgentRunResult:
        """Run one task and return its structured terminal result."""

        token = cancellation or CancellationSource().token
        run_id = self.event_emitter.begin_run()
        try:
            caller_cancellation: asyncio.CancelledError | None = None
            try:
                self.state.begin_task(task)
                await self.event_emitter.emit(AgentStarted(task))
                await token.run(self._prepare_task())
                result = await self._run_loop(token)
            except AgentCancelledError as exc:
                result = self._cancelled(str(exc))
            except RepeatedToolCallError as exc:
                result = self._failure(
                    StopReason.REPEATED_TOOL_CALL,
                    str(exc),
                )
            except asyncio.CancelledError as exc:
                caller_cancellation = exc
                result = self._cancelled("agent run coroutine was cancelled")
            except Exception as exc:
                result = self._failure(StopReason.RUNTIME_ERROR, str(exc))
            self._commit_result(result)
            await self.event_emitter.emit(AgentFinished(result))
            if caller_cancellation is not None:
                raise caller_cancellation
            return result
        finally:
            self.event_emitter.end_run(run_id)

    async def _prepare_task(self) -> None:
        await self.tool_runtime.on_task_start()
        self._activate_explicit_skill()

    async def _run_loop(
        self,
        cancellation: CancellationToken,
    ) -> AgentRunResult:
        for _ in range(self.policy.max_steps):
            cancellation.raise_if_cancelled()
            turn = self.state.advance_turn()
            await self.event_emitter.emit(TurnStarted(turn))
            try:
                message = await self._chat_next_turn(cancellation)
                self.state.add_message(message.to_agent_message())
                await self.event_emitter.emit(
                    MessageCompleted(turn, message)
                )
                cancellation.raise_if_cancelled()

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
                            f"{self.state.no_tool_response_count} "
                            "consecutive times",
                        )
                    continue

                self.state.no_tool_response_count = 0
                tool_result = await self._execute_tool_calls(
                    message,
                    cancellation,
                )
                self.state.add_messages(list(tool_result.messages))
                if tool_result.cancelled:
                    raise AgentCancelledError(
                        tool_result.error
                        or cancellation.reason
                        or "agent run was aborted"
                    )
                terminal_result = self._terminal_tool_result(tool_result)
                if terminal_result is not None:
                    return terminal_result
            finally:
                await self.event_emitter.emit(TurnCompleted(turn))

        return self._failure(
            StopReason.MAX_STEPS,
            f"agent {self.agent_id!r} did not finish within "
            f"{self.policy.max_steps} steps",
        )

    async def _chat_next_turn(
        self,
        cancellation: CancellationToken,
    ) -> AssistantMessage:
        context = self.context_builder.build(
            self.state,
            tools=self.tools,
            transient_messages=self._runtime_context_messages(),
        )
        return await cancellation.run(
            self.model_call(context, cancellation=cancellation)
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
        cancellation: CancellationToken,
    ) -> ToolCallResult:
        result_messages: list[dict[str, Any]] = []

        tool_calls = message.tool_calls or ()
        for index, tool_call in enumerate(tool_calls):
            tool_result = await self.tool_runtime.execute_tool_call(
                tool_call,
                cancellation=cancellation,
            )
            result_messages.extend(tool_result.messages)
            if tool_result.cancelled:
                reason = (
                    tool_result.error
                    or cancellation.reason
                    or "agent run was aborted"
                )
                for pending_call in tool_calls[index + 1 :]:
                    pending_result = await self.tool_runtime.cancel_tool_call(
                        pending_call,
                        reason=reason,
                    )
                    result_messages.extend(pending_result.messages)
                return ToolCallResult(
                    tuple(result_messages),
                    error=reason,
                    cancelled=True,
                )
            if tool_result.control is not ToolControl.CONTINUE:
                return ToolCallResult(
                    tuple(result_messages),
                    control=tool_result.control,
                    output=tool_result.output,
                )

        return ToolCallResult(tuple(result_messages))

    def _cancelled(self, error: str) -> AgentRunResult:
        return AgentRunResult(
            status=RunStatus.CANCELLED,
            stop_reason=StopReason.EXTERNAL_ABORT,
            turns=self.state.turn,
            error=error,
        )

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
