from pydantic import BaseModel, Field


class McpConfig(BaseModel):
    mcp_config: dict[str, dict[str, str]] = Field(default_factory=dict, description="mcp configuration")

class McpServer(BaseModel):
    mcp_name: str = Field(description="mcp server name")
    mcp_tools: list[dict[str, str]] = Field(default_factory=list, description="mcp tools")
