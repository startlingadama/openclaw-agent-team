"""CompositeEventSink: every event goes to every sink (console and trace at once)."""

from __future__ import annotations

import logging

from openclaw.domain.tasks.execution import ExecutionEvent
from openclaw.domain.tasks.ports import EventSink

log = logging.getLogger(__name__)


class CompositeEventSink:
    """Implements EventSink. A failing sink is logged and never stops the others or the run."""

    def __init__(self, *sinks: EventSink) -> None:
        self._sinks = sinks

    async def record(self, event: ExecutionEvent) -> None:
        for sink in self._sinks:
            try:
                await sink.record(event)
            except Exception:  # an observer must not break the agent run
                log.exception("event sink %s failed", type(sink).__name__)
