"""FileTraceSink: one directory per execution under `executions/` (ARCHITECTURE section 19).

    executions/<start>_<agent>_<id>/
    |-- events.jsonl     every event, appended as it happens (survives a crash)
    |-- execution.json   summary: status, steps, timing, parent and delegated executions
    `-- result.md        the request and the final answer, for a human

`events.jsonl` is the source: the two other files are derived from the events alone. A
delegation is a run of its own, with its own directory; `parent_execution_id` in the child and
`children` in the parent link them. Recording never breaks a run: a write failure is logged once
per execution and the run goes on.

Tool inputs and outputs are kept as they were (long strings are cut, see `max_field_chars`), so a
trace may hold what the tools read, e-mails included: the directory is created private (0700) and
`OPENCLAW_TRACE=off` disables the trace.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from openclaw.domain.tasks.execution import EventType, ExecutionEvent

log = logging.getLogger(__name__)

EVENTS_FILE, EXECUTION_FILE, RESULT_FILE = "events.jsonl", "execution.json", "result.md"
DEFAULT_MAX_FIELD_CHARS = 20_000
_SAFE = re.compile(r"[^A-Za-z0-9_-]")


@dataclass(slots=True)
class _Run:
    """What is known of one execution while it is running."""

    execution_id: str
    task_id: str
    agent_id: str
    directory: Path
    started_at: datetime
    sender: str | None = None
    parent_execution_id: str | None = None
    request: str | None = None
    events: int = 0
    children: list[dict[str, Any]] = field(default_factory=list)
    write_failed: bool = False


class FileTraceSink:
    """Implements EventSink."""

    def __init__(self, root: Path | str, *, max_field_chars: int = DEFAULT_MAX_FIELD_CHARS) -> None:
        self._root = Path(root)
        self._max = max_field_chars
        self._runs: dict[str, _Run] = {}

    async def record(self, event: ExecutionEvent) -> None:
        run = self._run_for(event)
        try:
            await asyncio.to_thread(self._write, run, event)
        except OSError as exc:  # observing must never stop the agent
            if not run.write_failed:
                run.write_failed = True
                log.warning("execution trace not written to %s: %s", run.directory, exc)
        if event.type is EventType.FINAL_RESULT:
            self._runs.pop(event.execution_id, None)

    # -- bookkeeping ----------------------------------------------------------------------
    def _run_for(self, event: ExecutionEvent) -> _Run:
        run = self._runs.get(event.execution_id)
        if run is None:  # first event seen: normally task_started
            run = _Run(
                event.execution_id,
                event.task_id,
                event.agent_id,
                self._root / _directory_name(event),
                event.timestamp,
            )
            self._runs[event.execution_id] = run
        if event.type is EventType.TASK_STARTED:
            run.sender = _text(event.data.get("sender"))
            run.request = _text(event.data.get("request"))
            run.parent_execution_id = _text(event.data.get("parent_execution_id"))
            parent = self._runs.get(run.parent_execution_id or "")
            if parent is not None:
                parent.children.append(
                    {
                        "execution_id": run.execution_id,
                        "task_id": run.task_id,
                        "agent_id": run.agent_id,
                        "status": "running",
                        "trace": run.directory.name,
                    }
                )
        elif event.type is EventType.FINAL_RESULT:
            parent = self._runs.get(run.parent_execution_id or "")
            if parent is not None:
                for child in parent.children:
                    if child["execution_id"] == run.execution_id:
                        child["status"] = str(event.data.get("status"))
        run.events += 1
        return run

    # -- files (blocking: run in a thread) ------------------------------------------------
    def _write(self, run: _Run, event: ExecutionEvent) -> None:
        self._root.mkdir(mode=0o700, exist_ok=True)
        run.directory.mkdir(mode=0o700, exist_ok=True)
        line = {
            "ts": event.timestamp.isoformat(),
            "execution_id": event.execution_id,
            "task_id": event.task_id,
            "agent_id": event.agent_id,
            "type": event.type.value,
            "data": self._clean(event.data),
        }
        with (run.directory / EVENTS_FILE).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
        if event.type is EventType.TASK_STARTED:
            self._write_summary(run, {"status": "running"})
        elif event.type is EventType.FINAL_RESULT:
            final = {
                "status": str(event.data.get("status")),
                "steps": event.data.get("steps"),
                "error": event.data.get("error"),
                "finished_at": event.timestamp,
            }
            self._write_summary(run, final)
            self._write_result(run, final, _text(event.data.get("answer")))

    def _write_summary(self, run: _Run, outcome: Mapping[str, Any]) -> None:
        finished = outcome.get("finished_at")
        summary = {
            "execution_id": run.execution_id,
            "task_id": run.task_id,
            "agent_id": run.agent_id,
            "sender": run.sender,
            "parent_execution_id": run.parent_execution_id,
            "status": outcome["status"],
            "steps": outcome.get("steps"),
            "error": outcome.get("error"),
            "started_at": run.started_at.isoformat(),
            "finished_at": finished.isoformat() if finished else None,
            "duration_ms": round((finished - run.started_at).total_seconds() * 1000)
            if finished
            else None,
            "events": run.events,
            "children": run.children,
        }
        _replace(run.directory / EXECUTION_FILE, json.dumps(summary, ensure_ascii=False, indent=2))

    def _write_result(self, run: _Run, outcome: Mapping[str, Any], answer: str | None) -> None:
        lines = [
            f"# {run.agent_id}: {outcome['status']}",
            "",
            f"- execution: `{run.execution_id}`",
            f"- task: `{run.task_id}` (from `{run.sender or 'unknown'}`)",
        ]
        if run.parent_execution_id:
            lines.append(f"- part of execution: `{run.parent_execution_id}`")
        lines.append(f"- steps: {outcome.get('steps')}")
        if outcome.get("error"):
            lines.append(f"- error: {outcome['error']}")
        lines += ["", "## Request", "", run.request or "(unknown)", "", "## Answer", ""]
        lines.append(answer or "(no answer)")
        if run.children:
            lines += ["", "## Delegations", ""]
            lines += [
                f"- `{c['agent_id']}`: {c['status']} (`../{c['trace']}/result.md`)"
                for c in run.children
            ]
        _replace(run.directory / RESULT_FILE, "\n".join(lines) + "\n")

    # -- values ---------------------------------------------------------------------------
    def _clean(self, value: Any) -> Any:
        """JSON-safe copy of an event's data, with long strings cut."""
        if isinstance(value, str):
            if len(value) <= self._max:
                return value
            return f"{value[: self._max]}...[truncated, {len(value) - self._max} more characters]"
        if value is None or isinstance(value, bool | int | float):
            return value
        if isinstance(value, Mapping):
            return {str(k): self._clean(v) for k, v in value.items()}
        if isinstance(value, list | tuple | set | frozenset):
            return [self._clean(v) for v in value]
        return self._clean(str(value))


def _directory_name(event: ExecutionEvent) -> str:
    start = event.timestamp.strftime("%Y%m%dT%H%M%S")
    agent = _SAFE.sub("_", event.agent_id)
    return f"{start}_{agent}_{_SAFE.sub('_', event.execution_id)[:12]}"


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def _replace(path: Path, content: str) -> None:
    """Whole-file write that never leaves a half-written file behind."""
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)
