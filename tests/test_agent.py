import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from simagentplg import (
    BaseAgent,
    BashApprovalMiddleware,
    BashHandler,
    FinishHandler,
    HumanApproval,
    Middleware,
    McpToolHandler,
    MethodToolHandler,
    ModelConfig,
    StepOutcome,
    ToolMiddleware,
    format_tool_call_preview,
)
from simagentplg.agent.base import (
    DEFAULT_SYSTEM_PROMPT,
    TOOL_COMPLETION_RETRY_PROMPT,
    TOOL_PROTOCOL_PROMPT,
)

TEST_CONFIG = ModelConfig(
    model="test-model",
    api_key="test-key",
    base_url="https://example.invalid",
)

ECHO_TOOL = {
    "type": "function",
    "function": {
        "name": "echo",
        "description": "Return the provided text.",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
}

DONE_TOOL = {
    "type": "function",
    "function": {
        "name": "done",
        "description": "Finish the current test task.",
        "parameters": {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        },
    },
}


@dataclass
class FakeFunction:
    name: str
    arguments: str


@dataclass
class FakeToolCall:
    id: str
    function: FakeFunction
    type: str = "function"


class FakeMessage:
    def __init__(
        self,
        content: str | None = None,
        tool_calls: list[FakeToolCall] | None = None,
    ) -> None:
        self.content = content
        self.tool_calls = tool_calls

    def model_dump(self) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": self.content,
            "tool_calls": self.tool_calls,
        }


class FakeCompletions:
    def __init__(self, responses: list[FakeMessage]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        message = self.responses.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeClient:
    def __init__(self, responses: list[FakeMessage]) -> None:
        self.completions = FakeCompletions(responses)
        self.chat = SimpleNamespace(completions=self.completions)


class EchoHandler(MethodToolHandler):
    def __init__(self) -> None:
        super().__init__((ECHO_TOOL,))
        self.started = 0
        self.stopped = 0
        self.task_starts = 0
        self.calls = 0

    async def startup(self) -> None:
        self.started += 1

    async def shutdown(self) -> None:
        self.stopped += 1

    async def on_task_start(self) -> None:
        self.task_starts += 1

    async def do_echo(self, arguments: dict[str, Any]) -> StepOutcome:
        self.calls += 1
        text = arguments.get("text")
        if not isinstance(text, str):
            return StepOutcome({"status": "error", "error": "text is required"})
        return StepOutcome({"status": "success", "text": text})


class DoneHandler(MethodToolHandler):
    def __init__(self) -> None:
        super().__init__((DONE_TOOL,))

    async def do_done(self, arguments: dict[str, Any]) -> StepOutcome:
        return StepOutcome(
            {"summary": arguments.get("summary", "")},
            should_exit=True,
        )


class FakeMcpManager:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def startup(self) -> None:
        self.started = True

    async def shutdown(self) -> None:
        self.stopped = True

    def get_openai_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "demo__lookup",
                    "description": "Lookup a value.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> str:
        self.calls.append((tool_name, arguments))
        return "mcp-result"


class RecordingToolMiddleware(ToolMiddleware):
    def __init__(
        self,
        outcome: StepOutcome | None = None,
        *,
        enabled: bool = True,
    ) -> None:
        super().__init__(enabled=enabled)
        self.outcome = outcome
        self.started = 0
        self.stopped = 0
        self.task_starts = 0
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def startup(self) -> None:
        self.started += 1

    async def shutdown(self) -> None:
        self.stopped += 1

    async def on_task_start(self) -> None:
        self.task_starts += 1

    async def before_tool_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> StepOutcome | None:
        self.calls.append((tool_name, dict(arguments)))
        return self.outcome


class ApprovalToolMiddleware(ToolMiddleware):
    def __init__(self, approval: HumanApproval, *, high_risk: bool) -> None:
        super().__init__()
        self.approval = approval
        self.high_risk = high_risk

    async def before_tool_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> StepOutcome | None:
        if not self.high_risk:
            return None
        approved = await self.approval.approve(
            format_tool_call_preview(tool_name, arguments)
        )
        if approved:
            return None
        return StepOutcome(
            {
                "status": "rejected",
                "tool": tool_name,
                "reason": "human rejected tool execution",
            },
            should_exit=True,
        )


class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_middleware_base_class_is_exported_with_standard_spelling(self) -> None:
        self.assertTrue(issubclass(ToolMiddleware, Middleware))

    async def test_model_config_reads_chat_model_from_env(self) -> None:
        with (
            patch("simagentplg.agent.base.load_dotenv"),
            patch.dict(
                "os.environ",
                {
                    "CHAT_MODEL": "chat-model",
                    "MODEL_API_KEY": "key",
                    "MODEL_URL": "https://model.example",
                    "LLM_TIMEOUT": "12",
                    "LLM_TEMPERATURE": "0.2",
                },
                clear=True,
            ),
        ):
            config = ModelConfig.from_env()

        self.assertEqual(config.model, "chat-model")
        self.assertEqual(config.api_key, "key")
        self.assertEqual(config.base_url, "https://model.example")
        self.assertEqual(config.timeout, 12)
        self.assertEqual(config.temperature, 0.2)

    async def test_model_config_accepts_legacy_base_model(self) -> None:
        with (
            patch("simagentplg.agent.base.load_dotenv"),
            patch.dict(
                "os.environ",
                {
                    "BASE_MODEL": "legacy-model",
                    "MODEL_API_KEY": "key",
                    "MODEL_URL": "https://model.example",
                },
                clear=True,
            ),
        ):
            config = ModelConfig.from_env()

        self.assertEqual(config.model, "legacy-model")

    async def test_agents_share_config_but_not_messages(self) -> None:
        first_client = FakeClient([FakeMessage("first")])
        second_client = FakeClient([FakeMessage("second")])
        first = BaseAgent(
            TEST_CONFIG,
            agent_id="first",
            client=first_client,
        )
        second = BaseAgent(
            TEST_CONFIG,
            agent_id="second",
            client=second_client,
        )

        await first.runtime(task="only first sees this")
        await second.runtime(task="only second sees this")

        self.assertIs(first.config, second.config)
        self.assertNotEqual(first.messages, second.messages)
        self.assertFalse(
            any(
                message.get("content") == "only first sees this"
                for message in second.messages
            )
        )

    async def test_runtime_keeps_memory_and_reset_clears_it(self) -> None:
        client = FakeClient([FakeMessage("one"), FakeMessage("two")])
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="memory",
            client=client,
        )

        await agent.runtime(task="first task")
        await agent.runtime(task="second task")

        second_call_messages = client.completions.calls[1]["messages"]
        self.assertTrue(
            any(message.get("content") == "one" for message in second_call_messages)
        )

        agent.reset([{"role": "user", "content": "seed"}])
        self.assertEqual(
            agent.messages,
            [
                {"role": "system", "content": agent.system_prompt},
                {"role": "user", "content": "seed"},
            ],
        )

    async def test_default_system_prompt_is_plain_chat_prompt(self) -> None:
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="default-prompt",
            client=FakeClient([]),
        )

        self.assertEqual(agent.system_prompt, DEFAULT_SYSTEM_PROMPT)
        self.assertEqual(
            agent.messages,
            [{"role": "system", "content": DEFAULT_SYSTEM_PROMPT}],
        )

    async def test_tool_mode_injects_tool_protocol_explicitly(self) -> None:
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="tool-protocol",
            system_prompt="You are a custom coding agent.",
            handlers=[DoneHandler()],
            client=FakeClient([]),
        )

        self.assertEqual(
            agent.messages,
            [
                {"role": "system", "content": "You are a custom coding agent."},
                {"role": "system", "content": TOOL_PROTOCOL_PROMPT},
            ],
        )

    async def test_plain_chat_has_no_tool_protocol_message(self) -> None:
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="plain-protocol",
            system_prompt="You are a plain chat agent.",
            client=FakeClient([]),
        )

        self.assertEqual(
            agent.messages,
            [
                {"role": "system", "content": "You are a plain chat agent."},
            ],
        )

    async def test_plain_chat_returns_text_without_retry_prompt(self) -> None:
        client = FakeClient([FakeMessage("plain text")])
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="plain-retry",
            max_steps=2,
            client=client,
        )

        result = await agent.runtime(task="plain chat")

        self.assertEqual(result, "plain text")
        self.assertFalse(
            any(
                message.get("content") == TOOL_COMPLETION_RETRY_PROMPT
                for message in agent.messages
            )
        )

    async def test_convert_to_llm_messages_can_filter_internal_context(self) -> None:
        class FilteringAgent(BaseAgent):
            def convert_to_llm_messages(
                self,
                messages: list[dict[str, Any]],
            ) -> list[dict[str, Any]]:
                return [
                    dict(message)
                    for message in messages
                    if not message.get("exclude_from_llm")
                ]

        client = FakeClient([FakeMessage("visible")])
        agent = FilteringAgent(
            TEST_CONFIG,
            agent_id="context",
            client=client,
        )
        agent.messages.append(
            {
                "role": "user",
                "content": "internal note",
                "exclude_from_llm": True,
            }
        )

        result = await agent.runtime(task="real task")

        self.assertEqual(result, "visible")
        sent_messages = client.completions.calls[0]["messages"]
        self.assertFalse(
            any(message.get("content") == "internal note" for message in sent_messages)
        )
        self.assertTrue(
            any(message.get("content") == "real task" for message in sent_messages)
        )

    async def test_skill_metadata_is_injected_without_llm_router(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            skills_dir = Path(temp_dir)
            skill_dir = skills_dir / "release_notes"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "\n".join(
                    [
                        "---",
                        "name: release_notes",
                        "description: Write user-facing release notes.",
                        "---",
                        "",
                        "# Release Notes",
                        "",
                        "This full instruction should load only on demand.",
                    ]
                ),
                encoding="utf-8",
            )
            client = FakeClient([FakeMessage("ok")])
            agent = BaseAgent(
                TEST_CONFIG,
                agent_id="skills-index",
                skills_dir=skills_dir,
                client=client,
            )

            with patch.dict("os.environ", {}, clear=True):
                result = await agent.runtime(task="Write release notes")

        self.assertEqual(result, "ok")
        sent_messages = client.completions.calls[0]["messages"]
        joined = "\n".join(str(message.get("content", "")) for message in sent_messages)
        self.assertIn("Available skills:", joined)
        self.assertIn("release_notes: Write user-facing release notes.", joined)
        self.assertNotIn("This full instruction should load only on demand.", joined)

    async def test_explicit_skill_name_loads_full_skill_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            skills_dir = Path(temp_dir)
            skill_dir = skills_dir / "release_notes"
            examples_dir = skill_dir / "examples"
            examples_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "\n".join(
                    [
                        "---",
                        "name: release_notes",
                        "description: Write user-facing release notes.",
                        "---",
                        "",
                        "# Release Notes",
                        "",
                        "FULL SKILL RULES",
                    ]
                ),
                encoding="utf-8",
            )
            (skill_dir / "template.md").write_text("TEMPLATE BODY", encoding="utf-8")
            (examples_dir / "sample.md").write_text("SAMPLE BODY", encoding="utf-8")
            client = FakeClient([FakeMessage("ok")])
            agent = BaseAgent(
                TEST_CONFIG,
                agent_id="skills-load",
                skills_dir=skills_dir,
                client=client,
            )

            await agent.runtime(task="$release_notes Write release notes")

        sent_messages = client.completions.calls[0]["messages"]
        joined = "\n".join(str(message.get("content", "")) for message in sent_messages)
        self.assertIn('You are executing the local skill "release_notes".', joined)
        self.assertIn("FULL SKILL RULES", joined)
        self.assertIn("TEMPLATE BODY", joined)
        self.assertIn("SAMPLE BODY", joined)

    async def test_model_can_load_skill_with_internal_tool(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            skills_dir = Path(temp_dir)
            skill_dir = skills_dir / "release_notes"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "\n".join(
                    [
                        "---",
                        "name: release_notes",
                        "description: Write user-facing release notes.",
                        "---",
                        "",
                        "# Release Notes",
                        "",
                        "FULL SKILL RULES",
                    ]
                ),
                encoding="utf-8",
            )
            client = FakeClient(
                [
                    FakeMessage(
                        tool_calls=[
                            FakeToolCall(
                                id="skill-call",
                                function=FakeFunction(
                                    "load_skill",
                                    '{"skill_name": "release_notes"}',
                                ),
                            )
                        ]
                    ),
                    FakeMessage("release notes"),
                ]
            )
            agent = BaseAgent(
                TEST_CONFIG,
                agent_id="skills-tool",
                skills_dir=skills_dir,
                client=client,
            )

            result = await agent.runtime(task="Write release notes")

        self.assertEqual(result, "release notes")
        first_tools = client.completions.calls[0]["tools"]
        self.assertEqual(
            [tool["function"]["name"] for tool in first_tools],
            ["load_skill"],
        )
        tool_message = next(
            message
            for message in agent.messages
            if message.get("role") == "tool"
        )
        self.assertEqual(
            json.loads(tool_message["content"])["skill_name"],
            "release_notes",
        )
        second_messages = client.completions.calls[1]["messages"]
        joined = "\n".join(
            str(message.get("content", "")) for message in second_messages
        )
        self.assertIn('You are executing the local skill "release_notes".', joined)
        self.assertIn("FULL SKILL RULES", joined)

    async def test_chat_json_requests_and_parses_json_object(self) -> None:
        client = FakeClient([FakeMessage('{"ok": true, "count": 2}')])
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="json",
            client=client,
        )

        payload = await agent.chat_json(
            [{"role": "user", "content": "return json"}],
        )

        self.assertEqual(payload, {"ok": True, "count": 2})
        self.assertEqual(
            client.completions.calls[0]["response_format"],
            {"type": "json_object"},
        )

    async def test_chat_json_rejects_invalid_json_content(self) -> None:
        client = FakeClient([FakeMessage("not json")])
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="bad-json",
            client=client,
        )

        with self.assertRaisesRegex(RuntimeError, "invalid JSON"):
            await agent.chat_json([{"role": "user", "content": "return json"}])

    async def test_agent_id_is_normalized_and_read_only(self) -> None:
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="  assistant  ",
            client=FakeClient([]),
        )

        self.assertEqual(agent.agent_id, "assistant")
        with self.assertRaises(AttributeError):
            agent.agent_id = "renamed"  # type: ignore[misc]

    async def test_empty_agent_id_is_rejected(self) -> None:
        for agent_id in ("", "   "):
            with self.subTest(agent_id=agent_id):
                with self.assertRaisesRegex(ValueError, "agent_id"):
                    BaseAgent(
                        TEST_CONFIG,
                        agent_id=agent_id,
                        client=FakeClient([]),
                    )

    async def test_agent_id_is_required(self) -> None:
        with self.assertRaisesRegex(TypeError, "agent_id"):
            BaseAgent(  # type: ignore[call-arg]
                TEST_CONFIG,
                client=FakeClient([]),
            )

    async def test_method_handler_dispatches_atomic_tool(self) -> None:
        handler = EchoHandler()
        result = await handler.dispatch("echo", {"text": "hello"})
        self.assertEqual(
            result.data,
            {"status": "success", "text": "hello"},
        )

    async def test_tool_mode_uses_only_explicit_handlers(self) -> None:
        echo = EchoHandler()
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="tools",
            handlers=[echo],
            client=FakeClient([]),
        )

        self.assertEqual(agent.handlers, [echo])
        self.assertEqual(
            [tool["function"]["name"] for tool in agent.tools],
            ["echo"],
        )

    async def test_explicit_bash_handler_is_preserved_without_finish_injection(self) -> None:
        bash = BashHandler()
        echo = EchoHandler()
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="tools",
            handlers=[bash, echo],
            client=FakeClient([]),
        )

        self.assertEqual(agent.handlers, [bash, echo])
        self.assertEqual(
            [tool["function"]["name"] for tool in agent.tools],
            ["bash_run", "echo"],
        )

    async def test_explicit_finish_handler_is_preserved(self) -> None:
        finish = FinishHandler()
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="tools",
            handlers=[finish],
            client=FakeClient([]),
        )

        self.assertEqual(agent.handlers, [finish])
        self.assertEqual(
            [tool["function"]["name"] for tool in agent.tools],
            ["run_finish"],
        )

    async def test_tool_disabled_mode_does_not_add_bash_handler(self) -> None:
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="chat",
            client=FakeClient([]),
        )

        self.assertEqual(agent.handlers, [])
        self.assertEqual(agent.tools, [])

    async def test_duplicate_tool_names_fail_during_startup(self) -> None:
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="duplicate-tools",
            handlers=[EchoHandler(), EchoHandler()],
            client=FakeClient([]),
        )

        with self.assertRaisesRegex(ValueError, "duplicate tool 'echo'"):
            await agent.startup()

    async def test_unknown_tool_has_explicit_error(self) -> None:
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="unknown-tool",
            handlers=[EchoHandler()],
            client=FakeClient([]),
        )

        with self.assertRaisesRegex(KeyError, "unknown tool 'missing'"):
            await agent.dispatch("missing", {})

    async def test_invalid_json_tool_arguments_are_returned_to_model(self) -> None:
        client = FakeClient(
            [
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(
                            id="call-1",
                            function=FakeFunction("echo", "[1, 2]"),
                        )
                    ]
                ),
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(
                            id="call-2",
                            function=FakeFunction(
                                "done",
                                '{"summary": "recovered"}',
                            ),
                        )
                    ]
                ),
            ]
        )
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="invalid-json",
            handlers=[EchoHandler(), DoneHandler()],
            client=client,
        )

        result = await agent.runtime(task="use echo")

        self.assertEqual(json.loads(result or "")["summary"], "recovered")
        tool_message = next(
            message for message in agent.messages if message["role"] == "tool"
        )
        payload = json.loads(tool_message["content"])
        self.assertEqual(payload["status"], "error")
        self.assertIn("JSON object", payload["error"])

    async def test_tool_mode_corrects_plain_text_until_exit_tool(self) -> None:
        client = FakeClient(
            [
                FakeMessage("premature answer"),
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(
                            id="call-1",
                            function=FakeFunction(
                                "done",
                                '{"summary": "done"}',
                            ),
                        )
                    ]
                ),
            ]
        )
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="finish-required",
            handlers=[DoneHandler()],
            client=client,
        )

        result = await agent.runtime(task="complete the task")

        self.assertEqual(json.loads(result or "")["summary"], "done")
        second_messages = client.completions.calls[1]["messages"]
        self.assertTrue(
            any(
                message.get("role") == "system"
                and message.get("content") == TOOL_COMPLETION_RETRY_PROMPT
                for message in second_messages
            )
        )

    async def test_tool_calls_are_logged(self) -> None:
        client = FakeClient(
            [
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(
                            id="call-1",
                            function=FakeFunction(
                                "echo",
                                '{"text": "hello"}',
                            ),
                        )
                    ]
                ),
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(
                            id="call-2",
                            function=FakeFunction(
                                "done",
                                '{"summary": "logged"}',
                            ),
                        )
                    ]
                ),
            ]
        )
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="log-tools",
            handlers=[EchoHandler(), DoneHandler()],
            client=client,
        )

        with self.assertLogs("log-tools", level="INFO") as captured:
            result = await agent.runtime(task="use echo then finish")

        self.assertEqual(json.loads(result or "")["summary"], "logged")
        logs = "\n".join(captured.output)
        self.assertIn("Calling tool echo", logs)
        self.assertIn('arguments={"text": "hello"}', logs)
        self.assertIn("Tool echo completed exit=False", logs)
        self.assertIn("Calling tool done", logs)
        self.assertIn("Tool done completed exit=True", logs)

    async def test_task_start_hook_runs_for_each_tool_task(self) -> None:
        handler = EchoHandler()
        client = FakeClient(
            [
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(
                            id="call-1",
                            function=FakeFunction(
                                "done",
                                '{"summary": "first"}',
                            ),
                        )
                    ]
                ),
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(
                            id="call-2",
                            function=FakeFunction(
                                "done",
                                '{"summary": "second"}',
                            ),
                        )
                    ]
                ),
            ]
        )
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="task-hooks",
            handlers=[handler, DoneHandler()],
            client=client,
        )

        await agent.runtime(task="first")
        await agent.runtime(task="second")

        self.assertEqual(handler.task_starts, 2)

    async def test_tool_middleware_lifecycle_and_low_risk_execution(self) -> None:
        handler = EchoHandler()
        middleware = RecordingToolMiddleware()
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="middleware-low-risk",
            handlers=[handler],
            middlewares=[middleware],
            client=FakeClient([]),
        )

        outcome = await agent.dispatch("echo", {"text": "allowed"})
        await agent.shutdown()

        self.assertEqual(outcome.data, {"status": "success", "text": "allowed"})
        self.assertEqual(handler.calls, 1)
        self.assertEqual(middleware.started, 1)
        self.assertEqual(middleware.stopped, 1)
        self.assertEqual(middleware.calls, [("echo", {"text": "allowed"})])

    async def test_tool_middleware_task_start_hook_runs_for_each_task(self) -> None:
        middleware = RecordingToolMiddleware()
        client = FakeClient(
            [
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(
                            id="call-1",
                            function=FakeFunction("done", '{"summary": "first"}'),
                        )
                    ]
                ),
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(
                            id="call-2",
                            function=FakeFunction("done", '{"summary": "second"}'),
                        )
                    ]
                ),
            ]
        )
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="middleware-task-hooks",
            handlers=[DoneHandler()],
            middlewares=[middleware],
            client=client,
        )

        await agent.runtime(task="first")
        await agent.runtime(task="second")

        self.assertEqual(middleware.task_starts, 2)

    async def test_middlewares_do_not_run_without_handlers(self) -> None:
        middleware = RecordingToolMiddleware()
        client = FakeClient([FakeMessage("plain chat")])
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="middleware-disabled",
            middlewares=[middleware],
            client=client,
        )

        result = await agent.runtime(task="hello")

        self.assertEqual(result, "plain chat")
        self.assertEqual(middleware.started, 0)
        self.assertEqual(middleware.task_starts, 0)
        self.assertEqual(middleware.calls, [])

    async def test_tool_middleware_rejection_ends_runtime(self) -> None:
        handler = EchoHandler()
        middleware = RecordingToolMiddleware(
            StepOutcome(
                {
                    "status": "rejected",
                    "tool": "echo",
                    "reason": "human rejected tool execution",
                },
                should_exit=True,
            )
        )
        client = FakeClient(
            [
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(
                            id="call-1",
                            function=FakeFunction("echo", '{"text": "blocked"}'),
                        )
                    ]
                )
            ]
        )
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="middleware-reject",
            handlers=[handler],
            middlewares=[middleware],
            client=client,
        )

        result = await agent.runtime(task="try echo")

        payload = json.loads(result or "")
        self.assertEqual(payload["status"], "rejected")
        self.assertEqual(payload["tool"], "echo")
        self.assertEqual(handler.calls, 0)

    async def test_middlewares_short_circuit_in_order(self) -> None:
        first = RecordingToolMiddleware(
            StepOutcome({"status": "blocked"}, should_exit=True)
        )
        second = RecordingToolMiddleware()
        handler = EchoHandler()
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="middleware-chain",
            handlers=[handler],
            middlewares=[first, second],
            client=FakeClient([]),
        )

        outcome = await agent.dispatch("echo", {"text": "blocked"})

        self.assertEqual(outcome.data, {"status": "blocked"})
        self.assertEqual(first.calls, [("echo", {"text": "blocked"})])
        self.assertEqual(second.calls, [])
        self.assertEqual(handler.calls, 0)

    async def test_human_approval_accepts_y_after_invalid_input(self) -> None:
        approval = HumanApproval(max_preview_chars=10)

        with (
            patch("builtins.input", side_effect=["maybe", "Y"]),
            patch("builtins.print") as print_mock,
        ):
            approved = await approval.approve("0123456789abcdef")

        self.assertTrue(approved)
        first_print = print_mock.call_args_list[0].args[0]
        self.assertIn("...<truncated 6 chars>", first_print)

    async def test_human_approval_middleware_can_reject_high_risk_tool(self) -> None:
        handler = EchoHandler()
        middleware = ApprovalToolMiddleware(
            HumanApproval(),
            high_risk=True,
        )
        client = FakeClient(
            [
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(
                            id="call-1",
                            function=FakeFunction("echo", '{"text": "blocked"}'),
                        )
                    ]
                )
            ]
        )
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="human-approval-reject",
            handlers=[handler],
            middlewares=[middleware],
            client=client,
        )

        with (
            patch("builtins.input", return_value="n"),
            patch("builtins.print"),
        ):
            result = await agent.runtime(task="try echo")

        payload = json.loads(result or "")
        self.assertEqual(payload["status"], "rejected")
        self.assertEqual(handler.calls, 0)

    async def test_human_approval_middleware_can_approve_high_risk_tool(self) -> None:
        handler = EchoHandler()
        middleware = ApprovalToolMiddleware(
            HumanApproval(),
            high_risk=True,
        )
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="human-approval-allow",
            handlers=[handler],
            middlewares=[middleware],
            client=FakeClient([]),
        )

        with (
            patch("builtins.input", return_value="y"),
            patch("builtins.print"),
        ):
            outcome = await agent.dispatch("echo", {"text": "allowed"})

        self.assertEqual(outcome.data, {"status": "success", "text": "allowed"})
        self.assertEqual(handler.calls, 1)

    async def test_bash_approval_middleware_reviews_unlisted_bash_run_by_default(self) -> None:
        middleware = BashApprovalMiddleware()
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="bash-approval-default",
            handlers=[BashHandler()],
            middlewares=[middleware],
            client=FakeClient([]),
        )

        with (
            patch("builtins.input", return_value="y") as input_mock,
            patch("builtins.print") as print_mock,
        ):
            outcome = await agent.dispatch("bash_run", {"code": "printf ok"})

        input_mock.assert_called_once()
        preview = print_mock.call_args_list[0].args[0]
        self.assertIn("Review:", preview)
        self.assertIn("safe command allowlist", preview)
        self.assertFalse(outcome.should_exit)
        self.assertEqual(outcome.data["status"], "success")
        self.assertEqual(outcome.data["stdout"], "ok")

    async def test_bash_approval_middleware_can_skip_review_explicitly(self) -> None:
        middleware = BashApprovalMiddleware(approval_policy="never")
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="bash-approval-never",
            handlers=[BashHandler()],
            middlewares=[middleware],
            client=FakeClient([]),
        )

        with patch("builtins.input") as input_mock:
            outcome = await agent.dispatch("bash_run", {"code": "printf ok"})

        input_mock.assert_not_called()
        self.assertFalse(outcome.should_exit)
        self.assertEqual(outcome.data["status"], "success")
        self.assertEqual(outcome.data["stdout"], "ok")

    async def test_bash_approval_safe_policy_skips_allowlisted_command(self) -> None:
        middleware = BashApprovalMiddleware(approval_policy="unless_safe")
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="bash-approval-safe",
            handlers=[BashHandler()],
            middlewares=[middleware],
            client=FakeClient([]),
        )

        with patch("builtins.input") as input_mock:
            outcome = await agent.dispatch(
                "bash_run",
                {"code": "git status --short"},
            )

        input_mock.assert_not_called()
        self.assertEqual(outcome.data["status"], "success")
        self.assertEqual(outcome.data["exit_code"], 0)

    async def test_bash_approval_safe_policy_reviews_unlisted_command(self) -> None:
        middleware = BashApprovalMiddleware(approval_policy="unless_safe")
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="bash-approval-unlisted",
            handlers=[BashHandler()],
            middlewares=[middleware],
            client=FakeClient([]),
        )

        with (
            patch("builtins.input", return_value="n") as input_mock,
            patch("builtins.print") as print_mock,
        ):
            outcome = await agent.dispatch("bash_run", {"code": "printf ok"})

        input_mock.assert_called_once()
        preview = print_mock.call_args_list[0].args[0]
        self.assertIn("safe command allowlist", preview)
        self.assertTrue(outcome.should_exit)
        self.assertEqual(outcome.data["status"], "rejected")

    async def test_bash_approval_safe_policy_reviews_shell_redirection(self) -> None:
        middleware = BashApprovalMiddleware(approval_policy="unless_safe")
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="bash-approval-dev-null",
            handlers=[BashHandler()],
            middlewares=[middleware],
            client=FakeClient([]),
        )

        with (
            patch("builtins.input", return_value="n") as input_mock,
            patch("builtins.print"),
        ):
            outcome = await agent.dispatch(
                "bash_run",
                {"code": "git status --short > /dev/null"},
            )

        input_mock.assert_called_once()
        self.assertTrue(outcome.should_exit)
        self.assertEqual(outcome.data["status"], "rejected")

    async def test_bash_approval_middleware_rejects_legacy_hint_policy_bash_run(self) -> None:
        middleware = BashApprovalMiddleware(approval_policy="on_review_hint")
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="bash-approval-reject",
            handlers=[BashHandler()],
            middlewares=[middleware],
            client=FakeClient([]),
        )

        with (
            patch("builtins.input", return_value="n") as input_mock,
            patch("builtins.print"),
        ):
            outcome = await agent.dispatch(
                "bash_run",
                {"code": "rm -rf build"},
            )

        input_mock.assert_called_once()
        self.assertTrue(outcome.should_exit)
        self.assertEqual(outcome.data["status"], "rejected")
        self.assertEqual(outcome.data["tool"], "bash_run")

    async def test_bash_approval_middleware_approves_unlisted_safe_policy_bash_run(self) -> None:
        middleware = BashApprovalMiddleware(approval_policy="unless_safe")
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="bash-approval-approve",
            handlers=[BashHandler()],
            middlewares=[middleware],
            client=FakeClient([]),
        )

        with (
            patch("builtins.input", return_value="y") as input_mock,
            patch("builtins.print"),
        ):
            outcome = await agent.dispatch(
                "bash_run",
                {"code": "rm -rf build"},
            )

        input_mock.assert_called_once()
        self.assertFalse(outcome.should_exit)
        self.assertEqual(outcome.data["status"], "success")
        self.assertEqual(outcome.data["exit_code"], 0)

    async def test_bash_approval_middleware_ignores_non_bash_tools(self) -> None:
        handler = EchoHandler()
        middleware = BashApprovalMiddleware()
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="bash-approval-ignore",
            handlers=[handler],
            middlewares=[middleware],
            client=FakeClient([]),
        )

        with patch("builtins.input") as input_mock:
            outcome = await agent.dispatch("echo", {"text": "allowed"})

        input_mock.assert_not_called()
        self.assertEqual(outcome.data, {"status": "success", "text": "allowed"})
        self.assertEqual(handler.calls, 1)

    async def test_third_identical_tool_call_fails_before_execution(self) -> None:
        handler = EchoHandler()
        repeated_call = FakeToolCall(
            id="call",
            function=FakeFunction("echo", '{"text": "same"}'),
        )
        client = FakeClient(
            [
                FakeMessage(tool_calls=[repeated_call]),
                FakeMessage(tool_calls=[repeated_call]),
                FakeMessage(tool_calls=[repeated_call]),
            ]
        )
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="repeat-guard",
            handlers=[handler],
            max_steps=3,
            client=client,
        )

        with self.assertRaisesRegex(RuntimeError, "consecutive times"):
            await agent.runtime(task="repeat")

        self.assertEqual(handler.calls, 2)

    async def test_tool_mode_raises_when_finish_is_never_called(self) -> None:
        client = FakeClient([FakeMessage("not finished"), FakeMessage(None)])
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="step-limit",
            handlers=[DoneHandler()],
            max_steps=2,
            client=client,
        )

        with self.assertRaisesRegex(RuntimeError, "did not finish within 2"):
            await agent.runtime(task="never finish")

    async def test_plain_chat_mode_does_not_start_handlers(self) -> None:
        client = FakeClient([FakeMessage("plain chat")])
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="plain-chat",
            client=client,
        )

        result = await agent.runtime(task="hello")

        self.assertEqual(result, "plain chat")
        self.assertIsNone(client.completions.calls[0]["tools"])

    async def test_bash_handler_rejects_invalid_arguments(self) -> None:
        handler = BashHandler()

        missing_code = await handler.dispatch("bash_run", {})
        invalid_timeout = await handler.dispatch(
            "bash_run",
            {"code": "printf ok", "timeout": 0},
        )

        self.assertEqual(missing_code.data["status"], "error")
        self.assertIn("non-empty string", missing_code.data["error"])
        self.assertIn("positive integer", invalid_timeout.data["error"])

    async def test_mcp_handler_uses_the_same_handler_contract(self) -> None:
        manager = FakeMcpManager()
        handler = McpToolHandler(manager=manager)

        await handler.startup()
        outcome = await handler.dispatch("demo__lookup", {"query": "value"})
        await handler.shutdown()

        self.assertEqual(outcome.data, "mcp-result")
        self.assertEqual(
            manager.calls,
            [("demo__lookup", {"query": "value"})],
        )
        self.assertTrue(manager.started)
        self.assertTrue(manager.stopped)


if __name__ == "__main__":
    unittest.main()
