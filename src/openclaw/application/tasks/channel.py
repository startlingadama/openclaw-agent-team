"""What a conversational channel (Telegram) asks of the application: run a task with the team
or with one agent, and list the agents. Errors come back as a `TaskRunResult`, never raised."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence

from openclaw.application.agents.run_agent import RunAgent
from openclaw.application.tasks.run_task import RunTask, TaskRunResult
from openclaw.application.teams.run_team import RunTeam
from openclaw.domain.agents.ports import AgentRepository
from openclaw.domain.shared.errors import OpenClawError
from openclaw.domain.tasks.execution import Execution


class ChannelBackend:
    def __init__(
        self,
        *,
        run_task: RunTask,
        run_agent: RunAgent,
        run_team: RunTeam,
        agents: AgentRepository,
        team_id: str,
    ) -> None:
        self._run_task = run_task
        self._run_agent = run_agent
        self._run_team = run_team
        self._agents = agents
        self._team_id = team_id

    async def run_team(self, text: str, task_id: str) -> TaskRunResult:
        async def runner(_agent_id: str, instructions: str, *, task_id: str) -> Execution:
            return await self._run_team(self._team_id, instructions, task_id=task_id)

        return await self._run_task.execute(runner, self._team_id, text, task_id=task_id)

    async def run_agent(self, agent_id: str, text: str, task_id: str) -> TaskRunResult:
        runner: Callable[..., Awaitable[Execution]] = self._run_agent
        return await self._run_task.execute(runner, agent_id, text, task_id=task_id)

    async def agents(self) -> Sequence[tuple[str, str]]:
        found: list[tuple[str, str]] = []
        for agent_id in await self._agents.list_ids():
            try:
                found.append((agent_id, (await self._agents.get(agent_id)).role))
            except OpenClawError:  # one broken definition must not hide the others
                found.append((agent_id, "invalid definition"))
        return found
