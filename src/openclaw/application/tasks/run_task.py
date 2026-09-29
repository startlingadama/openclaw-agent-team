"""Application use case for executing a channel-submitted task."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from openclaw.domain.tasks.execution import Execution


@dataclass(frozen=True, slots=True)
class TaskRunResult:
    execution_id: str | None
    status: str
    answer: str | None = None
    error: str | None = None


class RunTask:
    """Invoke the agent use case and return a stable result for channel adapters."""

    async def execute(
        self,
        runner: Callable[..., Awaitable[Execution]],
        agent_id: str,
        instructions: str,
        *,
        task_id: str,
    ) -> TaskRunResult:
        try:
            execution = await runner(agent_id, instructions, task_id=task_id)
        except Exception as exc:
            return TaskRunResult(None, "failed", error=str(exc))

        status = str(execution.status)
        if status == "max_steps_exceeded":
            status = "failed"
        return TaskRunResult(execution.id, status, execution.answer, execution.error)
