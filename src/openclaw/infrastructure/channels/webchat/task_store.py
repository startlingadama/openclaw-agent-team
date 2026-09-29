"""In-memory state and subscriber registry for active WebChat tasks."""

from __future__ import annotations

import queue
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from openclaw.infrastructure.channels.webchat.serialization import translate_event


@dataclass
class TaskRecord:
    task_id: str
    agent_id: str
    title: str
    status: str = "queued"
    started: str = field(default_factory=lambda: time.strftime("%H:%M"))
    createdAgo: str = "just now"
    instructions: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    execution_id: str | None = None
    subscribers: set[queue.Queue[dict[str, Any]]] = field(default_factory=set)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.task_id,
            "title": self.title,
            "agentId": self.agent_id,
            "status": self.status,
            "started": self.started,
            "duration": "0m",
            "createdAgo": self.createdAgo,
            "events": list(self.events),
        }


class TaskStore:
    """Adapter-local active task state; execution history remains the durable source."""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._lock = threading.RLock()

    def create(self, *, agent_id: str, title: str, instructions: str = "") -> TaskRecord:
        task = TaskRecord(
            task_id=uuid.uuid4().hex[:12],
            agent_id=agent_id,
            title=title,
            instructions=instructions,
        )
        with self._lock:
            self._tasks[task.task_id] = task
            self.emit(
                task.task_id,
                {
                    "time": time.strftime("%H:%M"),
                    "type": "task.created",
                    "label": "Task created",
                    "status": "ok",
                    "input": instructions or title,
                },
            )
        return task

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._tasks[task_id].as_dict() for task_id in reversed(list(self._tasks))]

    def get(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            return self._tasks.get(task_id)

    def count_for_agent(self, agent_id: str) -> int:
        with self._lock:
            return sum(task.agent_id == agent_id for task in self._tasks.values())

    def set_status(self, task_id: str, status: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is not None:
                task.status = status

    def set_execution(self, task_id: str, execution_id: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is not None:
                task.execution_id = execution_id

    def emit(self, task_id: str, event: Mapping[str, Any]) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            payload = dict(event)
            task.events.append(payload)
            if payload.get("task_status"):
                task.status = str(payload["task_status"])
            for subscriber in tuple(task.subscribers):
                subscriber.put(payload)

    async def record_event(self, event: Any) -> None:
        mapped = translate_event(event)
        if mapped is not None:
            self.emit(event.task_id, mapped)

    def subscribe(self, task_id: str) -> queue.Queue[dict[str, Any]]:
        with self._lock:
            task = self._tasks.setdefault(
                task_id,
                TaskRecord(task_id=task_id, agent_id="", title=""),
            )
            subscriber: queue.Queue[dict[str, Any]] = queue.Queue()
            task.subscribers.add(subscriber)
            for event in task.events:
                subscriber.put(dict(event))
            return subscriber

    def unsubscribe(self, task_id: str, subscriber: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is not None:
                task.subscribers.discard(subscriber)
