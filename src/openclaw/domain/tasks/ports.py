from __future__ import annotations

from typing import Protocol

from openclaw.domain.tasks.approval import Approval
from openclaw.domain.tasks.execution import ExecutionEvent


class EventSink(Protocol):
    """Receives structured execution events (file/JSONL adapter comes with observability)."""

    async def record(self, event: ExecutionEvent) -> None: ...


class ApprovalPort(Protocol):
    """Asks a human to approve or reject a proposed action (REQUIREMENTS section 20)."""

    async def request(self, approval: Approval) -> Approval:
        """Return the approval resolved as APPROVED or REJECTED. May raise ApprovalError."""
        ...
