# Handoff：从 Pi Agent 学习 Agent Harness 工程设计

## 1. 背景

这份 handoff 总结 Pi Agent 对我们自己的 Agent Harness 工程有什么参考价值。Pi 的核心思路不是“写一个更长的提示词”，而是把 LLM 封装进一套可运行、可观察、可插拔、可恢复、可控工具调用的 Agent Runtime。`pi-agent-core` 负责抽象 Agent loop、状态、上下文、工具调用和事件；`pi-coding-agent` 在它之上实现编码场景里的系统提示词、项目上下文、工具、扩展、会话、压缩和重试。`pi-agent-core` 的类型定义里明确包含 `AgentState`、`AgentContext`、`AgentLoopConfig`、`transformContext`、`convertToLlm`、`beforeToolCall`、`afterToolCall`、steering/follow-up、`prepareNextTurn` 等关键扩展点。

---

## 2. 我们最应该学习的地方

### 2.1 不要把 Agent 做成一次性 LLM 调用，要做成 Runtime

普通 Agent demo 通常是：

```text
用户输入 → 拼 prompt → 调模型 → 输出
```

Pi 的结构更像：

```text
用户输入
  ↓
AgentState / AgentContext
  ↓
transformContext()
  ↓
convertToLlm()
  ↓
LLM Context
  ↓
模型流式输出
  ↓
工具调用
  ↓
工具结果回灌
  ↓
下一轮 LLM
  ↓
最终回复 / 会话持久化 / 压缩 / 重试
```

`agent-loop.ts` 里明确写了：Agent loop 内部一直使用 `AgentMessage`，只有到 LLM 调用边界才转换成 `Message[]`；在真正调用模型前，会先执行 `transformContext()`，再执行 `convertToLlm()`，最后组装 `{ systemPrompt, messages, tools }` 作为 LLM Context。

**我们可以学习：**

我们的 Agent 不应该只封装一个 `chat()` 函数，而应该有一个独立的 Runtime 层：

```text
AgentRuntime
├─ state
├─ context
├─ messages
├─ tools
├─ event stream
├─ tool middleware
├─ context transform
├─ LLM adapter
└─ lifecycle control
```

---

### 2.2 Agent 上下文和 LLM 上下文要分开

Pi 的一个关键设计是：

```text
AgentMessage[] ≠ LLM Message[]
```

Agent 内部可以保存更多业务消息，比如 bash 执行记录、压缩摘要、分支摘要、自定义扩展消息；但 LLM 最终能看到什么，要经过 `convertToLlm()` 决定。`pi-coding-agent` 的 `messages.ts` 就扩展了 `bashExecution`、`custom`、`branchSummary`、`compactionSummary` 等消息类型，并把它们转换成 LLM 兼容的 user/assistant/toolResult 消息；其中 `excludeFromContext` 的 bash 执行记录可以被过滤掉，不进入 LLM 上下文。

**我们可以学习：**

不要把所有历史记录都直接塞给模型。应该分两层：

```text
Agent 内部上下文
├─ 用户消息
├─ assistant 消息
├─ 工具结果
├─ UI 状态
├─ debug 信息
├─ 执行日志
├─ 长期记忆
├─ 压缩摘要
└─ 业务事件

LLM 可见上下文
├─ systemPrompt
├─ 必要历史消息
├─ 必要工具结果
├─ 摘要后的上下文
└─ 当前任务相关信息
```

也就是说，我们要做一个 `convertToLlmContext()`，让模型看到“该看的”，而不是看到“系统里保存的一切”。

---

### 2.3 上下文构建要有 hook，而不是写死

Pi 在 `AgentLoopConfig` 里定义了 `transformContext()`：它发生在 `convertToLlm()` 之前，适合做 AgentMessage 级别的上下文窗口管理、旧消息裁剪、外部上下文注入等。

`pi-coding-agent` 进一步把这个 hook 接到了扩展系统：`transformContext` 会调用 extension runner 的 `emitContext(messages)`，也就是说，外部扩展可以在每次 LLM 调用前修改上下文。

**我们可以学习：**

我们的上下文工程不要写死在一个函数里，应该分成几个 hook：

```text
beforeContextBuild()
retrieveMemory()
transformContext()
compressContext()
convertToLlm()
afterContextBuild()
```

这样后续可以插入：

```text
RAG 检索
用户画像注入
长期记忆召回
历史摘要压缩
工具结果压缩
权限策略注入
多 Agent 共享上下文
```

---

### 2.4 工具调用必须有中间件

Pi 不是让模型直接调用工具，而是在工具执行前后提供 hook：

```text
beforeToolCall
tool.execute()
afterToolCall
```

`beforeToolCall` 可以阻止工具执行，`afterToolCall` 可以改写工具结果、设置错误状态、决定是否提前终止。`AgentLoopConfig` 对这两个 hook 有明确设计。

Pi 的扩展文档里也给了具体场景：扩展可以监听 `tool_call`，例如发现 bash 命令里包含 `rm -rf` 时弹出确认框，用户拒绝就返回 `{ block: true }` 阻止执行。

**我们可以学习：**

我们的工具系统应该是：

```text
LLM 请求工具
  ↓
权限检查
  ↓
参数校验
  ↓
用户确认
  ↓
工具执行
  ↓
结果清洗
  ↓
结果压缩
  ↓
写回上下文
```

不能做成：

```text
LLM 请求工具 → 直接执行
```

尤其是涉及文件、命令、数据库、网络请求、发消息、支付、删除操作时，必须有 tool middleware。

---

### 2.5 事件流是 Agent UI 和调试的基础

Pi 的 `AgentEvent` 包含 agent lifecycle、turn lifecycle、message lifecycle、tool execution lifecycle，例如：

```text
agent_start
turn_start
message_start
message_update
message_end
tool_execution_start
tool_execution_update
tool_execution_end
turn_end
agent_end
```

这些事件让 UI 可以实时展示模型输出、工具调用状态、pending tool calls 和错误信息。

**我们可以学习：**

Agent Runtime 必须有事件总线，而不是只返回最终字符串。

建议我们的事件层至少支持：

```text
agent_started
turn_started
message_delta
message_completed
tool_started
tool_progress
tool_completed
tool_failed
context_compacted
permission_required
agent_completed
agent_failed
```

这样才能支持：

```text
终端 UI
Web UI
日志系统
回放系统
调试面板
审计系统
任务恢复
```

---

### 2.6 系统提示词要动态构建，而不是一个固定 prompt

`pi-coding-agent` 的 `buildSystemPrompt()` 会根据参数动态构造系统提示词：包括 custom prompt、当前启用工具、工具 snippets、guidelines、append system prompt、cwd、context files、skills 等。默认提示词会声明自己是 “expert coding assistant operating inside pi, a coding agent harness”，并加入可用工具、guidelines、Pi 文档路径、项目上下文、skills、当前日期和当前工作目录。

**我们可以学习：**

我们的系统提示词应该由 Prompt Builder 构建：

```text
SystemPromptBuilder
├─ agent identity
├─ role policy
├─ tool descriptions
├─ project rules
├─ user preferences
├─ memory summary
├─ safety rules
├─ current environment
├─ current date
└─ active workflow
```

不要把提示词散落在代码里，也不要让所有场景共用一个巨大 prompt。

---

### 2.7 项目上下文应该作为一等公民

Pi 的 coding agent 会把项目上下文文件、skills、cwd、date 等拼进 system prompt；它还支持 `SYSTEM.md`、`APPEND_SYSTEM.md`、`AGENTS.md` / `CLAUDE.md` 这类项目级说明文件。`buildSystemPrompt()` 里明确支持 `contextFiles` 和 `skills`，并会在 prompt 末尾追加当前日期和当前工作目录。

**我们可以学习：**

我们的 Agent 项目里也应该有类似机制：

```text
.agent/
├─ SYSTEM.md
├─ APPEND_SYSTEM.md
├─ PROJECT_RULES.md
├─ SKILLS/
├─ TOOLS/
├─ MEMORY.md
└─ EXTENSIONS/
```

这样不同项目可以有不同规则，而不是所有项目共享一套硬编码行为。

---

### 2.8 扩展系统是 Harness 的关键

Pi 的扩展文档说明，extensions 是 TypeScript modules，可以扩展 Pi 行为；它们可以订阅生命周期事件、注册 LLM 可调用工具、添加命令，并支持放在全局或项目本地目录，自动发现后可通过 `/reload` 热重载。

Pi 扩展的能力包括 custom tools、event interception、user interaction、custom UI components、custom commands、session persistence、custom rendering 等；文档列出的典型场景包括权限门禁、git checkpointing、路径保护、自定义 compaction、会话摘要、交互式工具、外部集成等。

**我们可以学习：**

Agent Harness 最好不要把所有能力写死，而是设计插件系统：

```text
Extension API
├─ onInput
├─ onContext
├─ onBeforeModelRequest
├─ onToolCall
├─ onToolResult
├─ onMessage
├─ onTurnEnd
├─ registerTool
├─ registerCommand
├─ registerProvider
└─ registerRenderer
```

这样后续可以快速接入：

```text
知识库
数据库
QQ群/微信/Slack
GitHub
CI/CD
权限审批
审计日志
游戏 NPC 系统
用户画像系统
```

---

### 2.9 要支持 steering 和 follow-up

Pi 的 `AgentLoopConfig` 里有 `getSteeringMessages()` 和 `getFollowUpMessages()`：steering 用于 Agent 工作中途注入用户新指令，follow-up 用于 Agent 本来要结束时继续处理排队任务。

**我们可以学习：**

真实 Agent 运行时，用户不会总是等它完成才说话。尤其在编码、群聊机器人、自动化任务里，用户可能中途改变目标。

所以我们的 Agent 要支持：

```text
steer    = 运行中修正方向
followUp = 当前任务结束后追加任务
abort    = 立即取消
pause    = 暂停等待用户
resume   = 继续执行
```

这会比“一次请求一次回复”的体验更接近真实助手。

---

### 2.10 要支持 prepareNextTurn

Pi 的 `prepareNextTurn()` 可以在每轮结束后返回新的 context/model/thinkingLevel，影响下一轮 provider request。

**我们可以学习：**

Agent 每一轮之后都应该有机会刷新运行状态：

```text
prepareNextTurn()
├─ 判断上下文是否快满
├─ 是否需要压缩
├─ 是否要切换模型
├─ 是否要改变 reasoning level
├─ 是否要刷新工具列表
├─ 是否要注入新记忆
└─ 是否要停止
```

这对于长任务非常重要。

---

## 3. 建议我们的 Agent Harness 架构

建议我们参考 Pi，把自己的 Agent 拆成四层：

```text
1. agent-core
   通用 Agent Runtime，不绑定具体业务。

2. agent-product
   具体产品层，比如 coding-agent、chat-agent、qq-agent、game-npc-agent。

3. model-adapter
   统一 OpenAI、Anthropic、本地模型、代理模型等。

4. ui-adapter
   终端 UI、Web UI、QQ/微信/Slack UI、日志 UI。
```

推荐目录：

```text
agent/
├─ core/
│  ├─ Agent.ts
│  ├─ AgentLoop.ts
│  ├─ AgentState.ts
│  ├─ AgentContext.ts
│  ├─ AgentMessage.ts
│  ├─ AgentEvent.ts
│  └─ ToolRuntime.ts
│
├─ context/
│  ├─ transformContext.ts
│  ├─ convertToLlm.ts
│  ├─ compaction.ts
│  ├─ memoryRecall.ts
│  └─ promptBuilder.ts
│
├─ tools/
│  ├─ registry.ts
│  ├─ middleware.ts
│  ├─ permissions.ts
│  └─ builtins/
│
├─ extensions/
│  ├─ ExtensionAPI.ts
│  ├─ ExtensionRunner.ts
│  └─ lifecycle.ts
│
├─ sessions/
│  ├─ SessionManager.ts
│  ├─ persistence.ts
│  └─ replay.ts
│
├─ models/
│  ├─ ModelRegistry.ts
│  ├─ providers/
│  └─ stream.ts
│
└─ products/
   ├─ coding-agent/
   ├─ qq-agent/
   └─ game-agent/
```

---

## 4. 我们可以直接落地的 MVP

第一阶段不要做太复杂，先实现一个最小 Harness：

```text
AgentRuntime
├─ state.messages
├─ state.tools
├─ state.systemPrompt
├─ prompt()
├─ continue()
├─ abort()
├─ subscribe()
├─ transformContext()
├─ convertToLlm()
├─ beforeToolCall()
├─ afterToolCall()
└─ runLoop()
```

最小流程：

```text
用户输入
  ↓
append user message
  ↓
transformContext
  ↓
convertToLlm
  ↓
call LLM
  ↓
stream assistant
  ↓
如果有 tool_call
  ↓
beforeToolCall
  ↓
execute tool
  ↓
afterToolCall
  ↓
append toolResult
  ↓
continue loop
```

第一阶段内置工具只需要：

```text
read_file
write_file
edit_file
run_command
finish
```

但工具必须有权限层。

---

## 5. 我们不应该直接照搬的地方

Pi 是 coding agent，所以它很多设计围绕代码任务、文件操作、bash、TUI、session compaction。我们可以学习结构，但不要直接照搬业务细节。

不建议直接照搬：

```text
Pi 的默认系统提示词
Pi 的工具命名
Pi 的 TUI 结构
Pi 的 session 文件格式
Pi 的 coding-agent 专用消息类型
```

建议吸收：

```text
Agent Runtime 和 Product Agent 分层
Agent Context 和 LLM Context 分离
transformContext / convertToLlm 双阶段上下文构建
beforeToolCall / afterToolCall 工具中间件
AgentEvent 事件流
Extension API
Session persistence
Steering / follow-up
prepareNextTurn
Dynamic system prompt builder
```

---

## 6. 对我们项目的具体启发

如果我们做的是 QQ 群聊机器人，那么 Pi 的思想可以这样迁移：

```text
pi-coding-agent        → qq-agent
read/bash/edit/write   → read_group_context/send_message/search_memory/call_api
AGENTS.md             → group_rules.md / persona.md
skills                → chat_skills
tool_call hook        → 发送消息前审核
context hook          → 群聊上下文筛选
follow-up             → 等当前话题结束后再补充
steering              → 用户中途纠正机器人
session manager       → 群聊长期记忆
```

如果我们做的是游戏 NPC Agent，则可以这样迁移：

```text
pi-coding-agent        → game-agent
tools                 → move/talk/attack/trade/observe
context files         → world_rules.md / npc_profile.md
extension             → 任务系统 / 情绪系统 / 战斗系统
tool middleware       → 行为合法性检查
event stream          → 驱动游戏 UI 和动画
memory compaction     → NPC 长期经历摘要
```

---

## 7. 最终结论

Pi Agent 最值得学习的不是某个 prompt，而是它的 Harness 工程方式：

```text
1. Agent 是运行时，不是 prompt。
2. 上下文要分 Agent Context 和 LLM Context。
3. 工具调用必须有中间件。
4. 所有过程都应该事件化。
5. 系统提示词应该动态构建。
6. 项目上下文应该一等公民化。
7. 扩展系统要能插入生命周期。
8. 会话要能持久化、压缩、恢复、重试。
9. 用户中途输入要支持 steering / follow-up。
10. 每一轮都要允许 prepareNextTurn 刷新状态。
```

一句话总结：

**我们要学 Pi 的不是“怎么写一个 coding agent”，而是“怎么把 Agent 做成一个可工程化运行的平台”。**
