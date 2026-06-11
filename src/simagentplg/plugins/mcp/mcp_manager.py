import json
import asyncio
from pathlib import Path
from fastmcp import Client
from fastmcp.mcp_config import MCPConfig
from mcp.types import Tool
from simagentplg.logger import get_logger

logger = get_logger(name="MCP")


class McpServerManager:
    """MCP 多服务管理器，负责从 JSON 配置加载、连接和管理多个 MCP 服务。

    支持按服务名前缀路由工具调用，单个服务连接失败不影响其他服务。
    """

    def __init__(self, path: Path | None = None):
        """初始化管理器。

        Args:
            path: MCP 配置 JSON 文件路径。
        """
        if path is None:
            path = Path(__file__).parent / "mcp_config.json"
        self.path = path
        self.mcp_clients_map: dict[str, Client] = {}
        self.mcp_tools_map: dict[str, list[Tool]] = {}

    async def startup(self) -> None:
        """启动所有 MCP 服务连接。

        从 JSON 配置文件读取服务列表，逐个建立连接并拉取工具列表。
        单个服务连接失败会记录错误日志但不阻断其他服务的启动。
        """
        logger.info("MCP 服务管理器启动中...")
        with open(self.path, "r", encoding="utf-8") as f:
            mcp_configs = MCPConfig.from_dict(json.load(f))
            logger.info(f"加载到 {len(mcp_configs.mcpServers)} 个 MCP 服务配置")
            for service_name, server_model in mcp_configs.mcpServers.items():
                try:
                    logger.info(f"正在连接 {service_name} ...")
                    mcp_client = Client({service_name: server_model.model_dump()})
                    await mcp_client.__aenter__()
                    tools = await mcp_client.list_tools()
                    self.mcp_tools_map[service_name] = tools
                    self.mcp_clients_map[service_name] = mcp_client
                    logger.info(f"{service_name} 连接成功，加载 {len(tools)} 个工具")
                except Exception as e:
                    logger.error(f"连接 {service_name} 失败: {e}")
        logger.info(
            f"MCP 服务管理器启动完成，共 {len(self.mcp_clients_map)} 个服务在线"
        )

    async def shutdown(self) -> None:
        """关闭所有 MCP 服务连接，释放资源。"""
        logger.info("MCP 服务管理器关闭中...")
        for service_name, mcp_client in self.mcp_clients_map.items():
            await mcp_client.__aexit__(None, None, None)
            logger.info(f"{service_name} 已断开")
        logger.info("MCP 服务管理器已关闭")

    async def call_tool(self, tool_name: str, args: dict[str, str]) -> str:
        """调用 MCP 工具，按服务名前缀自动路由。

        Args:
            tool_name: 工具名，格式为 "{服务名}__{工具名}"，如 "playwright__browser_navigate"。
            args: 传递给工具的参数字典。

        Returns:
            工具执行结果的字符串表示。

        Raises:
            ValueError: 当工具名不存在于任何已连接的服务中时抛出。
        """
        logger.info(f"调用工具: {tool_name}, 参数: {args}")
        for service_name, client in self.mcp_clients_map.items():
            prefix = f"{service_name}__"
            if tool_name.startswith(prefix):
                raw_name = tool_name[len(prefix) :]
                result = await client.call_tool(raw_name, args)
                logger.info(f"工具 {tool_name} 执行成功")
                return str(result)
        logger.error(f"未知的 MCP 工具: {tool_name}")
        raise ValueError(f"unknown MCP tool: {tool_name}")

    def get_openai_tools(self) -> list[dict]:
        """将所有已连接服务的工具转换为 OpenAI tools 格式。

        Returns:
            OpenAI tools 参数格式的列表。
        """
        openai_tools = []
        for service_name, tools in self.mcp_tools_map.items():
            for tool in tools:
                openai_tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": f"{service_name}__{tool.name}",
                            "description": tool.description,
                            "parameters": tool.inputSchema,
                        },
                    }
                )
        logger.info(f"转换到 OpenAI tools 格式的工具: {len(openai_tools)} 个工具")
        return openai_tools


async def main():
    mcp_manager = McpServerManager()
    await mcp_manager.startup()
    mcp_manager.get_openai_tools()
    await mcp_manager.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
