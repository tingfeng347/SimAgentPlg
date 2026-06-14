"""Paths to resources bundled with SimAgentPlg."""

from pathlib import Path

RESOURCES_DIR = Path(__file__).parent
DEFAULT_MCP_CONFIG = RESOURCES_DIR / "mcp_config.json"
DEFAULT_SKILLS_DIR = RESOURCES_DIR / "skills"

__all__ = ["RESOURCES_DIR", "DEFAULT_MCP_CONFIG", "DEFAULT_SKILLS_DIR"]
