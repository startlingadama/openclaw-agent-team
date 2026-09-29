"""Execution entity and structured trace events (ARCHITECTURE section 19, ADR-019)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class ExecutionStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    MAX_STEPS_EXCEEDED = "max_steps_exceeded"


class EventType(StrEnum):
    TASK_STARTED = "task_started"
    AGENT_SELECTED = "agent_selected"
    SKILL_LOADED = "skill_loaded"
    LLM_DECISION = "llm_decision"
    POLICY_DENIED = "policy_denied"
    DUPLICATE_CALL = "duplicate_call"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"
    TOOL_CALLED = "tool_called"
    TOOL_RESULT = "tool_result"
    ERROR = "error"
    FINAL_RESULT = "final_result"


@dataclass(frozen=True, slots=True)
class ExecutionEvent:
    execution_id: str
    task_id: str
    agent_id: str
    type: EventType
    timestamp: datetime
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Execution:
    id: str
    task_id: str
    agent_id: str
    status: ExecutionStatus
    answer: str | None
    error: str | None
    steps: int
    events: tuple[ExecutionEvent, ...]
