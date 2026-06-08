import json
import asyncio
from pathlib import Path
from fastmcp import Client
from fastmcp.mcp_config import MCPConfig
from fastmcp.tools import Tool


class McpServerManager:
    def __init__(self, path: Path):
        self.path = path
        self.mcp_clients_map: dict[str, Client] = {} 
        self.mcp_tools_map: dict[str, list[Tool]] = {}

    async def startup(self) -> None:
        with open(self.path, "r", encoding="utf-8") as f:
            mcp_configs = MCPConfig.from_dict(json.load(f))
            for service_name, server_model in mcp_configs.mcpServers.items():
                try:
                    mcp_client = Client({service_name: server_model.model_dump()})
                    await mcp_client.__aenter__()
                    tools = await mcp_client.list_tools()
                    self.mcp_tools_map[service_name] = tools  # ty:ignore[invalid-assignment]
                    self.mcp_clients_map[service_name] = mcp_client  
                except Exception as e:
                    print(f"[WARN] 跳过 {service_name}: {e}")
    
    async def shutdown(self) -> None:
        for mcp_client in self.mcp_clients_map.values():
            await mcp_client.__aexit__(None, None, None)
    
    async def call_tool(self, tool_name: str, args: dict[str, str]) -> str:
        for service_name, client in self.mcp_clients_map.items():
            prefix = f"{service_name}."
            if tool_name.startswith(prefix):
                raw_name = tool_name[len(prefix):]
                result = await client.call_tool(raw_name, args)
                return str(result)
        raise ValueError(f"unknown MCP tool: {tool_name}")


async def main():
    mcp_sever_manager = McpServerManager(path=Path(__file__).parent / "mcp_config.json")
    await mcp_sever_manager.startup()
    for name, tools in mcp_sever_manager.mcp_tools_map.items():
        print(f"{name}: {tools}")

if __name__ == "__main__":
    asyncio.run(main())