from datetime import UTC, datetime

import pytest

from openclaw.domain.tasks.execution import EventType, ExecutionEvent
from openclaw.infrastructure.channels.webchat.events import WebChatEventSink


@pytest.mark.asyncio
async def test_webchat_logs_safe_execution_summary_and_forwards_event(caplog):
    forwarded = []

    async def handle(event):
        forwarded.append(event)

    event = ExecutionEvent(
        execution_id="exec-1",
        task_id="task-1",
        agent_id="github",
        type=EventType.TOOL_CALLED,
        timestamp=datetime.now(UTC),
        data={"tool": "github.search_code", "input": {"query": "private prompt"}},
    )
    sink = WebChatEventSink(handle)

    with caplog.at_level("INFO", logger="openclaw.infrastructure.channels.webchat.events"):
        await sink.record(event)

    assert "github" in caplog.text
    assert "tool_called" in caplog.text
    assert "github.search_code" in caplog.text
    assert "private prompt" not in caplog.text
    assert forwarded == [event]
