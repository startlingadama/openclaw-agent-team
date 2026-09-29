from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from openclaw.domain.tasks.execution import ExecutionEvent

log = logging.getLogger(__name__)

_LOG_FIELDS = {
    "task_started": ("task_id",),
    "agent_selected": ("task_id",),
    "llm_decision": ("decision", "tool", "skill"),
    "skill_loaded": ("skill",),
    "tool_called": ("tool",),
    "tool_result": ("tool", "status", "duration_ms"),
    "approval_requested": ("tool",),
    "approval_resolved": ("tool", "approval"),
    "error": ("error_type",),
    "final_result": ("status", "steps"),
}


class WebChatEventSink:
    """Logs safe execution summaries and forwards events to the WebChat channel."""

    def __init__(self, handler: Callable[[ExecutionEvent], Awaitable[None]]) -> None:
        self._handler = handler

    async def record(self, event: ExecutionEvent) -> None:
        fields = _LOG_FIELDS.get(event.type.value)
        if fields is not None:
            details = " ".join(f"{key}={event.data[key]}" for key in fields if key in event.data)
            log.info(
                "[%s] %s task_id=%s %s",
                event.agent_id,
                event.type.value,
                event.task_id,
                details,
            )
        await self._handler(event)
