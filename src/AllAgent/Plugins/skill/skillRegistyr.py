import json
import asyncio
import os

from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
from allagent.logger import get_logger

load_dotenv()
logger = get_logger("skill")



@dataclass(frozen=True)
class Skill:
    name: str
    root: Path
    skill_md: Path
    template_md: Path | None = None
    sample_md: Path | None = None


class SkillRegistry:
    """技能注册表，扫描本地技能目录并通过 LLM 路由用户请求到匹配的技能。

    核心流程：
    1. discover() — 扫描 skills_root 下含 SKILL.md 的子目录，注册技能
    2. dispatch() — 用 LLM 从自然语言输入中选择技能并构建 messages
    3. _build_messages() — 将技能定义、模板、示例拼装为 LLM 对话格式
    """

    def __init__(self, skills_root: str | Path):
        """
        Args:
            skills_root: 技能根目录路径，子目录中含 SKILL.md 的会被识别为技能。
        """
        self.skills_root = Path(skills_root)
        self._skills: dict[str, Skill] = {}
        logger.info("技能注册表初始化，根目录: %s", self.skills_root)

    async def discover(self) -> dict[str, Skill]:
        """扫描技能根目录，注册所有含 SKILL.md 的子目录为技能。

        Returns:
            注册的技能字典 {name: Skill}。

        Raises:
            FileNotFoundError: 技能根目录不存在。
        """

        if not self.skills_root.exists():
            raise FileNotFoundError(f"skills root not found: {self.skills_root}")

        self._skills.clear()

        for child in sorted(self.skills_root.iterdir()):
            if not child.is_dir():
                continue

            skill_md = child / "SKILL.md"
            if not skill_md.exists():
                continue

            template_md = child / "template.md"
            sample_md = child / "examples" / "sample.md"

            self._skills[child.name] = Skill(
                name=child.name,
                root=child,
                skill_md=skill_md,
                template_md=template_md if template_md.exists() else None,
                sample_md=sample_md if sample_md.exists() else None,
            )

        logger.info("发现 %d 个技能: %s", len(self._skills), list(self._skills.keys()))
        return dict(self._skills)

    async def dispatch(
        self,
        user_text: str,
    ) -> dict:
        """用 LLM 从自然语言中自动匹配技能并构建对话消息。

        Args:
            user_text: 用户自然语言输入，如 "帮我写一份周报"。
            model: 用于路由的 OpenAI 模型名。

        Returns:
            包含 skill_name、task、messages 的 payload 字典。

        Raises:
            ValueError: LLM 返回的技能名未注册。
        """
        if not self._skills:
            await self.discover()

        client = OpenAI(
            api_key=os.environ["MODEL_API_KEY"],
            base_url=os.environ["MODEL_URL"],
        )
        logger.info("正在调用 LLM 路由技能，输入: %s", user_text[:80])

        skill_list = "\n".join(
            f"- {name}: {skill.skill_md.read_text(encoding='utf-8')[:200]}"
            for name, skill in self._skills.items()
        )

        response = client.chat.completions.create(
            model=os.environ.get("SKILL_MODEL", "gpt-4o-mini"),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是一个技能路由器。根据用户输入，选择最匹配的技能并提取任务描述。\n"
                        "可用技能列表：\n" + skill_list
                    ),
                },
                {"role": "user", "content": user_text},
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "select_skill",
                        "description": "选择要使用的技能并提取任务",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "skill_name": {
                                    "type": "string",
                                    "enum": list(self._skills.keys()),
                                    "description": "选择的技能名",
                                },
                                "task": {
                                    "type": "string",
                                    "description": "用户想要执行的具体任务",
                                },
                            },
                            "required": ["skill_name", "task"],
                        },
                    },
                }
            ],
        )

        msg = response.choices[0].message
        if not msg.tool_calls:
            logger.warning("LLM 未返回 tool call，无法匹配技能")
            return {"skill_name": "", "task": "", "messages": []}

        args = json.loads(msg.tool_calls[0].function.arguments)  # ty:ignore[unresolved-attribute]

        skill_name: str = args.get("skill_name", "")
        task: str = args.get("task", "")

        try:
            skill = self._skills[skill_name]
        except KeyError:
            logger.warning("LLM 返回了未知技能: %s，可用: %s", skill_name, list(self._skills.keys()))
            return {"skill_name": "", "task": task, "messages": []}

        logger.info("LLM 路由结果 — 技能: %s, 任务: %s", skill_name, task)
        return {
            "skill_name": skill.name,
            "task": task,
            "messages": self._build_messages(skill, task),
        }

    def _build_messages(self, skill: Skill, task: str) -> list[dict[str, str]]:
        """将技能定义、模板和示例拼装为 LLM 对话消息。

        Args:
            skill: 技能对象。
            task: 用户任务描述。

        Returns:
            [system_message, user_message] 格式的消息列表。
        """
        system_parts = [
            f'You are executing the local skill "{skill.name}".',
            "",
            "[SKILL.md]",
            skill.skill_md.read_text(encoding="utf-8").strip(),
        ]

        if skill.template_md is not None:
            system_parts.extend(
                [
                    "",
                    "[template.md]",
                    skill.template_md.read_text(encoding="utf-8").strip(),
                ]
            )

        if skill.sample_md is not None:
            system_parts.extend(
                [
                    "",
                    "[examples/sample.md]",
                    skill.sample_md.read_text(encoding="utf-8").strip(),
                ]
            )

        return [
            {"role": "system", "content": "\n".join(system_parts)},
            {"role": "user", "content": task},
        ]


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
