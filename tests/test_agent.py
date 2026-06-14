import json
import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from simagentplg import (
    BaseAgent,
    BashHandler,
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

    async def startup(self) -> None:
        self.started += 1

    async def shutdown(self) -> None:
        self.stopped += 1

    async def do_echo(self, arguments: dict[str, Any]) -> StepOutcome:
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
            enable_tools=False,
            client=first_client,
        )
        second = BaseAgent(
            TEST_CONFIG,
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

    async def test_method_handler_dispatches_atomic_tool(self) -> None:
        handler = EchoHandler()
        result = await handler.dispatch("echo", {"text": "hello"})
        self.assertEqual(
            result.data,
            {"status": "success", "text": "hello"},
        )

    async def test_duplicate_tool_names_fail_during_startup(self) -> None:
        agent = BaseAgent(
            TEST_CONFIG,
            handlers=[EchoHandler(), EchoHandler()],
            client=FakeClient([]),
        )

        with self.assertRaisesRegex(ValueError, "duplicate tool 'echo'"):
            await agent.startup()

    async def test_unknown_tool_has_explicit_error(self) -> None:
        agent = BaseAgent(
            TEST_CONFIG,
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
                FakeMessage("recovered"),
            ]
        )
        agent = BaseAgent(
            TEST_CONFIG,
            handlers=[EchoHandler()],
            client=client,
        )

        result = await agent.runtime(task="use echo")

        self.assertEqual(result, "recovered")
        tool_message = next(
            message for message in agent.messages if message["role"] == "tool"
        )
        payload = json.loads(tool_message["content"])
        self.assertEqual(payload["status"], "error")
        self.assertIn("JSON object", payload["error"])

    async def test_tool_disabled_mode_does_not_start_handlers(self) -> None:
        handler = EchoHandler()
        client = FakeClient([FakeMessage("plain chat")])
        agent = BaseAgent(
            TEST_CONFIG,
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
