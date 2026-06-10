import json
from pathlib import Path

from allagent.logger import get_logger

from allagent.plugins import McpServerManager, SkillManager
from allagent.agent.base import LLMConfig

logger = get_logger("REACTAGENT")

REACT_LOOP_PROMPT = """
你是一个有能力调用外部工具的智能助手。你必须严格遵循以下 ReAct 流程：

1. Thought: 分析当前问题，规划下一步行动。
2. Action: 调用一个工具来执行行动。
   - 如果需要操作浏览器/文件系统/技能，调用对应的 MCP 工具或技能工具。
   - 当任务全部完成时，必须调用 finish_work 工具来结束任务。

重要规则：
- 每轮只能调用一个或一组工具，不能同时输出思考内容和工具调用之外的文字。
- 工具执行结果会返回给你，请根据结果继续思考下一步。
- 不要重复相同的无效操作。
"""

MAX_STEP = 20


class ReactLoop(LLMConfig):

    def __init__(
        self,
    ) -> None:
        super().__init__()
        self._startup: bool = False 
        _agent_dir = Path(__file__).parent
        self.mcp_manager: McpServerManager = McpServerManager(_agent_dir / "mcp_config.json")
        self.skill_manager: SkillManager = SkillManager(_agent_dir / "react_skill")
        

    async def startup(self) -> None:
        await self.mcp_manager.startup()
        mcp_tools = self.mcp_manager.get_openai_tools()
        self.all_tools = [*mcp_tools]
        await self.skill_manager.discover()

    async def runtime(
        self, *, task: str, system_prompt: str = REACT_LOOP_PROMPT
    ) -> str | None:

        if self._startup is False:
            await self.startup()
            self._startup = True

        self.messages.append({"role": "system", "content": system_prompt})
        self.messages.append({"role": "user", "content": task})

        last_skill_name: str | None = None

        for turn in range(MAX_STEP):
            logger.info("第 %d/%d 轮", turn + 1, MAX_STEP)

            skill_dispatch = await self.skill_manager.dispatch(self.messages)

            if skill_dispatch:
                skill_name = skill_dispatch.get("skill_name", "")
                if skill_name and skill_name != last_skill_name:
                    last_skill_name = skill_name
                    self.messages.extend(skill_dispatch["messages"])

            message = await self.chat_text(self.messages, tools=self.all_tools)
            self.messages.append(message.model_dump())

            if not message.tool_calls:
                if message.content:
                    return message.content  # 普通文字回复直接返回
                continue

            fn_calls = [tc for tc in message.tool_calls if tc.type == "function"]

            for tc in fn_calls:
                tool_args = json.loads(tc.function.arguments)
                result = await self.mcp_manager.call_tool(
                    tc.function.name, tool_args
                )
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    }
                )


async def main():
    task = "今天关于agent的新概念是什么？"
    loop = ReactLoop()
    result = await loop.runtime(task=task)
    logger.info("ReAct 运行结果: %s", result)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
