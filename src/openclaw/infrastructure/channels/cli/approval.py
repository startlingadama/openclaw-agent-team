"""Human approval on the terminal (ADR-015). Without a terminal, nothing is ever approved."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable

from openclaw.domain.shared.errors import ApprovalError
from openclaw.domain.tasks.approval import Approval

_YES = frozenset({"y", "yes", "o", "oui"})


class CliApprovalPort:
    """Implements ApprovalPort. The runtime treats an ApprovalError as a rejection."""

    def __init__(
        self,
        ask: Callable[[str], str] = input,
        interactive: Callable[[], bool] = lambda: sys.stdin.isatty(),
    ) -> None:
        self._ask = ask
        self._interactive = interactive

    async def request(self, approval: Approval) -> Approval:
        if not self._interactive():
            raise ApprovalError("no interactive terminal to ask for approval")
        call = approval.call
        arguments = json.dumps(dict(call.arguments), ensure_ascii=False, default=str, indent=2)
        print(
            f"\nApproval needed from [{approval.agent_id}]: {call.name} "
            f"({approval.risk_level}; {approval.reason})\n"
            f"{arguments}",
            file=sys.stderr,
        )
        answer = await asyncio.to_thread(self._ask, "Approve? [y/N] ")
        return approval.resolve(answer.strip().lower() in _YES)
