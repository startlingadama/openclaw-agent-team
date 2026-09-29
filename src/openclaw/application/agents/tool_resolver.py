from __future__ import annotations

from openclaw.domain.agents.model import Agent
from openclaw.domain.tools.model import ToolSpec
from openclaw.domain.tools.ports import ToolPort


class ToolResolver:
    """The LLM only ever sees the tools its agent is permitted to use (ADR-009)."""

    def __init__(self, tools: ToolPort) -> None:
        self._tools = tools

    def spec_for(self, name: str) -> ToolSpec | None:
        return self._tools.get_spec(name)

    def visible_specs(self, agent: Agent) -> tuple[ToolSpec, ...]:
        names = sorted(agent.tool_permissions.allowed | agent.tool_permissions.approval_required)
        specs = (self._tools.get_spec(name) for name in names)
        return tuple(s for s in specs if s is not None)
