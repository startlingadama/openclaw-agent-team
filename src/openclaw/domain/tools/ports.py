from __future__ import annotations

from typing import Any, Protocol

from openclaw.domain.agents.model import AgentId
from openclaw.domain.tools.model import ToolCall, ToolSpec


class ToolPort(Protocol):
    """Access to external tools. Implemented by tool adapters / the tool registry."""

    def get_spec(self, name: str) -> ToolSpec | None: ...

    async def execute(self, call: ToolCall, caller: AgentId) -> Any:
        """Run the tool on behalf of `caller` (set by the runtime, never by the LLM).

        Raises ToolError / AuthenticationError on failure.
        """
        ...


class ScriptToolPort(Protocol):
    """How a script running in the sandbox calls a tool (ADR-025).

    The call is made on behalf of `caller` with that agent's permissions and approvals, exactly
    like a direct call: it cannot widen them. Raises ToolError (denied, rejected, failed).
    """

    async def call(self, call: ToolCall, caller: AgentId) -> Any: ...
