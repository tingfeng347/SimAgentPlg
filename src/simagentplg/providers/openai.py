from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from dotenv import load_dotenv
from openai import AsyncOpenAI

from simagentplg.agent.cancellation import AgentCancelledError
from simagentplg.providers.base import (
    AssistantMessage,
    ModelAdapter,
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
