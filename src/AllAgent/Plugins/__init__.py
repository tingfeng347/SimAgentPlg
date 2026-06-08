"""插件系统 — MCP 服务管理 & Skill 技能路由。"""

from allagent.plugins.mcp.mcp_manager import McpServerManager
from allagent.plugins.skill.skillRegistyr import SkillRegistry

__all__ = ["McpServerManager", "SkillRegistry"]
