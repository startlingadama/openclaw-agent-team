"""The documents a task produced, to be sent to the chat (ADR-030).

The WebChat finds them in the events of its task store; the Telegram channel has none, so this
object is an event sink of its own. It sees the tool calls of every run and keeps, for each task,
the paths that a `docs.write`, `docs.patch` or `docs.compile_pdf` call wrote without error. A
successful compilation counts for the `.tex` source and the PDF written next to it.

A delegated run is a task of its own (`DelegateTask` gives it a new task id), but the user asked
the task of the supervisor: the first event of a run names its parent execution, so the files of
a delegated run are attributed to the task at the root of the chain. The scripts of the sandbox
run inside the execution that started them and need nothing more.

What may be sent is decided by `DocumentFiles`, the check the WebChat download route uses: a
`.md`, `.tex` or `.pdf` file inside the documents directory, no dotfile, no path that leaves it.
The paths come from an LLM: nothing is read outside that check.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from openclaw.domain.tasks.execution import EventType, ExecutionEvent
from openclaw.infrastructure.tools.docs.files import DocumentFiles

WRITE_TOOLS = frozenset({"docs.write", "docs.patch"})
COMPILE_TOOL = "docs.compile_pdf"
MAX_TASKS = 200  # tasks whose files were never collected (a stopped bot) are dropped, oldest first
MAX_EXECUTIONS = 2000  # executions whose root task is remembered


@dataclass(frozen=True, slots=True)
class TaskDocument:
    path: Path
    name: str
    size: int  # bytes


@dataclass(slots=True)
class _TaskFiles:
    pending: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    produced: list[str] = field(default_factory=list)


class TaskDocuments:
    """An EventSink that remembers the documents of each task until `take` collects them."""

    def __init__(self, files: DocumentFiles) -> None:
        self._files = files
        self._tasks: OrderedDict[str, _TaskFiles] = OrderedDict()
        self._root_task: OrderedDict[str, str] = OrderedDict()  # execution id -> root task id

    async def record(self, event: ExecutionEvent) -> None:
        if event.type is EventType.TASK_STARTED:
            parent = event.data.get("parent_execution_id")
            root = self._root_task.get(parent, event.task_id) if isinstance(parent, str) else None
            self._root_task[event.execution_id] = root or event.task_id
            while len(self._root_task) > MAX_EXECUTIONS:
                self._root_task.popitem(last=False)
            return
        tool = event.data.get("tool")
        if tool not in WRITE_TOOLS and tool != COMPILE_TOOL:
            return
        task = self._root_task.get(event.execution_id, event.task_id)
        if event.type is EventType.TOOL_CALLED:
            state = self._state(task)
            key = (event.execution_id, str(tool))
            state.pending.setdefault(key, []).append(_input_path(event.data.get("input")))
        elif event.type is EventType.TOOL_RESULT:
            state = self._tasks.get(task)
            calls = None if state is None else state.pending.get((event.execution_id, str(tool)))
            if state is None or not calls:
                return
            path = calls.pop(0)
            if not path or event.data.get("status") == "error":
                return
            state.produced.append(path)
            if tool == COMPILE_TOOL:
                state.produced.append(str(PurePosixPath(path).with_suffix(".pdf")))

    def take(self, task_id: str) -> list[TaskDocument]:
        """The documents of the task that are still there and may be sent, oldest first.

        The task is forgotten: a second call returns nothing.
        """
        state = self._tasks.pop(task_id, None)
        if state is None:
            return []
        found: list[TaskDocument] = []
        for relative in dict.fromkeys(state.produced):
            entry = self._files.describe(relative)
            path = self._files.downloadable(relative)
            if entry is not None and path is not None:
                found.append(TaskDocument(path, entry.name, entry.size))
        return found

    def _state(self, task_id: str) -> _TaskFiles:
        state = self._tasks.get(task_id)
        if state is None:
            state = self._tasks[task_id] = _TaskFiles()
            while len(self._tasks) > MAX_TASKS:
                self._tasks.popitem(last=False)
        return state


def _input_path(value: Any) -> str:
    path = value.get("path") if isinstance(value, Mapping) else None
    return path if isinstance(path, str) else ""
