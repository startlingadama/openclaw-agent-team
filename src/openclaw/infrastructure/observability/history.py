"""FileExecutionHistory: reads what `FileTraceSink` wrote under `executions/` (ADR-024).

Directory names start with the UTC start time, so they sort by age without opening any file.
A directory that cannot be read (interrupted before `execution.json`, hand-edited) is skipped in
listings, with a warning, and reported when it is asked for by name.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from openclaw.domain.shared.errors import HistoryError
from openclaw.domain.tasks.execution import EventType, ExecutionEvent
from openclaw.domain.tasks.history import ExecutionRecord, ExecutionSummary
from openclaw.infrastructure.observability.trace import EVENTS_FILE, EXECUTION_FILE

log = logging.getLogger(__name__)


class FileExecutionHistory:
    """Implements the ExecutionHistory port. Nothing is cached: the files are the truth."""

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)

    async def summaries(
        self, limit: int | None = None, *, roots_only: bool = False
    ) -> list[ExecutionSummary]:
        return await asyncio.to_thread(self._summaries, limit, roots_only, True)

    async def find(self, ref: str) -> ExecutionSummary:
        ref = ref.strip()
        if not ref:
            raise HistoryError("an execution id is needed")
        found = [
            s
            for s in await asyncio.to_thread(self._summaries, None, False, False)
            if s.execution_id.startswith(ref)
            or s.task_id.startswith(ref)
            or s.trace.startswith(ref)
        ]
        if not found:
            raise HistoryError(f"no execution matches '{ref}'")
        if len(found) > 1:
            shown = ", ".join(f"{s.execution_id[:12]} ({s.agent_id})" for s in found[:5])
            more = f", and {len(found) - 5} more" if len(found) > 5 else ""
            raise HistoryError(f"'{ref}' matches {len(found)} executions: {shown}{more}")
        return found[0]

    async def record(self, summary: ExecutionSummary) -> ExecutionRecord:
        return await asyncio.to_thread(self._record, summary)

    # -- internals ------------------------------------------------------------------------
    def _directories(self) -> list[Path]:
        if not self._root.is_dir():
            return []
        return sorted((d for d in self._root.iterdir() if d.is_dir()), reverse=True)

    def _summaries(
        self, limit: int | None, roots_only: bool, with_request: bool
    ) -> list[ExecutionSummary]:
        found: list[ExecutionSummary] = []
        for directory in self._directories():
            if limit is not None and len(found) >= limit:
                break
            try:
                summary = _read_summary(directory)
            except HistoryError as exc:
                log.warning("execution trace skipped: %s", exc)
                continue
            if roots_only and summary.parent_execution_id:
                continue
            if with_request:
                summary = replace(summary, request=_first_request(directory))
            found.append(summary)
        return found

    def _record(self, summary: ExecutionSummary) -> ExecutionRecord:
        events = _read_events(self._root / summary.trace)
        request = next(
            (_str(e.data.get("request")) for e in events if _is(e, "task_started")), None
        )
        answer = next(
            (_str(e.data.get("answer")) for e in reversed(events) if _is(e, "final_result")), None
        )
        return ExecutionRecord(summary, request, answer, events)


def _read_summary(directory: Path) -> ExecutionSummary:
    path = directory / EXECUTION_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return ExecutionSummary(
            execution_id=str(data["execution_id"]),
            task_id=str(data["task_id"]),
            agent_id=str(data["agent_id"]),
            status=str(data["status"]),
            started_at=datetime.fromisoformat(data["started_at"]),
            sender=data.get("sender"),
            parent_execution_id=data.get("parent_execution_id"),
            steps=data.get("steps"),
            error=data.get("error"),
            finished_at=_when(data.get("finished_at")),
            duration_ms=data.get("duration_ms"),
            events=int(data.get("events") or 0),
            trace=directory.name,
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HistoryError(f"{directory.name}: {EXECUTION_FILE} cannot be read ({exc})") from exc


def _read_events(directory: Path) -> tuple[ExecutionEvent, ...]:
    path = directory / EVENTS_FILE
    try:
        raw = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise HistoryError(f"{directory.name}: {EVENTS_FILE} cannot be read ({exc})") from exc
    events = []
    for line in raw:
        try:
            data: dict[str, Any] = json.loads(line)
            events.append(
                ExecutionEvent(
                    data["execution_id"],
                    data["task_id"],
                    data["agent_id"],
                    EventType(data["type"]),
                    datetime.fromisoformat(data["ts"]),
                    data.get("data") or {},
                )
            )
        except (ValueError, KeyError, TypeError):
            continue  # a line cut by a crash, or an event type from another version
    return tuple(events)


def _first_request(directory: Path) -> str | None:
    """`task_started` is the first line of the trace: read that line only."""
    try:
        with (directory / EVENTS_FILE).open(encoding="utf-8") as stream:
            data = json.loads(stream.readline())
        return _str(data["data"].get("request")) if data["type"] == "task_started" else None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def _is(event: ExecutionEvent, type_: str) -> bool:
    return event.type.value == type_


def _when(value: Any) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def _str(value: Any) -> str | None:
    return None if value is None else str(value)
