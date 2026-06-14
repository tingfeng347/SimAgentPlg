"""Expose tools from an MCP server to an agent."""

import asyncio
from pathlib import Path

from simagentplg import BaseAgent, McpToolHandler, ModelConfig

MCP_CONFIG = Path(__file__).with_name("mcp_config.json")


async def main() -> None:
    agent = BaseAgent(
        config=ModelConfig.from_env(),
        handlers=[McpToolHandler(MCP_CONFIG)],
    )

    try:
        result = await agent.runtime(
            task="Open https://example.com and report the page title."
        )
        print(result)
    finally:
        await agent.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
