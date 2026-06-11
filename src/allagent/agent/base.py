import asyncio
from typing import Optional, Any, cast
import os

from dataclasses import dataclass
from openai import AsyncOpenAI
from abc import ABC, abstractmethod
from dotenv import load_dotenv
from openai.types.chat import ChatCompletionMessage

from allagent.logger import get_logger
from .tool_schema import LOCAL_TOOLS

load_dotenv()

logger = get_logger("LLMCONFIG")

BASE_PROMPT = """
你是一个帮助用户完成各种任务的聊天助手
"""


@dataclass
class StepOutcome:
    data: Any  # 工具返回值
    next_prompt: Optional[str] = None  # 下一轮追加的 prompt，None 表示任务完成
    should_exit: bool = False  # True 表示立即退出


# bash 命令黑名单
BASH_BLACKLIST = [
    "rm ",
    "rm\n",
    "rm\t",
    "rm(",
    "rm;",
    "rm\\",
    "rm|",
    "rm&",
    "rm<",
    "rm>",
    "sudo ",
    "mkfs.",
    "dd if=",
    ":(){ :|:& };:",
    "> /dev/sda",
    "/dev/null",
    "chmod 777",
]


async def bash_run(
    code: str,
    timeout: int = 60,
    cwd: Optional[str] = None,
    maxlen: int = 10000,
) -> dict:
    """异步执行 bash 代码片段，返回执行结果 dict。"""
    logger.info("bash_run 脚本:\n%s...", code[:40])

    for pattern in BASH_BLACKLIST:
        if pattern in code:
            logger.warning("bash_run 命中黑名单: %s", pattern.strip())
            return {
                "status": "error",
                "msg": f"禁止执行危险命令: {pattern.strip()}",
                "exit_code": -1,
            }

    try:
        process = await asyncio.create_subprocess_exec(
            "bash",
            "-c",
            code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
        )

        stdout_chunks: list[str] = []

        async def read_stdout() -> None:
            assert process.stdout is not None
            while True:
                line_bytes = await process.stdout.readline()
                if not line_bytes:
                    break
                try:
                    line = line_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    line = line_bytes.decode("gbk", errors="ignore")
                stdout_chunks.append(line)

        read_task = asyncio.create_task(read_stdout())

        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                process.kill()
            except Exception:
                pass
            await process.wait()
            stdout_chunks.append("\n[Timeout Error] 超时强制终止\n")

        await read_task

        stdout_str = "".join(stdout_chunks)
        exit_code = process.returncode if process.returncode is not None else -1
        status = "success" if exit_code == 0 else "error"

        return {
            "status": status,
            "stdout": stdout_str[-maxlen:],
            "exit_code": exit_code,
        }

    except Exception as e:
        return {"status": "error", "msg": str(e)}


class BaseHandler:
    """
    工具调度基类 —— 约定优于配置：
    子类只需定义 do_{tool_name} 方法，LLM 调用该工具时会自动反射路由。
    """

    async def dispatch(
        self, tool_name: str, args: dict, index: int = 0, tool_num: int = 1
    ) -> StepOutcome:
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
            return StepOutcome(
                None, next_prompt=f"未知工具 {tool_name}", should_exit=False
            )


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
        self.exec_cwd = os.getcwd()
        self.messages: list = []
        self.all_tools = [*LOCAL_TOOLS]
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

    async def do_bash_run(self, args: dict) -> StepOutcome:
        """执行 bash 代码片段。"""
        code = args.get("code") or args.get("script")
        if not code:
            logger.warning("bash_run 缺少 code 参数")
            return StepOutcome(
                "[Error] Code missing. Use 'code' or 'script' arg.",
                next_prompt="\n",
            )

        try:
            timeout = int(args.get("timeout", 60))
        except Exception:
            timeout = 60

        tool_num = args.get("_tool_num", 1)
        maxlen = max(1, 10000 // tool_num)

        logger.info("执行 bash_run, timeout=%d, cwd=%s", timeout, self.exec_cwd)
        result = await bash_run(code, timeout=timeout, cwd=self.exec_cwd, maxlen=maxlen)
        logger.info(
            "bash_run 完成, status=%s, exit_code=%s",
            result.get("status"),
            result.get("exit_code"),
        )
        return StepOutcome(result, next_prompt="\n")
