from __future__ import annotations

import asyncio
import inspect
import os
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from dotenv import load_dotenv
from openai import AsyncOpenAI

from simagentplg.agent.cancellation import AgentCancelledError
from simagentplg.providers.base import (
    AssistantMessage,
    ModelAdapter,
    ModelResponseCompleted,
    ModelStreamEvent,
    ModelTextDelta,
    ModelThinkingDelta,
    ModelToolCall,
)

if TYPE_CHECKING:
    from simagentplg.agent.cancellation import CancellationToken
    from simagentplg.agent.context_builder import ContextBuildResult


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


@dataclass(slots=True)
class _StreamingToolCall:
    id: str = ""
    name: str = ""
    arguments: str = ""


class OpenAIModelAdapter(ModelAdapter):
    """OpenAI-compatible provider adapter for the core model contract."""

    def __init__(
        self,
        config: ModelConfig,
        *,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.config = config
        self._client = client
        self._owns_client = client is None

    async def startup(self) -> None:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self.config.api_key,
                base_url=self.config.base_url,
                timeout=self.config.timeout,
            )

    async def shutdown(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.close()
            self._client = None

    async def complete(
        self,
        context: "ContextBuildResult",
        *,
        cancellation: "CancellationToken | None" = None,
    ) -> AssistantMessage:
        await self.startup()
        client = self._client
        if client is None:
            raise RuntimeError("OpenAI model client is not initialized")

        try:
            request = client.chat.completions.create(
                model=self.config.model,
                messages=cast(Any, context.llm_messages),
                temperature=self.config.temperature,
                tools=cast(Any, context.tools) or None,
            )
            response = (
                await cancellation.run(request)
                if cancellation is not None
                else await request
            )
        except asyncio.CancelledError:
            raise
        except AgentCancelledError:
            raise
        except Exception as exc:
            raise RuntimeError(f"chat completion failed: {exc}") from exc

        if not response.choices:
            raise RuntimeError("chat completion returned no choices")
        message = response.choices[0].message
        return AssistantMessage(
            content=message.content,
            tool_calls=tuple(
                ModelToolCall(
                    id=tool_call.id,
                    name=tool_call.function.name,
                    arguments=tool_call.function.arguments,
                )
                for tool_call in message.tool_calls or ()
                if tool_call.type == "function"
            ),
        )

    async def stream(
        self,
        context: "ContextBuildResult",
        *,
        cancellation: "CancellationToken | None" = None,
    ) -> AsyncIterator[ModelStreamEvent]:
        """Stream and normalize one OpenAI-compatible chat completion."""

        await self.startup()
        client = self._client
        if client is None:
            raise RuntimeError("OpenAI model client is not initialized")

        response: Any | None = None
        content_parts: list[str] = []
        tool_calls: dict[int, _StreamingToolCall] = {}
        has_finish_reason = False
        try:
            request = client.chat.completions.create(
                model=self.config.model,
                messages=cast(Any, context.llm_messages),
                temperature=self.config.temperature,
                tools=cast(Any, context.tools) or None,
                stream=True,
            )
            response = (
                await cancellation.run(request)
                if cancellation is not None
                else await request
            )
            iterator = response.__aiter__()
            while True:
                if cancellation is not None:
                    cancellation.raise_if_cancelled()
                try:
                    next_chunk = anext(iterator)
                    chunk = (
                        await cancellation.run(next_chunk)
                        if cancellation is not None
                        else await next_chunk
                    )
                except StopAsyncIteration:
                    break

                choices = getattr(chunk, "choices", None) or ()
                if not choices:
                    continue
                choice = choices[0]
                if getattr(choice, "finish_reason", None) is not None:
                    has_finish_reason = True
                delta = getattr(choice, "delta", None)
                if delta is None:
                    continue

                content = getattr(delta, "content", None)
                if content:
                    text = str(content)
                    content_parts.append(text)
                    yield ModelTextDelta(text)

                for field in (
                    "reasoning_content",
                    "reasoning",
                    "reasoning_text",
                ):
                    reasoning = getattr(delta, field, None)
                    if isinstance(reasoning, str) and reasoning:
                        yield ModelThinkingDelta(reasoning)
                        break

                for position, partial in enumerate(
                    getattr(delta, "tool_calls", None) or ()
                ):
                    index = getattr(partial, "index", None)
                    if index is None:
                        index = position
                    call = tool_calls.setdefault(index, _StreamingToolCall())
                    call_id = getattr(partial, "id", None)
                    if call_id:
                        call.id = str(call_id)
                    function = getattr(partial, "function", None)
                    if function is None:
                        continue
                    name = getattr(function, "name", None)
                    if name:
                        call.name += str(name)
                    arguments = getattr(function, "arguments", None)
                    if arguments:
                        call.arguments += str(arguments)
        except asyncio.CancelledError:
            raise
        except AgentCancelledError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"chat completion stream failed: {exc}"
            ) from exc
        finally:
            if response is not None:
                close = getattr(response, "close", None)
                if close is None:
                    close = getattr(response, "aclose", None)
                if close is not None:
                    with suppress(Exception):
                        close_result = close()
                        if inspect.isawaitable(close_result):
                            await close_result

        if not has_finish_reason:
            raise RuntimeError(
                "chat completion stream ended without finish_reason"
            )

        normalized_tool_calls: list[ModelToolCall] = []
        for index in sorted(tool_calls):
            call = tool_calls[index]
            if not call.id or not call.name:
                raise RuntimeError(
                    "chat completion stream returned an incomplete tool call"
                )
            normalized_tool_calls.append(
                ModelToolCall(
                    id=call.id,
                    name=call.name,
                    arguments=call.arguments,
                )
            )

        yield ModelResponseCompleted(
            AssistantMessage(
                content="".join(content_parts) or None,
                tool_calls=tuple(normalized_tool_calls),
            )
        )
