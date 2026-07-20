import asyncio
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from simagentplg import (
    SESSION_SCHEMA_VERSION,
    AgentRunResult,
    AgentSession,
    AssistantMessage,
    BaseAgent,
    CancellationToken,
    JsonlSessionStorage,
    ModelAdapter,
    RunStatus,
    RunUsage,
    SessionRecorder,
    SessionRecordKind,
    SessionSerializationError,
    SessionStorageError,
    StopReason,
    SummaryEntry,
    session_from_dict,
    session_to_dict,
)


class SequenceModel(ModelAdapter):
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.contexts: list[Any] = []

    async def complete(
        self,
        context: Any,
        *,
        cancellation: CancellationToken | None = None,
    ) -> AssistantMessage:
        self.contexts.append(context)
        return AssistantMessage(content=self.responses.pop(0))


def completed_result(output: str = "完成") -> AgentRunResult:
    return AgentRunResult(
        status=RunStatus.COMPLETED,
        stop_reason=StopReason.TEXT_RESPONSE,
        turns=2,
        output=output,
        usage=RunUsage(
            input_tokens=12,
            output_tokens=3,
            total_tokens=15,
            request_count=1,
            reported_request_count=1,
            cache_read_tokens=2,
            reasoning_tokens=1,
        ),
    )


def durable_session(session_id: str = "持久会话") -> AgentSession:
    session = AgentSession(session_id=session_id)
    session.bind_agent("durable-agent")
    session.begin_run("run-1", "记住 café", 1)
    session.append_message(
        "run-1",
        2,
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "lookup",
                        "arguments": '{"query":"北京"}',
                    },
                }
            ],
        },
    )
    session.append_message(
        "run-1",
        3,
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "content": '{"value":"晴天"}',
        },
    )
    session.append_message(
        "run-1",
        4,
        {
            "role": "assistant",
            "content": "完成",
            "usage": {
                "input_tokens": 12,
                "output_tokens": 3,
                "total_tokens": 15,
                "cache_read_tokens": 2,
                "cache_write_tokens": None,
                "reasoning_tokens": 1,
            },
        },
    )
    session.finish_run("run-1", 5, completed_result())
    summary = SummaryEntry(
        content="用户要求记住 café；北京查询结果为晴天。",
        source="summary-model:test",
        history_start_index=1,
        first_kept_index=3,
        summarized_message_count=2,
        tokens_before=15,
    )
    session.apply_compaction(
        "compact-1",
        6,
        summary,
        (
            summary.to_agent_message(),
            {"role": "assistant", "content": "完成"},
        ),
    )
    return session


def json_files(directory: str) -> list[Path]:
    return list(Path(directory).glob("*.jsonl"))


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def directory_entries(directory: str) -> list[Path]:
    return list(Path(directory).iterdir())


def append_bytes(path: Path, content: bytes) -> None:
    with path.open("ab") as stream:
        stream.write(content)


def read_json_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def write_json_lines(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )


class SessionPersistenceTests(unittest.IsolatedAsyncioTestCase):
    def test_versioned_codec_round_trips_complete_session(self) -> None:
        session = durable_session()

        payload = session_to_dict(session)
        restored = session_from_dict(payload)

        self.assertEqual(payload["schema_version"], SESSION_SCHEMA_VERSION)
        self.assertEqual(restored, session)
        self.assertEqual(restored.runs[0].result, completed_result())
        self.assertEqual(
            restored.compactions[0].summary.content,
            session.compactions[0].summary.content,
        )

    async def test_jsonl_storage_round_trips_between_instances(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            writer = JsonlSessionStorage(directory)
            reader = JsonlSessionStorage(directory)
            session = durable_session()

            await writer.save(session)
            restored = await reader.load(session.session_id)

            self.assertEqual(restored, session)
            files = await asyncio.to_thread(json_files, directory)
            self.assertEqual(len(files), 1)
            self.assertNotIn("持久会话", files[0].name)

    async def test_session_id_cannot_escape_storage_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "sessions"
            storage = JsonlSessionStorage(root)
            session = durable_session("../../outside")

            await storage.save(session)

            self.assertEqual(len(list(root.glob("*.jsonl"))), 1)
            self.assertFalse((Path(directory) / "outside.json").exists())

    async def test_corrupt_and_unknown_schema_are_explicit_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = JsonlSessionStorage(directory)
            session = durable_session("broken")
            await storage.save(session)
            path = (await asyncio.to_thread(json_files, directory))[0]

            await asyncio.to_thread(write_text, path, "{not-json\n")
            with self.assertRaises(SessionSerializationError):
                await storage.load("broken")

            await asyncio.to_thread(
                write_text,
                path,
                json.dumps({"journal_schema_version": 999}) + "\n",
            )
            with self.assertRaisesRegex(
                SessionSerializationError,
                "unsupported.*999",
            ):
                await storage.load("broken")

    async def test_incomplete_tail_is_ignored_and_repaired_on_append(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = JsonlSessionStorage(directory)
            original = durable_session("partial-tail")
            await storage.save(original)
            path = (await asyncio.to_thread(json_files, directory))[0]
            await asyncio.to_thread(append_bytes, path, b'{"partial":')

            restored = await storage.load("partial-tail")
            self.assertEqual(restored, original)

            changed = original.snapshot()
            changed.begin_run("run-2", "new task", 1)
            changed.append_message(
                "run-2",
                2,
                {"role": "assistant", "content": "new answer"},
            )
            changed.finish_run("run-2", 3, completed_result("new answer"))
            await storage.save(changed)

            self.assertEqual(await storage.load("partial-tail"), changed)
            self.assertEqual(len(await storage.records("partial-tail")), 2)

    async def test_broken_tree_parent_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = JsonlSessionStorage(directory)
            session = durable_session("broken-parent")
            await storage.save(session)
            await storage.save(session)
            path = (await asyncio.to_thread(json_files, directory))[0]
            records = await asyncio.to_thread(read_json_lines, path)
            records[1]["parent_id"] = "not-the-first-record"
            await asyncio.to_thread(write_json_lines, path, records)

            with self.assertRaisesRegex(
                SessionSerializationError,
                "parent changed",
            ):
                await storage.load("broken-parent")

    async def test_non_json_message_is_rejected_without_creating_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = JsonlSessionStorage(directory)
            session = AgentSession(session_id="invalid-json")
            session.bind_agent("durable-agent")
            session.begin_run("run-1", "task", 1)
            session.append_message(
                "run-1",
                2,
                {"role": "assistant", "content": {"not-json"}},
            )

            with self.assertRaises(SessionSerializationError):
                await storage.save(session)
            self.assertEqual(
                await asyncio.to_thread(directory_entries, directory),
                [],
            )

    async def test_failed_append_preserves_previous_journal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = JsonlSessionStorage(directory)
            original = durable_session("atomic")
            await storage.save(original)
            changed = original.snapshot()
            changed.begin_run("run-2", "new task", 1)
            changed.append_message(
                "run-2",
                2,
                {"role": "assistant", "content": "new answer"},
            )
            changed.finish_run("run-2", 3, completed_result("new answer"))

            with (
                patch(
                    "simagentplg.session.jsonl.os.write",
                    side_effect=OSError("disk failure"),
                ),
                self.assertRaises(SessionStorageError),
            ):
                await storage.save(changed)

            restored = await storage.load("atomic")
            self.assertEqual(restored, original)

    async def test_separate_python_process_loads_saved_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = JsonlSessionStorage(directory)
            await storage.save(durable_session("cross-process"))
            script = """
import asyncio
import json
import sys
from simagentplg import JsonlSessionStorage

async def main():
    session = await JsonlSessionStorage(sys.argv[1]).load(sys.argv[2])
    if session is None:
        raise RuntimeError("missing session")
    print(json.dumps({
        "agent_id": session.agent_id,
        "roles": [message["role"] for message in session.messages],
        "runs": len(session.runs),
    }, ensure_ascii=False))

asyncio.run(main())
"""

            process = await asyncio.to_thread(
                subprocess.run,
                [
                    sys.executable,
                    "-c",
                    script,
                    directory,
                    "cross-process",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            loaded = json.loads(process.stdout)

            self.assertEqual(loaded["agent_id"], "durable-agent")
            self.assertEqual(loaded["roles"], ["system", "assistant"])
            self.assertEqual(loaded["runs"], 1)

    async def test_restored_agent_continues_and_updates_durable_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = JsonlSessionStorage(directory)
            recorder = SessionRecorder(session_id="resume", storage=storage)
            first_agent = BaseAgent(
                SequenceModel(["saved answer"]),
                agent_id="durable-agent",
                event_sink=recorder,
            )
            await first_agent.run(task="saved task")

            saved = await JsonlSessionStorage(directory).load("resume")
            assert saved is not None
            resumed_model = SequenceModel(["continued answer"])
            resumed_agent = BaseAgent(
                resumed_model,
                agent_id="durable-agent",
                event_sink=SessionRecorder(
                    session_id="resume",
                    storage=JsonlSessionStorage(directory),
                ),
            )
            resumed_agent.restore_session(saved)

            result = await resumed_agent.run(task="continue")
            updated = await storage.load("resume")

            self.assertEqual(result.output, "continued answer")
            assert updated is not None
            self.assertEqual(len(updated.runs), 2)
            records = await storage.records("resume")
            self.assertEqual(
                [record.kind for record in records],
                [
                    SessionRecordKind.RUN_STARTED,
                    SessionRecordKind.MESSAGE_APPENDED,
                    SessionRecordKind.RUN_FINISHED,
                    SessionRecordKind.RUN_STARTED,
                    SessionRecordKind.MESSAGE_APPENDED,
                    SessionRecordKind.RUN_FINISHED,
                ],
            )
            self.assertEqual(
                [record.revision for record in records],
                list(range(1, 7)),
            )
            self.assertEqual(
                [record.parent_id for record in records[1:]],
                [record.record_id for record in records[:-1]],
            )
            self.assertTrue(all(record.branch_id == "main" for record in records))
            self.assertEqual(
                [
                    message.get("content")
                    for message in resumed_model.contexts[0].agent_messages
                ],
                [
                    resumed_agent.system_prompt,
                    "saved task",
                    "saved answer",
                    "continue",
                ],
            )

    def test_restore_rejects_wrong_agent_and_unfinished_run(self) -> None:
        wrong_agent = BaseAgent(
            SequenceModel(["unused"]),
            agent_id="other-agent",
        )
        with self.assertRaisesRegex(ValueError, "belongs to agent"):
            wrong_agent.restore_session(durable_session())

        unfinished = AgentSession(session_id="unfinished")
        unfinished.bind_agent("durable-agent")
        unfinished.begin_run("run-open", "task", 1)
        matching_agent = BaseAgent(
            SequenceModel(["unused"]),
            agent_id="durable-agent",
        )
        with self.assertRaisesRegex(ValueError, "unfinished"):
            matching_agent.restore_session(unfinished)


if __name__ == "__main__":
    unittest.main()
