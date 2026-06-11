from typing import Optional, Any, cast
import os

from dataclasses import dataclass
from openai import AsyncOpenAI
from abc import ABC, abstractmethod
from dotenv import load_dotenv
from openai.types.chat import ChatCompletionMessage

load_dotenv()


@dataclass
class StepOutcome:
    data: Any                                # 工具返回值
    next_prompt: Optional[str] = None        # 下一轮追加的 prompt，None 表示任务完成
    should_exit: bool = False                # True 表示立即退出

class BaseHandler:
    """
    工具调度基类 —— 约定优于配置：
    子类只需定义 do_{tool_name} 方法，LLM 调用该工具时会自动反射路由。
    """

    async def dispatch(self, tool_name: str, args: dict, index: int = 0, tool_num: int = 1) -> StepOutcome:
        """
        根据 tool_name 反射到 self.do_{tool_name} 方法。
        自动注入 _index / _tool_num 参数。
        """
        method_name = f"do_{tool_name}"
        if hasattr(self, method_name):
            args["_index"] = index
            args["_tool_num"] = tool_num
            return await getattr(self, method_name)(args)
        else:
            return StepOutcome(None, next_prompt=f"未知工具 {tool_name}", should_exit=False)


BASE_PROMPT = """
你是一个帮助用户完成各种任务的聊天助手
"""


class LLMConfig(BaseHandler, ABC):
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
        self.messages: list = []
        self.all_tools = []
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    @abstractmethod
    async def runtime(
        self, *, task: str, system_prompt: str = BASE_PROMPT
    ) -> str | None:
        pass

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

