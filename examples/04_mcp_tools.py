"""Expose tools from an MCP server to an agent."""

import asyncio
from pathlib import Path

from simagentplg import BaseAgent, FinishHandler, McpToolHandler, ModelConfig

MCP_CONFIG = Path(__file__).with_name("mcp_config.json")


async def main() -> None:
    agent = BaseAgent(
        config=ModelConfig.from_env(),
        agent_id="browser",
        system_prompt=(
            "Use MCP tools to inspect the requested page. When finished, call "
            "run_finish with the page title and relevant result summary."
        ),
        handlers=[McpToolHandler(MCP_CONFIG), FinishHandler()],
    )

    try:
        result = await agent.runtime(
            task="Open https://baidu.com and report the page title."
        )
        print(result)
    finally:
        await agent.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
