import asyncio
from pathlib import Path
from allagent.plugins import McpServerManager


async def main():
    mcp_sever_manager = McpServerManager()
    await mcp_sever_manager.startup()
    mcp_sever_manager.get_openai_tools()
    await mcp_sever_manager.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
