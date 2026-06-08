import asyncio
from pathlib import Path

from allagent.plugins import SkillRegistry


def main() -> None:
    """SkillRegistry 功能验证入口：发现技能并通过 LLM 路由一条示例输入。"""
    here = Path(__file__).resolve().parent
    skills_root = here / "my_skills"

    registry = SkillRegistry(skills_root)
    asyncio.run(registry.discover())

    payload = asyncio.run(registry.dispatch("hi"))
    print("Selected skill:", payload["skill_name"])
    print("Task:", payload["task"])
    print("Messages:")
    for message in payload["messages"]:
        print(f"- {message['role']}: {message['content']}...")


if __name__ == "__main__":
    main()
