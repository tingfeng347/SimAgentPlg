from __future__ import annotations

import asyncio
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

from simagentplg.agent.cancellation import (
    AgentCancelledError,
    CancellationToken,
)
from simagentplg.agent.context_management import (
    CompactionPolicy,
    CompactionPreparation,
    MessageTokenEstimator,
)
from simagentplg.agent.state import AgentState
from simagentplg.agent.types import INTERNAL_METADATA_PREFIX, AgentMessage

if TYPE_CHECKING:
    from simagentplg.agent.events import AgentEventEmitter


SUMMARY_METADATA_KEY = f"{INTERNAL_METADATA_PREFIX}summary"
SUMMARY_CONTEXT_HEADER = "Conversation summary from earlier turns:"


class CompactionStatus(StrEnum):
    """Terminal state of one explicit compaction operation."""

    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class CompactorOutput:
    """Provider-neutral summary text returned by a concrete Compactor."""

    content: str
    source: str

    def __post_init__(self) -> None:
        if not self.content.strip():
            raise ValueError("compactor output content must not be empty")
        if not self.source.strip():
            raise ValueError("compactor output source must not be empty")


@dataclass(frozen=True, slots=True)
class SummaryEntry:
    """Canonical metadata for one summary projected into model context."""

    content: str
    source: str
    history_start_index: int
    first_kept_index: int
    summarized_message_count: int
    tokens_before: int

    def __post_init__(self) -> None:
        if not self.content.strip():
            raise ValueError("summary content must not be empty")
        if not self.source.strip():
            raise ValueError("summary source must not be empty")
        if self.history_start_index < 0:
            raise ValueError("history_start_index must not be negative")
        if self.first_kept_index <= self.history_start_index:
            raise ValueError("first_kept_index must follow history_start_index")
        if self.summarized_message_count <= 0:
            raise ValueError("summarized_message_count must be greater than zero")
        if self.tokens_before < 0:
            raise ValueError("tokens_before must not be negative")

    def to_dict(self) -> dict[str, Any]:
        """Return a detached JSON-compatible metadata representation."""

        return {
            "content": self.content,
            "source": self.source,
            "history_start_index": self.history_start_index,
            "first_kept_index": self.first_kept_index,
            "summarized_message_count": self.summarized_message_count,
            "tokens_before": self.tokens_before,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> SummaryEntry:
        """Restore validated summary metadata from an internal message."""

        content = value["content"]
        source = value["source"]
        integer_fields = {
            name: value[name]
            for name in (
                "history_start_index",
                "first_kept_index",
                "summarized_message_count",
                "tokens_before",
            )
        }
        if not isinstance(content, str) or not isinstance(source, str):
            raise TypeError("summary content and source must be strings")
        if any(
            not isinstance(item, int) or isinstance(item, bool)
            for item in integer_fields.values()
        ):
            raise TypeError("summary range and token fields must be integers")
        return cls(
            content=content,
            source=source,
            history_start_index=integer_fields["history_start_index"],
            first_kept_index=integer_fields["first_kept_index"],
            summarized_message_count=integer_fields["summarized_message_count"],
            tokens_before=integer_fields["tokens_before"],
        )

    def to_agent_message(self) -> AgentMessage:
        """Project this entry as a system message with internal metadata."""

        return {
            "role": "system",
            "content": f"{SUMMARY_CONTEXT_HEADER}\n\n{self.content}",
            SUMMARY_METADATA_KEY: self.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class CompactionRequest:
    """Input supplied to a concrete summary generator."""

    preparation: CompactionPreparation
    previous_summary: SummaryEntry | None = None


class Compactor(Protocol):
    """Pluggable, cancellable behavior for generating one summary."""

    async def compact(
        self,
        request: CompactionRequest,
        *,
        cancellation: CancellationToken | None = None,
    ) -> CompactorOutput:
        """Summarize the prepared history without mutating Agent State."""


@dataclass(frozen=True, slots=True)
class CompactionResult:
    """Structured terminal result of one explicit compaction operation."""

    status: CompactionStatus
    preparation: CompactionPreparation
    summary: SummaryEntry | None = None
    messages: tuple[AgentMessage, ...] = field(default_factory=tuple)
    error: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "messages", deepcopy(self.messages))
        if self.status is CompactionStatus.COMPLETED:
            if self.summary is None or not self.messages or self.error is not None:
                raise ValueError(
                    "completed compaction requires summary and messages only"
                )
            if not self.preparation.can_compact:
                raise ValueError("completed compaction requires old turns")
        elif self.status is CompactionStatus.SKIPPED:
            if self.summary is not None or self.messages or self.error is not None:
                raise ValueError("skipped compaction must not contain output")
            if self.preparation.can_compact:
                raise ValueError("skipped compaction must not have old turns")
        else:
            if self.summary is not None or self.messages or not self.error:
                raise ValueError(
                    "failed or cancelled compaction requires only an error"
                )

    @property
    def completed(self) -> bool:
        return self.status is CompactionStatus.COMPLETED


def build_summary_entry(
    output: CompactorOutput,
    preparation: CompactionPreparation,
    *,
    previous_summary: SummaryEntry | None = None,
) -> SummaryEntry:
    """Combine trusted Core range metadata with concrete summary text."""

    previous_count = (
        previous_summary.summarized_message_count if previous_summary is not None else 0
    )
    return SummaryEntry(
        content=output.content.strip(),
        source=output.source.strip(),
        history_start_index=preparation.history_start_index,
        first_kept_index=preparation.first_kept_index,
        summarized_message_count=(
            previous_count + len(preparation.messages_to_summarize)
        ),
        tokens_before=preparation.estimated_history_tokens,
    )


def find_previous_summary(
    messages: tuple[AgentMessage, ...],
) -> SummaryEntry | None:
    """Return the latest valid internal Summary Entry, if present."""

    for message in reversed(messages):
        raw = message.get(SUMMARY_METADATA_KEY)
        if not isinstance(raw, Mapping):
            continue
        try:
            return SummaryEntry.from_dict(raw)
        except (KeyError, TypeError, ValueError):
            continue
    return None


def build_compacted_state_messages(
    preparation: CompactionPreparation,
    summary: SummaryEntry,
) -> list[AgentMessage]:
    """Build the atomic Agent State replacement without old Summary entries."""

    protected = [
        deepcopy(message)
        for message in preparation.protected_messages
        if not _is_valid_summary_message(message)
    ]
    return [
        *protected,
        summary.to_agent_message(),
        *(deepcopy(message) for message in preparation.messages_to_keep),
    ]


def build_compacted_session_messages(
    preparation: CompactionPreparation,
    summary: SummaryEntry,
) -> tuple[AgentMessage, ...]:
    """Build the compacted conversation projection stored by Session."""

    leading_system_end = 0
    while (
        leading_system_end < len(preparation.protected_messages)
        and preparation.protected_messages[leading_system_end].get("role") == "system"
    ):
        leading_system_end += 1
    preserved = (
        deepcopy(message)
        for message in preparation.protected_messages[leading_system_end:]
        if not _is_valid_summary_message(message)
    )
    return (
        *preserved,
        summary.to_agent_message(),
        *(deepcopy(message) for message in preparation.messages_to_keep),
    )


def _is_valid_summary_message(message: Mapping[str, Any]) -> bool:
    raw = message.get(SUMMARY_METADATA_KEY)
    if not isinstance(raw, Mapping):
        return False
    try:
        SummaryEntry.from_dict(raw)
    except (KeyError, TypeError, ValueError):
        return False
    return True


class CompactionRuntime:
    """Execute explicit compaction independently from the Agent Loop."""

    def __init__(
        self,
        *,
        state: AgentState,
        policy: CompactionPolicy | None,
        estimator: MessageTokenEstimator | None,
        event_emitter: AgentEventEmitter,
    ) -> None:
        self.state = state
        self.policy = policy
        self.estimator = estimator
        self.event_emitter = event_emitter

    async def compact(
        self,
        compactor: Compactor,
        *,
        cancellation: CancellationToken,
    ) -> CompactionResult:
        """Generate and atomically install one compacted history snapshot."""

        from simagentplg.agent.events import (
            CompactionCompleted,
            CompactionFailed,
            CompactionStarted,
        )

        if self.policy is None:
            raise RuntimeError("explicit compaction requires a CompactionPolicy")

        before = self.state.snapshot().messages
        preparation = self.policy.prepare(
            before,
            estimator=self.estimator,
        )
        previous_summary = find_previous_summary(preparation.protected_messages)
        request = CompactionRequest(preparation, previous_summary)
        operation_id = self.event_emitter.begin_run()
        try:
            await self.event_emitter.emit(CompactionStarted(request))
            if not preparation.can_compact:
                result = CompactionResult(
                    status=CompactionStatus.SKIPPED,
                    preparation=preparation,
                )
                await self.event_emitter.emit(CompactionCompleted(result))
                return result

            try:
                output = await cancellation.run(
                    compactor.compact(
                        request,
                        cancellation=cancellation,
                    )
                )
                if not isinstance(output, CompactorOutput):
                    raise TypeError("Compactor.compact() must return CompactorOutput")
                cancellation.raise_if_cancelled()
                if self.state.messages != before:
                    raise RuntimeError("agent history changed during compaction")

                summary = build_summary_entry(
                    output,
                    preparation,
                    previous_summary=previous_summary,
                )
                state_messages = build_compacted_state_messages(
                    preparation,
                    summary,
                )
                session_messages = build_compacted_session_messages(
                    preparation,
                    summary,
                )
                result = CompactionResult(
                    status=CompactionStatus.COMPLETED,
                    preparation=preparation,
                    summary=summary,
                    messages=session_messages,
                )
                completed_event = CompactionCompleted(result)
                self.state.replace_messages(state_messages)
                await self.event_emitter.emit(completed_event)
                return result
            except AgentCancelledError as exc:
                result = CompactionResult(
                    status=CompactionStatus.CANCELLED,
                    preparation=preparation,
                    error=str(exc),
                )
                await self.event_emitter.emit(CompactionFailed(result))
                return result
            except asyncio.CancelledError:
                result = CompactionResult(
                    status=CompactionStatus.CANCELLED,
                    preparation=preparation,
                    error="compaction coroutine was cancelled",
                )
                await self.event_emitter.emit(CompactionFailed(result))
                raise
            except Exception as exc:
                result = CompactionResult(
                    status=CompactionStatus.FAILED,
                    preparation=preparation,
                    error=str(exc),
                )
                await self.event_emitter.emit(CompactionFailed(result))
                return result
        finally:
            self.event_emitter.end_run(operation_id)
