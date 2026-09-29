"""Reading past executions (ARCHITECTURE section 19, ADR-024): what a trace lets one look up."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from openclaw.domain.tasks.execution import ExecutionEvent


@dataclass(frozen=True, slots=True)
class ExecutionSummary:
    """One execution as recorded. `status` is an ExecutionStatus value, or `running` for a run
    that has not ended (or was interrupted before it could say so)."""

    execution_id: str
    task_id: str
    agent_id: str
    status: str
    started_at: datetime
    sender: str | None = None
    parent_execution_id: str | None = None
    steps: int | None = None
    error: str | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    events: int = 0
    trace: str = ""  # where it is stored (the directory name)
    request: str | None = None  # what it was asked; filled by `summaries`, not by `find`


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    summary: ExecutionSummary
    request: str | None
    answer: str | None
    events: tuple[ExecutionEvent, ...]


class ExecutionHistory(Protocol):
    """Where past executions come from (`executions/` in infrastructure)."""

    async def summaries(
        self, limit: int | None = None, *, roots_only: bool = False
    ) -> list[ExecutionSummary]:
        """Newest first. `roots_only` leaves out the executions started by a delegation."""
        ...

    async def find(self, ref: str) -> ExecutionSummary:
        """The execution whose id, task id or trace name starts with `ref`. Raises HistoryError
        if there is none or if `ref` is ambiguous."""
        ...

    async def record(self, summary: ExecutionSummary) -> ExecutionRecord:
        """The summary with its request, answer and events. Raises HistoryError if unreadable."""
        ...
