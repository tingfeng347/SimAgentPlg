# SimAgentPlg

[English](README.md) | [简体中文](README_zh-CN.md)

SimAgentPlg 0.2.1 是一个轻量级多智能体框架，用于构建有状态的
OpenAI 兼容 Agent、可组合工具 Handler、可选 MCP 工具、本地 Skill 路由，
以及简单的基于角色的多 Agent 工作流。

## 功能特性

- 有状态的 `BaseAgent`，支持对话记忆和显式 `reset()`
- 每个 Agent 拥有必填且不可修改的 `agent_id`
- 支持通过 `.env` 或直接构造使用 OpenAI 兼容模型配置
- 工具模式默认关闭，启用后只暴露显式注册的 Handler
- 内置 `BashHandler`，用于执行有边界的 Bash 命令
- 内置 `FinishHandler`，用于明确结束任务并报告 Git 文件变化
- `MethodToolHandler` 用于快速定义小型 Python 自定义工具
- `AgentManager` 支持同一 Agent 串行、不同 Agent 并发
- 线性 `AgentWorkflow`，适合 planner、executor、reviewer 等角色
- 可选 MCP 集成：`McpToolHandler` 和 `McpServerManager`
- 可选本地 Skill 发现和路由：`SkillManager`

需要 Python 3.12 或更高版本。

## 安装

使用 `uv` 安装本地项目和依赖：

```bash
uv sync
```

## 配置

复制 `.env_example` 为 `.env`，然后填写模型凭据：

```env
MODEL_API_KEY=sk-xxxxxxxx
MODEL_URL=https://api.deepseek.com
CHAT_MODEL=deepseek-v4-flash
SKILL_MODEL=deepseek-v4-flash
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

### 工具模式

设置 `enable_tools=True` 并显式传入 Handler：

```python
import json

from simagentplg import BaseAgent, BashHandler, FinishHandler, ModelConfig

agent = BaseAgent(
    config=ModelConfig.from_env(),
    agent_id="developer",
    system_prompt="使用可用工具完成编程任务。",
    handlers=[BashHandler(), FinishHandler()],
    enable_tools=True,
)

result = await agent.runtime(task="创建 hello.py，并输出 'hello'。")
report = json.loads(result)
print(report["summary"])
print(report["changes"])

await agent.shutdown()
```

工具模式只会暴露显式传给 `BaseAgent` 的 Handler：

```text
BaseAgent
  -> BashHandler
       -> bash_run
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
超时、输出长度限制，并包含一个针对明显危险命令的小型黑名单。

`FinishHandler` 暴露 `run_finish`。它返回 JSON 结果，并立即结束当前
`runtime()`：

```json
{
  "summary": "已创建 hello.py",
  "changes": {
    "available": true,
    "repository": "/repo/root",
    "added": ["hello.py"],
    "modified": [],
    "deleted": []
  }
}
```

文件变化通过比较本次任务开始和结束时的 Git 状态得到。`run_finish` 不会提交、
暂存或回滚文件。在非 Git 目录中仍然可以完成任务，此时 `changes.available`
为 `false`。

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
    enable_tools=True,
)
```

Handler 启动时会创建统一的工具路由表。重复工具名会立即报错，不会静默覆盖。

## Agent Manager

每个 Agent 自己持有身份，因此注册时不需要再次传入 ID：

```python
from simagentplg import AgentManager, BaseAgent, ModelConfig

config = ModelConfig.from_env()
manager = AgentManager()

manager.register(
    BaseAgent(
        config=config,
        agent_id="writer",
        system_prompt="你负责编写简洁的版本说明。",
    )
)
manager.register(
    BaseAgent(
        config=config,
        agent_id="reviewer",
        system_prompt="你负责审查软件改动中的风险。",
    )
)

results = await manager.run_many(
    {
        "writer": "编写 0.2.1 版本说明。",
        "reviewer": "审查本次发布的兼容性风险。",
    }
)

await manager.shutdown()
```

同一个 Agent 的调用会串行执行，因为它们共享同一份消息历史。不同 Agent
之间可以并发执行。`run_many()` 会将异常作为对应任务的结果返回，因此一个
Agent 失败不会取消其他 Agent。

`run_isolated(agent_id, task)` 会在持有该 Agent 锁的期间执行 `reset()` 和
任务。Workflow 使用它来避免角色或步骤之间产生隐式历史依赖。

## 多角色工作流

`AgentWorkflow` 可以将不同角色组织为经过校验的线性流水线：

```python
from simagentplg import (
    AgentManager,
    AgentWorkflow,
    BaseAgent,
    ModelConfig,
    WorkflowStep,
)

config = ModelConfig.from_env()
manager = AgentManager()
manager.register(
    BaseAgent(
        config=config,
        agent_id="planner",
        system_prompt="创建简洁且可执行的实现方案。",
    )
)
manager.register(
    BaseAgent(
        config=config,
        agent_id="executor",
        system_prompt="使用工具执行给定方案。",
        enable_tools=True,
    )
)
manager.register(
    BaseAgent(
        config=config,
        agent_id="reviewer",
        system_prompt="审查执行结果的正确性和风险。",
    )
)

workflow = AgentWorkflow(
    manager,
    [
        WorkflowStep(
            name="plan",
            agent_id="planner",
            prompt="规划以下任务：\n{input}",
        ),
        WorkflowStep(
            name="execute",
            agent_id="executor",
            prompt=(
                "原始任务：\n{original_task}\n\n"
                "执行以下方案：\n{input}"
            ),
        ),
        WorkflowStep(
            name="review",
            agent_id="reviewer",
            prompt="审查以下执行结果：\n{execute}",
        ),
    ],
)

result = await workflow.run("实现用户登录")
print(result.final_output)
await manager.shutdown()
```

Workflow 模板支持 `{input}`、`{original_task}`，以及已经完成的命名步骤输出，
例如 `{plan}` 或 `{execute}`。创建 Workflow 时会拒绝未知变量和对后续步骤
的前向引用。0.2.1 版本只支持线性步骤。

## MCP 工具

MCP 是可选功能，并使用相同的 Handler 接口：

```python
from simagentplg import BaseAgent, McpToolHandler, ModelConfig

agent = BaseAgent(
    config=ModelConfig.from_env(),
    agent_id="browser",
    handlers=[McpToolHandler("example/mcp_config.json")],
    enable_tools=True,
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

from simagentplg import BaseAgent, ModelConfig

agent = BaseAgent(
    config=ModelConfig.from_env(),
    agent_id="skilled-agent",
    skills_dir=Path("example/skills"),
    enable_tools=True,
)
```

`SkillManager` 会扫描每个包含 `SKILL.md` 的子目录。`SKILL_MODEL` 指定的
路由模型会根据 YAML front matter 选择匹配的 Skill。选中的 `SKILL.md`、
可选 `template.md` 和可选 `examples/sample.md` 会被注入 Agent 上下文。

```text
example/skills/
  release_notes/
    SKILL.md
    template.md
    examples/
      sample.md
```

当前 Skill 通过工具模式生命周期运行，因此需要设置 `enable_tools=True`，
并通过 `run_finish` 完成任务。

## 示例

可运行案例位于 [`example/`](example/README.md)：

```bash
uv run python example/01_stateful_chat.py
uv run python example/02_custom_tool.py
uv run python example/03_multi_agent.py
uv run python example/04_mcp_tools.py
uv run python example/05_role_workflow.py
uv run python example/06_skill.py
```

## 测试

在仓库根目录运行测试：

```bash
uv run python -m unittest
```

当前测试覆盖 Agent、Custom Handler、Finish 行为、Manager 锁和并发、
Workflow，以及示例文件是否可导入。

## 公共 API

```python
BaseAgent(
    config: ModelConfig | None = None,
    *,
    agent_id: str,
    system_prompt: str = REACT_LOOP_PROMPT,
    handlers: Iterable[BaseHandler] | None = None,
    enable_tools: bool = False,
    skills_dir: str | Path | None = None,
    max_steps: int = 20,
    client: Any | None = None,
)

await agent.runtime(*, task: str) -> str | None
agent.reset(history=None)
await agent.startup()
await agent.shutdown()
```

顶层包导出了 `BaseAgent`、`ModelConfig`、`StepOutcome`、`AgentManager`、
Workflow 类型、Handler 基类、`MethodToolHandler`、`BashHandler`、
`FinishHandler`、`McpToolHandler`、Handler 错误类型、`McpServerManager`、
`SkillManager` 以及默认资源路径。

## 0.2.1 版本变化

- 新增与 `BashHandler` 同级的 `FinishHandler` 和内置 `run_finish` 工具
- 新增单次任务范围内的 Git 文件变化报告
- 工具模式必须显式调用完成工具才算完成
- 连续三次调用相同工具和参数时提前终止
- 工具模式耗尽 `max_steps` 时抛出明确错误
- `BashHandler` 只负责提供 `bash_run`

## 许可证

MIT
