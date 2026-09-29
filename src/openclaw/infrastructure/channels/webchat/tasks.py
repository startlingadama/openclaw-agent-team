"""Task submission and background execution adapter for WebChat."""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time
from collections.abc import Mapping
from typing import Any

from openclaw.infrastructure.channels.webchat.serialization import execution_status
from openclaw.infrastructure.channels.webchat.task_store import TaskRecord, TaskStore


class WebChatTasks:
    def __init__(self, *, app: Any, tasks: TaskStore) -> None:
        self.app = app
        self.tasks = tasks
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._futures: set[concurrent.futures.Future[None]] = set()
        self._futures_lock = threading.Lock()
        self._closing = False
        self._thread.start()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _schedule(self, task: TaskRecord) -> concurrent.futures.Future[None]:
        if self._closing:
            raise RuntimeError("the web API is shutting down")
        future = asyncio.run_coroutine_threadsafe(self._execute(task), self._loop)
        with self._futures_lock:
            self._futures.add(future)
        future.add_done_callback(self._discard)
        return future

    def _discard(self, future: concurrent.futures.Future[None]) -> None:
        with self._futures_lock:
            self._futures.discard(future)

    async def create(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        agent_id = str(payload.get("agent_id") or payload.get("agentId") or "")
        title = str(payload.get("title") or payload.get("instructions") or "New task")
        instructions = str(payload.get("instructions") or "")
        if not agent_id:
            raise ValueError("agent_id is required")
        task = self.tasks.create(agent_id=agent_id, title=title, instructions=instructions)
        self._schedule(task)
        return task.as_dict()

    async def chat(self, *, agent_id: str, message: str) -> dict[str, Any]:
        task = self.tasks.create(agent_id=agent_id, title=message, instructions=message)
        self._schedule(task)
        return {"task_id": task.task_id, "message": message}

    async def list(self) -> list[dict[str, Any]]:
        return self.tasks.list()

    async def get(self, task_id: str) -> dict[str, Any]:
        task = self.tasks.get(task_id)
        if task is None:
            raise KeyError(task_id)
        return task.as_dict()

    async def events(self, task_id: str) -> list[dict[str, Any]]:
        task = self.tasks.get(task_id)
        if task is None:
            raise KeyError(task_id)
        return list(task.events)

    async def aclose(self) -> None:
        self._closing = True
        with self._futures_lock:
            pending = list(self._futures)
        if pending:
            wrapped = [asyncio.wrap_future(future) for future in pending]
            try:
                await asyncio.wait_for(
                    asyncio.gather(*wrapped, return_exceptions=True),
                    timeout=5,
                )
            except TimeoutError:
                for future in pending:
                    future.cancel()

        async def cancel_remaining() -> None:
            current = asyncio.current_task()
            tasks = [task for task in asyncio.all_tasks() if task is not current]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        cleanup = asyncio.run_coroutine_threadsafe(cancel_remaining(), self._loop)
        try:
            await asyncio.wait_for(asyncio.wrap_future(cleanup), timeout=5)
        except TimeoutError:
            cleanup.cancel()
        await self.app.aclose()
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)

    async def _execute(self, task: TaskRecord) -> None:
        self.tasks.set_status(task.task_id, "running")
        self.tasks.emit(
            task.task_id,
            {
                "time": time.strftime("%H:%M"),
                "type": "agent.started",
                "label": f"{task.agent_id} started",
                "status": "ok",
                "task_status": "running",
            },
        )
        result = await self.app.run_task.execute(
            self.app.run_agent,
            task.agent_id,
            task.instructions or task.title,
            task_id=task.task_id,
        )
        if result.execution_id is None:
            self.tasks.emit(
                task.task_id,
                {
                    "time": time.strftime("%H:%M"),
                    "type": "task.failed",
                    "label": "Task failed",
                    "status": "error",
                    "task_status": "failed",
                    "error": result.error,
                },
            )
            return
        self.tasks.set_execution(task.task_id, result.execution_id)
        self.tasks.set_status(task.task_id, execution_status(result.status))
