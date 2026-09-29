"""Use case: run one agent on one task (what `openclaw run <agent> "<task>"` triggers)."""

from __future__ import annotations

import uuid

from openclaw.application.agents.runtime import AgentRuntime
from openclaw.domain.agents.ports import AgentRepository
from openclaw.domain.messages.model import AgentMessage
from openclaw.domain.shared.errors import AgentError
from openclaw.domain.tasks.execution import Execution
from openclaw.domain.tools.ports import ToolPort


class MissingToolsError(AgentError):
    """The agent is permitted tools that no provider serves (credentials not configured)."""

    def __init__(self, agent_id: str, missing: tuple[str, ...]) -> None:
        super().__init__(
            f"agent '{agent_id}' needs tools that are not available: {', '.join(missing)}"
        )
        self.missing = missing


class RunAgent:
    def __init__(
        self,
        *,
        agents: AgentRepository,
        runtime: AgentRuntime,
        tools: ToolPort,
        sender: str = "user",
    ) -> None:
        self._agents = agents
        self._runtime = runtime
        self._tools = tools
        self._sender = sender

    async def __call__(
        self,
        agent_id: str,
        task: str,
        *,
        sender: str | None = None,
        task_id: str | None = None,
    ) -> Execution:
        if not task.strip():
            raise AgentError("the task must not be empty")
        agent = await self._agents.get(agent_id)
        # Fail before the first LLM call: a permitted tool that no provider serves would
        # otherwise silently disappear from what the agent sees (ToolResolver filters it out).
        # A tool declared `optional` is the exception: the agent chose to run without it.
        permissions = agent.tool_permissions
        permitted = permissions.allowed | permissions.approval_required
        missing = tuple(
            sorted(
                n
                for n in permitted
                if self._tools.get_spec(n) is None and not permissions.is_optional(n)
            )
        )
        if missing:
            raise MissingToolsError(agent.id, missing)
        message = AgentMessage(
            sender=sender or self._sender,
            recipient=agent.id,
            task_id=task_id or uuid.uuid4().hex,
            content=task,
        )
        return await self._runtime.run(agent, message)
