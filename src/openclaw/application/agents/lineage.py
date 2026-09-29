"""Which execution a run was started from (the trace links a delegation to its parent).

The current execution id lives in a context variable: it follows the asynchronous call chain of
one run, so a specialist started by `team.delegate` sees the supervisor's execution as its
parent, and concurrent runs never see each other's. An LLM cannot set it: only the runtime does.

The task id of the running execution travels the same way, so that what a script does through
the tool channel (ADR-025) is recorded under the execution that started it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_current: ContextVar[str | None] = ContextVar("openclaw_current_execution", default=None)
_task: ContextVar[str | None] = ContextVar("openclaw_current_task", default=None)


def current_execution() -> str | None:
    """Id of the execution running in this call chain, if any."""
    return _current.get()


def current_task() -> str | None:
    """Task id of the execution running in this call chain, if any."""
    return _task.get()


@contextmanager
def execution_scope(execution_id: str, task_id: str | None = None) -> Iterator[None]:
    token = _current.set(execution_id)
    task_token = _task.set(task_id)
    try:
        yield
    finally:
        _task.reset(task_token)
        _current.reset(token)
