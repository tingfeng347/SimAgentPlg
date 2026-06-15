import json
import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from simagentplg import (
    BaseAgent,
    BashHandler,
    FinishHandler,
    McpToolHandler,
    MethodToolHandler,
    ModelConfig,
    StepOutcome,
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


class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_agents_share_config_but_not_messages(self) -> None:
        first_client = FakeClient([FakeMessage("first")])
        second_client = FakeClient([FakeMessage("second")])
        first = BaseAgent(
            TEST_CONFIG,
            agent_id="first",
            enable_tools=False,
            client=first_client,
        )
        second = BaseAgent(
            TEST_CONFIG,
            agent_id="second",
            enable_tools=False,
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
            enable_tools=False,
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

    async def test_agent_id_is_normalized_and_read_only(self) -> None:
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="  assistant  ",
            enable_tools=False,
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
                        enable_tools=False,
                        client=FakeClient([]),
                    )

    async def test_agent_id_is_required(self) -> None:
        with self.assertRaisesRegex(TypeError, "agent_id"):
            BaseAgent(  # type: ignore[call-arg]
                TEST_CONFIG,
                enable_tools=False,
                client=FakeClient([]),
            )

    async def test_method_handler_dispatches_atomic_tool(self) -> None:
        handler = EchoHandler()
        result = await handler.dispatch("echo", {"text": "hello"})
        self.assertEqual(
            result.data,
            {"status": "success", "text": "hello"},
        )

    async def test_tool_mode_always_includes_builtin_handlers(self) -> None:
        echo = EchoHandler()
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="tools",
            handlers=[echo],
            enable_tools=True,
            client=FakeClient([]),
        )

        self.assertIsInstance(agent.handlers[0], BashHandler)
        self.assertIsInstance(agent.handlers[1], FinishHandler)
        self.assertIs(agent.handlers[2], echo)
        self.assertEqual(
            [tool["function"]["name"] for tool in agent.tools],
            ["bash_run", "run_finish", "echo"],
        )

    async def test_explicit_bash_handler_is_not_duplicated(self) -> None:
        bash = BashHandler()
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="tools",
            handlers=[bash, EchoHandler()],
            enable_tools=True,
            client=FakeClient([]),
        )

        self.assertEqual(
            sum(isinstance(handler, BashHandler) for handler in agent.handlers),
            1,
        )
        self.assertIs(agent.handlers[0], bash)
        self.assertIsInstance(agent.handlers[1], FinishHandler)
        self.assertEqual(agent.handlers[1].cwd, bash.cwd)

    async def test_explicit_finish_handler_is_not_duplicated(self) -> None:
        finish = FinishHandler()
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="tools",
            handlers=[finish],
            enable_tools=True,
            client=FakeClient([]),
        )

        self.assertEqual(
            sum(
                isinstance(handler, FinishHandler)
                for handler in agent.handlers
            ),
            1,
        )
        self.assertIs(agent.handlers[1], finish)

    async def test_tool_disabled_mode_does_not_add_bash_handler(self) -> None:
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="chat",
            enable_tools=False,
            client=FakeClient([]),
        )

        self.assertEqual(agent.handlers, [])
        self.assertEqual(agent.tools, [])

    async def test_duplicate_tool_names_fail_during_startup(self) -> None:
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="duplicate-tools",
            handlers=[EchoHandler(), EchoHandler()],
            enable_tools=True,
            client=FakeClient([]),
        )

        with self.assertRaisesRegex(ValueError, "duplicate tool 'echo'"):
            await agent.startup()

    async def test_unknown_tool_has_explicit_error(self) -> None:
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="unknown-tool",
            handlers=[EchoHandler()],
            enable_tools=True,
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
                                "run_finish",
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
            handlers=[EchoHandler()],
            enable_tools=True,
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

    async def test_tool_mode_corrects_plain_text_until_finish(self) -> None:
        client = FakeClient(
            [
                FakeMessage("premature answer"),
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(
                            id="call-1",
                            function=FakeFunction(
                                "run_finish",
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
            enable_tools=True,
            client=client,
        )

        result = await agent.runtime(task="complete the task")

        self.assertEqual(json.loads(result or "")["summary"], "done")
        second_messages = client.completions.calls[1]["messages"]
        self.assertTrue(
            any(
                message.get("role") == "system"
                and "run_finish" in message.get("content", "")
                for message in second_messages
            )
        )

    async def test_task_start_hook_runs_for_each_tool_task(self) -> None:
        handler = EchoHandler()
        client = FakeClient(
            [
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(
                            id="call-1",
                            function=FakeFunction(
                                "run_finish",
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
                                "run_finish",
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
            handlers=[handler],
            enable_tools=True,
            client=client,
        )

        await agent.runtime(task="first")
        await agent.runtime(task="second")

        self.assertEqual(handler.task_starts, 2)

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
            enable_tools=True,
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
            enable_tools=True,
            max_steps=2,
            client=client,
        )

        with self.assertRaisesRegex(RuntimeError, "did not finish within 2"):
            await agent.runtime(task="never finish")

    async def test_tool_disabled_mode_does_not_start_handlers(self) -> None:
        handler = EchoHandler()
        client = FakeClient([FakeMessage("plain chat")])
        agent = BaseAgent(
            TEST_CONFIG,
            agent_id="plain-chat",
            handlers=[handler],
            enable_tools=False,
            client=client,
        )

        result = await agent.runtime(task="hello")

        self.assertEqual(result, "plain chat")
        self.assertEqual(handler.started, 0)
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
