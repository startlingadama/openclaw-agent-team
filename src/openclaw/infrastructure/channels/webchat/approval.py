from __future__ import annotations

import asyncio
import threading
import uuid
from typing import Any


class WebChatApprovalController:
    """Approval port adapter for the HTTP/WebChat channel.

    Every request gets its own id. An execution can ask several approvals one after the other
    (a script that calls approval-required tools, a retry of the same tool): they must not share
    the execution's id, or a client that follows requests by id takes the next one for the one it
    already answered, and the run waits for an answer nobody gives.
    """

    def __init__(self) -> None:
        self._pending: dict[str, dict[str, Any]] = {}
        self._execution_tasks: dict[str, str] = {}
        self._lock = threading.RLock()

    def register_execution(self, execution_id: str, task_id: str) -> None:
        with self._lock:
            self._execution_tasks[execution_id] = task_id

    def pending(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "id": approval_id,
                    "taskId": record["task_id"],
                    "executionId": record["execution_id"],
                    "agentId": record["agent_id"],
                    "action": record["action"],
                    "status": "pending",
                    "requestedAgo": "just now",
                }
                for approval_id, record in self._pending.items()
            ]

    async def request(self, approval: Any) -> Any:
        approval_id = uuid.uuid4().hex
        execution_id = getattr(approval, "execution_id", "approval")
        loop = asyncio.get_running_loop()
        record = {
            "approval": approval,
            "execution_id": execution_id,
            "task_id": self._execution_tasks.get(execution_id, "unknown"),
            "agent_id": getattr(approval, "agent_id", "agent"),
            "action": getattr(getattr(approval, "call", None), "name", "tool call"),
            "future": loop.create_future(),
            "loop": loop,
        }
        with self._lock:
            self._pending[approval_id] = record
        try:
            return await record["future"]
        finally:
            with self._lock:
                self._pending.pop(approval_id, None)

    def resolve(self, approval_id: str, approved: bool, reason: str = "") -> Any:
        with self._lock:
            record = self._pending.get(approval_id)
            if record is None:
                raise KeyError(approval_id)
            resolved = record["approval"].resolve(approved, reason)
            record["loop"].call_soon_threadsafe(record["future"].set_result, resolved)
            self._pending.pop(approval_id, None)
        return resolved
