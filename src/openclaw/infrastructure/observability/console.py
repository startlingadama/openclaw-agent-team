"""ConsoleEventSink: one line per execution event on stderr (`openclaw run --verbose`)."""

from __future__ import annotations

import sys
from typing import TextIO

from openclaw.domain.tasks.execution import EventType, ExecutionEvent

_SHOWN = {
    EventType.LLM_DECISION: ("decision", "tool", "skill"),
    EventType.SKILL_LOADED: ("skill",),
    EventType.TOOL_CALLED: ("tool", "input"),
    EventType.TOOL_RESULT: ("tool", "status", "duration_ms", "error"),
    EventType.DUPLICATE_CALL: ("tool", "input"),
    EventType.POLICY_DENIED: ("tool", "reason"),
    EventType.APPROVAL_REQUESTED: ("tool",),
    EventType.APPROVAL_RESOLVED: ("tool", "approval"),
    EventType.ERROR: ("error_type", "error"),
}
_LIMIT = 200


class ConsoleEventSink:
    """Implements EventSink. Tool outputs and the final answer are not echoed here."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream

    async def record(self, event: ExecutionEvent) -> None:
        keys = _SHOWN.get(event.type)
        if keys is None:
            return
        details = " ".join(f"{k}={_clip(event.data[k])}" for k in keys if k in event.data)
        print(f"[{event.agent_id}] {event.type.value} {details}".rstrip(), file=self._out())

    def _out(self) -> TextIO:
        return self._stream or sys.stderr


def _clip(value: object) -> str:
    text = str(value)
    return text if len(text) <= _LIMIT else text[:_LIMIT] + "..."
