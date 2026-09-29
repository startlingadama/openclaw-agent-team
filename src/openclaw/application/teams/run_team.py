"""Use case: run a team on a task (what `openclaw team run <team> "<task>"` triggers).

A team is led by its supervisor: the task goes to the supervisor, and every delegation it makes
stays inside that team (`team_scope`), whatever `OPENCLAW_TEAM` says. Only the supervisor pattern
can be run this way; the other patterns have no single agent to start from.
"""

from __future__ import annotations

from openclaw.application.agents.run_agent import RunAgent
from openclaw.application.teams.context import team_scope
from openclaw.domain.shared.errors import TeamError
from openclaw.domain.tasks.execution import Execution
from openclaw.domain.teams.ports import TeamRepository
from openclaw.domain.teams.team import Team, TeamPattern


class RunTeam:
    def __init__(self, *, teams: TeamRepository, run_agent: RunAgent) -> None:
        self._teams = teams
        self._run_agent = run_agent

    async def __call__(self, team_id: str, task: str, *, task_id: str | None = None) -> Execution:
        team = await self._teams.get(team_id)
        if team.pattern is not TeamPattern.SUPERVISOR or team.supervisor is None:
            raise TeamError(
                f"team '{team.id}' uses the {team.pattern} pattern: only supervisor teams can run"
            )
        with team_scope(team.id):
            return await self._run_agent(team.supervisor, task, task_id=task_id)

    async def list_teams(self) -> list[Team]:
        return [await self._teams.get(team_id) for team_id in await self._teams.list_ids()]
