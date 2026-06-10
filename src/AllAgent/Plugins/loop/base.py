from typing import Optional, Any, cast
import os

from openai import AsyncOpenAI
from abc import ABC
from dotenv import load_dotenv
from openai.types.chat import ChatCompletionMessage

load_dotenv()


class LLMConfig(ABC):
    """
    为后续ReAct, Plan and Execute,提供基础框架
    它用于调用任何兼容OpenAI接口的服务，并默认使用流式响应。
    """

    def __init__(self, temperature: float = 0.7):
        """
        初始化客户端。参数从环境变量加载。
        """
        model = os.getenv("CHAT_MODEL")
        api_key = os.getenv("MODEL_API_KEY")
        base_url = os.getenv("MODEL_URL")
        timeout = int(os.getenv("LLM_TIMEOUT", 60))
        self.temperature = temperature

        if not model or not api_key or not base_url:
            raise ValueError("模型ID、API密钥和服务地址必须被提供或在.env文件中定义。")

        self.model = model
        self.apiKey = api_key
        self.baseUrl = base_url
        self.timeout = timeout

        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    async def chat_text(
        self, messages: list[dict[str, str]], *, tools: Optional[list[dict[str, str]]]
    ) -> ChatCompletionMessage:
        """Call the configured chat model and return stripped text."""

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=cast(Any, messages),
                temperature=self.temperature,
                tools=tools,  # ty:ignore[invalid-argument-type]
            )
        except Exception as exc:
            raise KeyError(f"chat completion failed: {exc}") from exc
        message: ChatCompletionMessage = response.choices[0].message
        return message

    def finish_tool(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "finish_work",
                    "description": "判定当前工作已全部完成，结束任务",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
