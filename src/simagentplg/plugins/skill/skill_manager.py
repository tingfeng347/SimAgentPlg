from typing import Optional
import json
import os
import re
import yaml

from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
from simagentplg.logger import get_logger

load_dotenv()
logger = get_logger("skill")


@dataclass(frozen=True)
class Skill:
    name: str
    root: Path
    skill_md: Path
    template_md: Path | None = None
    sample_md: Path | None = None


class SkillManager:
    """技能注册表，扫描本地技能目录并通过 LLM 路由用户请求到匹配的技能。

    核心流程：
    1. discover() — 扫描 skills_root 下含 SKILL.md 的子目录，注册技能
    2. dispatch() — 用 LLM 从自然语言输入中选择技能并构建 messages
    3. _build_messages() — 将技能定义、模板、示例拼装为 LLM 对话格式
    """

    def __init__(self, skills_root: str | Path | None = None):
        """
        Args:
            skills_root: 技能根目录路径，子目录中含 SKILL.md 的会被识别为技能。
        """
        if skills_root is None:
            skills_root = Path(__file__).parent / "my_skills"
        self.skills_root = Path(skills_root)
        self._skills: dict[str, Skill] = {}
        self._discovered: bool = False
        logger.info("技能注册表初始化，根目录: %s", self.skills_root)

    async def discover(self) -> None:
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

        if not self._skills:
            logger.debug(
                "技能扫描完成，未发现任何技能（skills_root: %s）", self.skills_root
            )
        else:
            logger.info(
                "发现 %d 个技能: %s", len(self._skills), list(self._skills.keys())
            )

    async def dispatch(
        self,
        messages: list[dict],
    ) -> Optional[dict]:
        """用 LLM 根据对话历史自动匹配技能并构建对话消息。

        Args:
            messages: 当前对话历史消息列表。

        Returns:
            if not self._discovered:
                包含 skill_name、task、me
                self._discovered = True
            if not self._skills:
                # 启动时已经扫过一次，仍为空：直接跳过 LLM 路由，避免无意义 IO + 同步阻塞
                return Nonessages 的 payload 字典。
            若无匹配返回 None。
        """
        if not self._skills:
            await self.discover()

        client = OpenAI(
            api_key=os.environ["MODEL_API_KEY"],
            base_url=os.environ["MODEL_URL"],
        )

        def _read_skill_frontmatter(skill: Skill) -> dict[str, str]:
            """解析 SKILL.md 的 YAML 头部。"""
            text = skill.skill_md.read_text(encoding="utf-8")
            match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
            if match:
                return yaml.safe_load(match.group(1))
            return {}

        skill_list: str = "\n".join(
            f"- {name}: {_read_skill_frontmatter(skill)}"
            for name, skill in self._skills.items()
        )

        # 提取原始任务和最新进展，构建紧凑上下文
        task = ""
        latest = ""
        for m in reversed(messages):
            if m.get("role") == "assistant" and not latest:
                latest = m.get("content", "")
            if m.get("role") == "user" and not task:
                task = m.get("content", "")

        context = f"原始任务: {task}"
        if latest:
            context += f"\n当前进展: {latest[:300]}"

        response = client.chat.completions.create(
            model=os.environ.get("SKILL_MODEL", "gpt-4o-mini"),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是一个技能路由器。根据用户输入和当前进展，选择最匹配的技能并提取任务描述。\n"
                        "可用技能列表：\n" + skill_list
                    ),
                },
                {"role": "user", "content": context},
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
            return None

        args = json.loads(
            msg.tool_calls[0].function.arguments  # ty:ignore[unresolved-attribute]
        )

        skill_name: str = args.get("skill_name", "")
        task: str = args.get("task", "")

        try:
            skill = self._skills[skill_name]
        except KeyError:
            logger.warning(
                "LLM 返回了未知技能: %s，可用: %s，跳过本次路由",
                skill_name,
                list(self._skills.keys()),
            )
            return None

        logger.info(f"LLM 路由结果 — 技能: {skill_name}, 任务: {task}")
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
