from allagent.logger import get_logger

from allagent.agent.base import LLMConfig

logger = get_logger("CHATAGENT")

CHAT_PROMPT = """
现在你的身份是我的同龄好朋友，我们日常随意聊天。
说话接地气、幽默风趣，偶尔开玩笑，不用正式话术。
可以一起聊生活、兴趣、美食、趣事，接话自然流畅，不要像机器人。
"""

MAX_STEP = 5


class ChatLoop(LLMConfig):

    def __init__(
        self,
    ) -> None:
        super().__init__()

    async def runtime(
        self, *, task: str, system_prompt: str = CHAT_PROMPT
    ) -> str | None:

        self.messages.append({"role": "system", "content": system_prompt})
        self.messages.append({"role": "user", "content": task})

        for turn in range(MAX_STEP):
            logger.info("第 %d/%d 轮", turn + 1, MAX_STEP)

            message = await self.chat_text(self.messages, tools=None)
            self.messages.append(message.model_dump())
            return message.content


async def main():
    task = "你好介绍一下自己"
    loop = ChatLoop()
    result = await loop.runtime(task=task)
    logger.info("Chat 运行结果: %s", result)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
