import asyncio
from pathlib import Path

from allagent.plugins import SkillManager


async def main() -> None:
    """SkillManager 功能验证入口：发现技能并通过 LLM 路由一条示例输入。"""
    here = Path("/Users/jyh030112/Desktop/Dev/All-Agent/src/allagent/plugins/skill")
    skills_root = here / "my_skills"

    registry = SkillManager(skills_root)
    await registry.discover()

    messages = [{"role": "user", "content": "帮我写一份周报"}]
    payload = await registry.dispatch(messages)
    if payload:
        print("Selected skill:", payload["skill_name"])
        print("Task:", payload["task"])
        print("Messages:")
        for message in payload["messages"]:
            print(f"- {message['role']}: {message['content'][:120]}...")
    else:
        print("未匹配到技能")


if __name__ == "__main__":
    asyncio.run(main())
