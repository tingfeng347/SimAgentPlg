from collections.abc import Sequence
from dataclasses import dataclass
from string import Formatter

from simagentplg.agent.manager import AgentManager

_RESERVED_FIELDS = frozenset({"input", "original_task"})


@dataclass(frozen=True, slots=True)
class WorkflowStep:
    """One named agent invocation in a linear workflow."""

    name: str
    agent_id: str
    prompt: str

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("workflow step name must not be empty")
        if self.name in _RESERVED_FIELDS:
            raise ValueError(
                f"workflow step name {self.name!r} is reserved"
            )
        if not self.agent_id:
            raise ValueError("workflow step agent_id must not be empty")


@dataclass(frozen=True, slots=True)
class WorkflowStepResult:
    """Input and output captured for one completed workflow step."""

    step_name: str
    agent_id: str
    task: str
    output: str


@dataclass(frozen=True, slots=True)
class WorkflowResult:
    """Complete execution record for a successful workflow."""

    original_task: str
    steps: tuple[WorkflowStepResult, ...]
    final_output: str


class WorkflowExecutionError(RuntimeError):
    """Raised when a workflow stops at a failed step."""

    def __init__(
        self,
        step: WorkflowStep,
        completed_steps: Sequence[WorkflowStepResult],
        cause: Exception,
    ) -> None:
        self.step = step
        self.completed_steps = tuple(completed_steps)
        self.cause = cause
        super().__init__(
            f"workflow step {step.name!r} failed for agent "
            f"{step.agent_id!r}: {cause}"
        )


class AgentWorkflow:
    """Execute registered agents as a validated linear pipeline."""

    def __init__(
        self,
        manager: AgentManager,
        steps: Sequence[WorkflowStep],
    ) -> None:
        if not steps:
            raise ValueError("workflow must contain at least one step")

        self.manager = manager
        self.steps = tuple(steps)
        self._validate_steps()

    async def run(self, task: str) -> WorkflowResult:
        completed: list[WorkflowStepResult] = []
        outputs: dict[str, str] = {}
        previous_output = task

        for step in self.steps:
            context = {
                "input": previous_output,
                "original_task": task,
                **outputs,
            }
            step_task = step.prompt.format_map(context)

            try:
                output = await self.manager.run_isolated(
                    step.agent_id,
                    step_task,
                )
                if output is None:
                    raise RuntimeError("agent returned no output")
            except Exception as exc:
                raise WorkflowExecutionError(
                    step,
                    completed,
                    exc,
                ) from exc

            result = WorkflowStepResult(
                step_name=step.name,
                agent_id=step.agent_id,
                task=step_task,
                output=output,
            )
            completed.append(result)
            outputs[step.name] = output
            previous_output = output

        return WorkflowResult(
            original_task=task,
            steps=tuple(completed),
            final_output=completed[-1].output,
        )

    def _validate_steps(self) -> None:
        known_fields = set(_RESERVED_FIELDS)
        step_names: set[str] = set()

        for step in self.steps:
            if step.name in step_names:
                raise ValueError(
                    f"duplicate workflow step name {step.name!r}"
                )
            self._validate_prompt(step, known_fields)
            step_names.add(step.name)
            known_fields.add(step.name)

    @staticmethod
    def _validate_prompt(
        step: WorkflowStep,
        known_fields: set[str],
    ) -> None:
        try:
            parsed = Formatter().parse(step.prompt)
            for _, field_name, format_spec, conversion in parsed:
                if field_name is None:
                    continue
                if not field_name:
                    raise ValueError("empty template field is not supported")
                if conversion or format_spec:
                    raise ValueError(
                        "template conversions and format specs are not supported"
                    )
                if "." in field_name or "[" in field_name:
                    raise ValueError(
                        "template attribute and index access are not supported"
                    )
                if field_name not in known_fields:
                    raise ValueError(
                        f"unknown or forward template field {field_name!r}"
                    )
        except ValueError as exc:
            raise ValueError(
                f"invalid prompt for workflow step {step.name!r}: {exc}"
            ) from exc
