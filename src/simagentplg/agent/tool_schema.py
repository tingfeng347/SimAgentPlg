"""
本地工具 schema —— 纯数据定义，LLMConfig 默认可用的所有本地工具。

添加新工具只需追加一条 dict，同时在 LLMConfig 中定义对应的 do_xxx 方法。
"""

LOCAL_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "bash_run",
            "description": "在 Bash 沙箱中执行一段脚本代码，返回 stdout、退出码和状态。用于运行命令行、操作文件、安装依赖等操作。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "要执行的 Bash 脚本代码，支持多行",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "最长等待秒数，默认 60",
                    },
                },
                "required": ["code"],
            },
        },
    },
]
