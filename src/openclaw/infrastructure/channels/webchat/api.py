"""Compose WebChat resource queries and task operations behind one API facade."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openclaw.infrastructure.channels.webchat import documents as document_views
from openclaw.infrastructure.channels.webchat.resources import WebChatResources
from openclaw.infrastructure.channels.webchat.task_store import TaskStore
from openclaw.infrastructure.channels.webchat.tasks import WebChatTasks

_TASK_METHODS = {
    "create_task": "create",
    "list_tasks": "list",
    "get_task": "get",
    "get_task_events": "events",
}


class WebChatAPI:
    """Small facade consumed by the HTTP transport."""

    def __init__(
        self, *, app: Any, tasks: TaskStore, approvals: Any, documents: Any = None
    ) -> None:
        self.app = app
        self._documents = documents  # locates the files a client may download (ADR-026)
        self.tasks = tasks
        self._resources = WebChatResources(app=app, tasks=tasks, approvals=approvals)
        self._task_operations = WebChatTasks(app=app, tasks=tasks)

    async def aclose(self) -> None:
        await self._task_operations.aclose()

    def find_document(self, relative: str) -> Path | None:
        """The Markdown, LaTeX or PDF file a client may download, or None."""
        return None if self._documents is None else self._documents.downloadable(relative)

    async def list_documents(self) -> list[dict[str, Any]]:
        """The history of the documents directory, newest first (ADR-029)."""
        if self._documents is None:
            return []
        return document_views.history(self._documents, self.tasks.list())

    async def get_task(self, task_id: str) -> dict[str, Any]:
        """The task, with the documents it produced that can still be downloaded."""
        task = await self._task_operations.get(task_id)
        if self._documents is not None:
            task["documents"] = document_views.task_documents(self._documents, task["events"])
        return task

    def __getattr__(self, name: str) -> Any:
        if hasattr(self._resources, name):
            return getattr(self._resources, name)
        operation_name = _TASK_METHODS.get(name, name)
        if hasattr(self._task_operations, operation_name):
            return getattr(self._task_operations, operation_name)
        raise AttributeError(f"{type(self).__name__!s} has no attribute {name!r}")
