from pathlib import Path
import json
import asyncio
import logging

from AllAgent.struct import McpConfig
from AllAgent.logger import get_logger


async def main():
    json_path = Path(__file__).parent / "mcp_config.json"

    mcp_configs = McpConfig()

    with open(json_path, "r", encoding="utf-8") as f:
        mcp_configs.mcp_config = json.load(f)
    logger = get_logger(name="MCP-Test")
    logger.info(mcp_configs.mcp_config)

if __name__ == "__main__":
    asyncio.run(main())
    
