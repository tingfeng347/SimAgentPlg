# SimAgentPlg

[English](README.md) | [简体中文](README_zh-CN.md)

SimAgentPlg 0.2.4 是一个轻量级框架，用于构建有状态的
OpenAI 兼容 Agent、可组合工具 Handler、可选 MCP 工具，以及本地 Skill
发现、索引和按需加载。

## 功能特性

- 有状态的 `BaseAgent`，支持对话记忆和显式 `reset()`
- 可观测的 `AgentState`，保存持久历史和当前任务状态
- `AgentContextBuilder`，为每轮模型调用构建不修改历史的临时上下文
- 每个 Agent 拥有必填且不可修改的 `agent_id`
- 支持通过 `.env` 或直接构造使用 OpenAI 兼容模型配置
- Handler 驱动的工具执行，不需要单独的工具模式开关
- 内置 `BashHandler`，用于执行有边界的 Bash 命令
- 内置 `GitDiffHandler`，用于查看 Git 工作区变化
- 内置 `FinishHandler`，用于明确结束任务
- `MethodToolHandler` 用于快速定义小型 Python 自定义工具
- 可选 MCP 集成：`McpToolHandler` 和 `McpServerManager`
- 可选本地 Skill 发现、索引和按需加载：`SkillManager`

需要 Python 3.12 或更高版本。

## 安装

使用 `uv` 安装本地项目和依赖：

```bash
uv sync
```

## 配置

复制 `.env.example` 为 `.env`，然后填写模型凭据：

```env
MODEL_API_KEY=sk-xxxxxxxx
MODEL_URL=https://api.deepseek.com
CHAT_MODEL=deepseek-v4-flash
LLM_TIMEOUT=60
LLM_TEMPERATURE=0.7
```

`ModelConfig.from_env()` 会读取 `CHAT_MODEL`、`MODEL_API_KEY`、
`MODEL_URL`、`LLM_TIMEOUT` 和 `LLM_TEMPERATURE`。

也可以直接构造配置：

```python
from simagentplg import ModelConfig

config = ModelConfig(
    model="deepseek-v4-flash",
    api_key="sk-xxxxxxxx",
    base_url="https://api.deepseek.com",
)
```

## 快速开始

### 普通对话

默认不启用工具。普通 Agent 会在多次 `runtime()` 调用之间保留对话历史：

```python
from simagentplg import BaseAgent, ModelConfig

agent = BaseAgent(
    config=ModelConfig.from_env(),
    agent_id="tutor",
    system_prompt="你是一名回答简洁的 Python 导师。",
)

first = await agent.runtime(task="请记住我更喜欢 Python。")
second = await agent.runtime(task="我更喜欢哪种编程语言？")

agent.reset()
await agent.shutdown()
```

同一 Agent 的调用会串行执行，以保护其对话历史。

### 工具模式

显式传入 Handler 即可启用工具执行：

```python
import json

from simagentplg import (
    BaseAgent,
    BashHandler,
    FinishHandler,
    GitDiffHandler,
    ModelConfig,
)

agent = BaseAgent(
    config=ModelConfig.from_env(),
    agent_id="developer",
    system_prompt="使用可用工具完成编程任务。",
    handlers=[BashHandler(), GitDiffHandler(), FinishHandler()],
)

result = await agent.runtime(task="创建 hello.py，并输出 'hello'。")
report = json.loads(result)
print(report["summary"])

await agent.shutdown()
```

Agent 只会暴露显式传给 `BaseAgent` 的 Handler：

```text
BaseAgent
  -> BashHandler
       -> bash_run
  -> GitDiffHandler
       -> run_gitdiff
  -> FinishHandler
       -> run_finish
  -> MethodToolHandler 子类
  -> McpToolHandler
```

在工具模式下，普通文本不会结束任务。模型必须调用一个完成工具，通常是
`run_finish`；自定义工具也可以返回
`StepOutcome(..., should_exit=True)` 来结束任务。

以下情况会导致工具模式报错：

- 在 `max_steps` 内没有调用任何完成工具
- 相同工具和参数连续出现三次

## 内置 Handler

`BashHandler` 暴露 `bash_run`，用于执行有边界的 Bash 命令。它支持工作目录、
超时和输出长度限制。

`GitDiffHandler` 暴露 `run_gitdiff`。它查看当前 Git 工作区变化，不会结束任务：

```json
{
  "status": "success",
  "mode": "status",
  "command": "git status --short",
  "output": "?? hello.py\n"
}
```

支持的模式包括：`status` 对应 `git status --short`，`stat` 对应
`git diff --stat`，`diff` 对应 `git diff`。

`FinishHandler` 暴露 `run_finish`。它返回 JSON 结果，并立即结束当前
`runtime()`：

```json
{
  "summary": "已创建 hello.py"
}
```

## Tool Middleware

`ToolMiddleware` 会装饰一次工具执行，可在下一层装饰器或 Handler 前后运行
逻辑。框架不内置高低风险规则，业务可以继承 middleware 自行分类。`BashApprovalMiddleware` 是人工审批门，
不是 shell 沙箱或安全边界。默认情况下，safe allowlist 之外的命令需要 y/n
审批：

```python
from simagentplg import (
    BaseAgent,
    BashApprovalMiddleware,
    BashHandler,
    FinishHandler,
    ModelConfig,
)

agent = BaseAgent(
    ModelConfig.from_env(),
    agent_id="coder",
    handlers=[BashHandler(), FinishHandler()],
    middlewares=[BashApprovalMiddleware()],
)
```

默认的 `approval_policy="unless_safe"` 只会让少量明确的只读命令跳过审批，
例如 `pwd`、`ls`、`git status`、`git diff`、`git log`、`rg`、`sed -n`、
`cat` 和 Python unittest 调用。设置 `approval_policy="always"` 可以审批
每个 `bash_run` 命令；`approval_policy="never"` 会显式关闭这一审批门。

## 自定义工具 Handler

`MethodToolHandler` 会把名为 `add` 的工具映射到异步方法 `do_add`：

```python
from collections.abc import Mapping
from typing import Any

from simagentplg import BaseAgent, MethodToolHandler, ModelConfig, StepOutcome

ADD_TOOL = {
    "type": "function",
    "function": {
        "name": "add",
        "description": "Add two numbers.",
        "parameters": {
            "type": "object",
            "properties": {
                "left": {"type": "number"},
                "right": {"type": "number"},
            },
            "required": ["left", "right"],
        },
    },
}


class MathHandler(MethodToolHandler):
    def __init__(self) -> None:
        super().__init__((ADD_TOOL,))

    async def do_add(self, arguments: Mapping[str, Any]) -> StepOutcome:
        return StepOutcome(
            {"value": arguments["left"] + arguments["right"]}
        )


agent = BaseAgent(
    config=ModelConfig.from_env(),
    agent_id="calculator",
    handlers=[MathHandler()],
)
```

Handler 启动时会创建统一的工具路由表。重复工具名会立即报错，不会静默覆盖。

## MCP 工具

MCP 是可选功能，并使用相同的 Handler 接口：

```python
from simagentplg import BaseAgent, McpToolHandler, ModelConfig

agent = BaseAgent(
    config=ModelConfig.from_env(),
    agent_id="browser",
    handlers=[McpToolHandler("examples/mcp_config.json")],
)
```

MCP 配置示例：

```json
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": ["@playwright/mcp@latest", "--headless"]
    }
  }
}
```

`McpServerManager` 会加载配置的服务，用服务名前缀暴露工具，并允许单个服务
启动失败时不阻塞其他服务。

## Skill

Skill 是可选的提示词扩展，与工具 Handler 相互独立：

```python
from pathlib import Path

from simagentplg import BaseAgent, FinishHandler, ModelConfig

agent = BaseAgent(
    config=ModelConfig.from_env(),
    agent_id="skilled-agent",
    handlers=[FinishHandler()],
    skills_dir=Path("examples/skills"),
)
```

`SkillManager` 会扫描每个包含 `SKILL.md` 的子目录，索引 Skill 名称和
YAML front matter 中的描述，并把紧凑 metadata 注入模型上下文。完整
`SKILL.md`、可选 `template.md` 和可选 `examples/sample.md` 会在模型调用
内置 `load_skill` 工具时按需加载。用户也可以用 `$skill_name` 或
`skill:skill_name` 强制指定 Skill。

```text
examples/skills/
  release_notes/
    SKILL.md
    template.md
    examples/
      sample.md
```

Skill 上下文本身不要求 Handler 工具。只有当任务需要通过 `run_finish`
结束时，才需要注册 `FinishHandler`。

## 示例

可运行案例位于 [`examples/`](examples/README.md)：

```bash
uv run python examples/01_stateful_chat.py
uv run python examples/02_custom_tool.py
uv run python examples/04_mcp_tools.py
uv run python examples/06_skill.py
uv run python examples/07_bash_approval.py
```

## 测试

在仓库根目录运行测试：

```bash
uv run python -m unittest
```

当前测试覆盖 Agent、Custom Handler、Tool Middleware、Finish 行为，以及示例
文件是否可导入。

## 公共 API

```python
BaseAgent(
    config: ModelConfig | None = None,
    *,
    agent_id: str,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    handlers: Iterable[BaseHandler] | None = None,
    middlewares: Iterable[ToolMiddleware] | None = None,
    skills_dir: str | Path | None = None,
    context_builder: AgentContextBuilder | None = None,
    max_steps: int = 20,
    client: Any | None = None,
)

await agent.runtime(*, task: str) -> str | None
agent.reset(history=None)
await agent.startup()
await agent.shutdown()
```

`agent.state` 保存持久对话，以及当前任务的状态、轮数、激活 Skill、结果和错误。
`AgentContextBuilder` 会从该状态派生每轮完整模型请求（包括结构化工具定义），
不会修改历史消息。

传入 Handler 后，`BaseAgent` 会进入工具执行模式，并注入 runtime 内部工具
协议 system message。没有 Handler 时，Agent 是普通聊天；`skills_dir` 仍可
暴露内部 `load_skill` 上下文工具，但不会要求完成工具。

顶层包导出了 `BaseAgent`、`ModelConfig`、`AgentState`、`AgentStatus`、
`AgentContextBuilder`、`ContextBuildResult`、`StepOutcome`、Handler 基类、
`MethodToolHandler`、`BashHandler`、
`GitDiffHandler`、`FinishHandler`、`McpToolHandler`、Handler 错误类型、
`McpServerManager`、`SkillManager` 以及默认资源路径。

## 0.2.4 版本变化

- 新增与 `BashHandler` 同级的 `FinishHandler` 和内置 `run_finish` 工具
- 新增与 `BashHandler` 同级的 `GitDiffHandler` 和内置 `run_gitdiff` 工具
- 工具模式必须显式调用完成工具才算完成
- 连续三次调用相同工具和参数时提前终止
- 工具模式耗尽 `max_steps` 时抛出明确错误
- `BashHandler` 只负责提供 `bash_run`

## 许可证

MIT
