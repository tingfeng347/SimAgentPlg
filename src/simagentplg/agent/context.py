from collections.abc import Mapping, Sequence

from simagentplg.agent.types import AgentMessage


def transform_context(
    messages: Sequence[Mapping[str, object]],
) -> list[AgentMessage]:
    """Return the internal agent context for one model turn.

    The default transform is intentionally behavior-preserving. It exists as a
    stable hook for compaction, memory recall, or project-context injection.
    """

    return [dict(message) for message in messages]


def convert_to_llm_messages(
    messages: Sequence[Mapping[str, object]],
) -> list[AgentMessage]:
    """Convert internal agent messages into provider-compatible messages."""

    return [dict(message) for message in messages]
