import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from simagentplg.logger import get_logger

logger = get_logger("skill")

_FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
LOAD_SKILL_TOOL_NAME = "load_skill"


@dataclass(frozen=True)
class Skill:
    name: str
    root: Path
    skill_md: Path
    description: str = ""
    frontmatter: dict[str, Any] = field(default_factory=dict)
    template_md: Path | None = None
    sample_md: Path | None = None


class SkillManager:
    """Local skill index with deterministic discovery and on-demand loading."""

    def __init__(self, skills_root: str | Path | None = None):
        """
        Args:
            skills_root: Root directory whose child folders may contain SKILL.md.
        """
        if skills_root is None:
            skills_root = Path(__file__).parent / "my_skills"
        self.skills_root = Path(skills_root)
        self._skills: dict[str, Skill] = {}
        self._discovered = False
        logger.info("技能注册表初始化，根目录: %s", self.skills_root)

    @property
    def skills(self) -> tuple[Skill, ...]:
        return tuple(self._skills.values())

    async def discover(self) -> None:
        """Scan child directories containing SKILL.md and build an index."""

        if not self.skills_root.exists():
            raise FileNotFoundError(f"skills root not found: {self.skills_root}")

        skills: dict[str, Skill] = {}

        for child in sorted(self.skills_root.iterdir()):
            if not child.is_dir():
                continue

            skill_md = child / "SKILL.md"
            if not skill_md.exists():
                continue

            frontmatter = self._read_frontmatter(skill_md)
            name = self._skill_name(child, frontmatter)
            template_md = child / "template.md"
            sample_md = child / "examples" / "sample.md"

            if name in skills:
                raise ValueError(f"duplicate skill name: {name!r}")

            skills[name] = Skill(
                name=name,
                root=child,
                skill_md=skill_md,
                description=str(frontmatter.get("description", "")),
                frontmatter=frontmatter,
                template_md=template_md if template_md.exists() else None,
                sample_md=sample_md if sample_md.exists() else None,
            )

        self._skills = skills
        self._discovered = True

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
        messages: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Compatibility shim: load only an explicitly named local skill.

        This no longer calls an LLM router. A skill is selected only when the
        user names it with ``$skill_name`` or ``skill:skill_name``.
        """

        if not self._discovered:
            await self.discover()

        skill_name = self.select_explicit_skill(messages)
        if skill_name is None:
            return None

        task = self._latest_user_task(messages)
        return {
            "skill_name": skill_name,
            "task": task,
            "messages": self.build_skill_messages(skill_name, task),
        }

    def build_index_message(self) -> dict[str, str] | None:
        """Return compact skill metadata for the model context."""

        if not self._skills:
            return None

        lines = [
            "Local skills are available. Use a skill when its description matches",
            "the user's task. If the user names a skill as $skill_name or",
            "skill:skill_name, its full instructions are loaded separately.",
            "",
            "Available skills:",
        ]
        for skill in self._skills.values():
            description = skill.description or "No description provided."
            lines.append(f"- {skill.name}: {description}")
        return {
            "role": "system",
            "content": "\n".join(lines),
        }

    def build_load_skill_tool(self) -> dict[str, Any] | None:
        """Return the internal tool schema used for on-demand skill loading."""

        if not self._skills:
            return None

        return {
            "type": "function",
            "function": {
                "name": LOAD_SKILL_TOOL_NAME,
                "description": (
                    "Load the full instructions for a local skill after its "
                    "metadata indicates that it is useful for the current task."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill_name": {
                            "type": "string",
                            "enum": list(self._skills.keys()),
                            "description": "The local skill to load.",
                        },
                    },
                    "required": ["skill_name"],
                    "additionalProperties": False,
                },
            },
        }

    def load_skill(self, skill_name: str) -> dict[str, str]:
        """Validate and report that a skill has been loaded."""

        skill = self.get(skill_name)
        description = skill.description or "No description provided."
        return {
            "status": "success",
            "skill_name": skill.name,
            "description": description,
            "message": (
                f"Loaded local skill {skill.name!r}. The next model turn "
                "will include its full instructions."
            ),
        }

    def build_skill_context_message(
        self,
        skill_name: str,
    ) -> dict[str, str]:
        """Load full skill instructions for provider context."""

        skill = self.get(skill_name)
        return {
            "role": "system",
            "content": "\n".join(self._skill_content_parts(skill)),
        }

    def build_skill_messages(
        self,
        skill_name: str,
        task: str,
    ) -> list[dict[str, str]]:
        """Load full skill instructions plus the current skill task."""

        return [
            self.build_skill_context_message(skill_name),
            {"role": "user", "content": task},
        ]

    def get(self, skill_name: str) -> Skill:
        try:
            return self._skills[skill_name]
        except KeyError as exc:
            available = ", ".join(self._skills) or "none"
            raise KeyError(
                f"unknown skill {skill_name!r}; available skills: {available}"
            ) from exc

    def select_explicit_skill(
        self,
        messages: list[dict[str, Any]],
    ) -> str | None:
        """Return a locally named skill from the latest user message."""

        task = self._latest_user_task(messages)
        if not task:
            return None

        for name in self._skills:
            if re.search(rf"(?<!\w)\${re.escape(name)}(?!\w)", task):
                return name
            if re.search(
                rf"(?<!\w)skill:{re.escape(name)}(?!\w)",
                task,
                flags=re.IGNORECASE,
            ):
                return name
        return None

    @staticmethod
    def _read_frontmatter(skill_md: Path) -> dict[str, Any]:
        text = skill_md.read_text(encoding="utf-8")
        match = _FRONTMATTER_PATTERN.match(text)
        if match is None:
            return {}

        data = yaml.safe_load(match.group(1)) or {}
        if not isinstance(data, dict):
            raise ValueError(f"SKILL.md frontmatter must be a mapping: {skill_md}")
        return dict(data)

    @staticmethod
    def _skill_name(
        skill_dir: Path,
        frontmatter: dict[str, Any],
    ) -> str:
        raw_name = frontmatter.get("name") or skill_dir.name
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise ValueError(f"skill name must be a non-empty string: {skill_dir}")
        return raw_name.strip()

    @staticmethod
    def _latest_user_task(messages: list[dict[str, Any]]) -> str:
        for message in reversed(messages):
            if message.get("role") == "user":
                content = message.get("content", "")
                return content if isinstance(content, str) else str(content)
        return ""

    @staticmethod
    def _skill_content_parts(skill: Skill) -> list[str]:
        parts = [
            f'You are executing the local skill "{skill.name}".',
            "",
            "[SKILL.md]",
            skill.skill_md.read_text(encoding="utf-8").strip(),
        ]

        if skill.template_md is not None:
            parts.extend(
                [
                    "",
                    "[template.md]",
                    skill.template_md.read_text(encoding="utf-8").strip(),
                ]
            )

        if skill.sample_md is not None:
            parts.extend(
                [
                    "",
                    "[examples/sample.md]",
                    skill.sample_md.read_text(encoding="utf-8").strip(),
                ]
            )
        return parts
