from datetime import UTC, datetime

from openclaw.domain.tasks.execution import EventType, ExecutionEvent
from openclaw.infrastructure.channels.webchat.serialization import translate_event


def test_llm_decisions_become_safe_generic_thinking_events():
    event = ExecutionEvent(
        execution_id="execution-1",
        task_id="task-1",
        agent_id="github",
        type=EventType.LLM_DECISION,
        timestamp=datetime.now(UTC),
        data={
            "decision": "call_tool",
            "tool": "github.search_code",
            "input": {"query": "private repository contents"},
        },
    )

    translated = translate_event(event)

    assert translated == {
        "time": event.timestamp.strftime("%H:%M"),
        "type": "agent.thinking",
        "label": "Analyzing the next step",
        "status": "running",
    }
    assert "private repository contents" not in str(translated)
    assert "github.search_code" not in str(translated)
