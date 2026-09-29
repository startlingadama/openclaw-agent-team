"""Translate domain events and history records to the frontend DTO contract."""

from __future__ import annotations

import time
from typing import Any


def translate_event(event: Any) -> dict[str, Any] | None:
    data = dict(event.data)
    kind = event.type.value
    timestamp = _stamp(event.timestamp)
    if kind == "task_started":
        return {
            "time": timestamp,
            "type": "task.created",
            "label": "Task created",
            "status": "ok",
            "input": data.get("request"),
        }
    if kind == "agent_selected":
        return {
            "time": timestamp,
            "type": "agent.started",
            "label": "Agent selected",
            "status": "ok",
        }
    if kind == "llm_decision":
        return {
            "time": timestamp,
            "type": "agent.thinking",
            "label": "Analyzing the next step",
            "status": "running",
        }
    if kind == "skill_loaded":
        return {
            "time": timestamp,
            "type": "skill.loaded",
            "label": f"{data.get('skill', 'skill')} loaded",
            "status": "ok",
            "duration": "120ms",
        }
    if kind == "tool_called":
        return {
            "time": timestamp,
            "type": "tool.called",
            "label": str(data.get("tool") or "tool call"),
            "status": "running",
            "input": data.get("input"),
        }
    if kind == "tool_result":
        failed = data.get("status") == "error"
        return {
            "time": timestamp,
            "type": "tool.completed",
            "label": str(data.get("tool") or "tool result"),
            "status": "error" if failed else "ok",
            "output": data.get("output"),
            "error": data.get("error"),
        }
    if kind == "approval_requested":
        return {
            "time": timestamp,
            "type": "approval.required",
            "label": f"Approval required: {data.get('tool', 'tool')}",
            "status": "pending",
            "task_status": "waiting_approval",
        }
    if kind == "approval_resolved":
        rejected = data.get("approval") == "rejected"
        return {
            "time": timestamp,
            "type": "approval.resolved",
            "label": f"Approval {data.get('approval', 'resolved')}",
            "status": "error" if rejected else "ok",
            "task_status": "running",
        }
    if kind == "final_result":
        completed = data.get("status") == "completed"
        return {
            "time": timestamp,
            "type": "task.completed" if completed else "task.failed",
            "label": "Task completed" if completed else "Task failed",
            "status": "ok" if completed else "error",
            "task_status": execution_status(data.get("status")),
            "output": data.get("answer"),
            "error": data.get("error"),
        }
    if kind == "error":
        return {
            "time": timestamp,
            "type": "task.failed",
            "label": "Task failed",
            "status": "error",
            "error": data.get("error"),
            "task_status": "failed",
        }
    return {"time": timestamp, "type": kind.replace("_", "."), "label": kind, "status": "ok"}


def execution_status(status: Any) -> str:
    if status is None:
        return "running"
    if isinstance(status, str):
        return {
            "completed": "completed",
            "failed": "failed",
            "max_steps_exceeded": "failed",
        }.get(status, status)
    return "completed" if str(status) == "completed" else "failed"


def execution_summary(summary: Any, events: Any = ()) -> dict[str, Any]:
    return {
        "id": summary.execution_id,
        "taskId": summary.task_id,
        "taskTitle": summary.request or summary.task_id,
        "agentId": summary.agent_id,
        "status": execution_status(summary.status),
        "startedAt": _stamp(summary.started_at),
        "duration": f"{summary.duration_ms or 0}ms",
        "steps": summary.steps or 0,
        "toolCalls": sum(event.type.value == "tool_called" for event in events),
        "approvals": sum(event.type.value == "approval_requested" for event in events),
    }


def _stamp(moment: Any) -> str:
    try:
        return moment.strftime("%H:%M")
    except Exception:
        return time.strftime("%H:%M")
