"""Documents of the writer agent, as the WebChat shows them (ADR-029).

Two views of the same directory: the files a task produced (shown under its answer in the chat)
and the history of every file in the directory (the Documents page). The files themselves are
found and checked by `DocumentFiles`, passed in from the composition root: this module only
translates its entries to the frontend DTO and reads task events.

What a task produced is read from its events, not guessed from file dates: a `docs.write`,
`docs.patch` or `docs.compile_pdf` call counts when its `tool.called` event is followed by a
`tool.completed` event without error. The events live in memory (`TaskStore`): after a restart
the files are still listed, without the task they came from.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

WRITE_TOOLS = frozenset({"docs.write", "docs.patch"})
COMPILE_TOOL = "docs.compile_pdf"


def produced_paths(events: Iterable[Mapping[str, Any]]) -> list[str]:
    """Relative paths the document tools of a task wrote without error, oldest first.

    A successful compilation counts for the `.tex` source and the PDF written next to it.
    Duplicates are dropped (the first position is kept).
    """
    pending: dict[str, list[str]] = {}
    found: list[str] = []
    for event in events:
        label = event.get("label")
        if label not in WRITE_TOOLS and label != COMPILE_TOOL:
            continue
        kind = event.get("type")
        if kind == "tool.called":
            pending.setdefault(str(label), []).append(_input_path(event.get("input")))
        elif kind == "tool.completed":
            calls = pending.get(str(label))
            path = calls.pop(0) if calls else ""
            if not path or event.get("status") == "error":
                continue
            found.append(path)
            if label == COMPILE_TOOL:
                found.append(str(PurePosixPath(path).with_suffix(".pdf")))
    return list(dict.fromkeys(found))


def _input_path(value: Any) -> str:
    path = value.get("path") if isinstance(value, Mapping) else None
    return path if isinstance(path, str) else ""


def entry_dto(entry: Any, task: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """A `DocumentEntry` as the frontend expects it; `task` is the task that produced it."""
    folder = str(PurePosixPath(entry.path).parent)
    return {
        "path": entry.path,
        "name": entry.name,
        "kind": entry.kind,
        "size": entry.size,
        "modified": datetime.fromtimestamp(entry.modified, UTC).isoformat(),
        "folder": "" if folder == "." else folder,
        "taskId": None if task is None else task.get("id"),
        "agentId": None if task is None else task.get("agentId"),
        "taskTitle": None if task is None else task.get("title"),
    }


def task_documents(files: Any, events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The documents a task produced that are still there and may be downloaded."""
    found = []
    for relative in produced_paths(events):
        entry = files.describe(relative)
        if entry is not None:
            found.append(entry_dto(entry))
    return found


def history(files: Any, tasks: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every document of the directory, newest first, with its task when it is still known.

    `tasks` come newest first: a document several tasks wrote belongs to the latest of them.
    """
    origins: dict[str, Mapping[str, Any]] = {}
    for task in tasks:
        for relative in produced_paths(task.get("events", ())):
            origins.setdefault(relative, task)
    return [entry_dto(entry, origins.get(entry.path)) for entry in files.listing()]
