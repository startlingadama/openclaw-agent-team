"""Use case: the supervisor of a team hands a task to one of its members (ADR-010).

Rules, all enforced here and never left to the LLM:

- the caller must be the supervisor of the active team (`team_scope`, else `default_team`);
- the target must be a member of that team;
- delegation is one level deep: an agent that is running a delegated task cannot delegate;
- the member runs under its own permissions and approvals: a delegation never widens them.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from openclaw.application.agents.run_agent import RunAgent
from openclaw.application.teams.context import active_team, delegating, is_delegating
from openclaw.domain.agents.model import Agent, AgentId
from openclaw.domain.agents.ports import AgentRepository
from openclaw.domain.shared.errors import AuthorizationError, TaskError, TeamError, ValidationError
from openclaw.domain.skills.model import SkillId
from openclaw.domain.tasks.execution import Execution
from openclaw.domain.tasks.task import Task
from openclaw.domain.teams.ports import TeamRepository
from openclaw.domain.teams.team import Team, TeamPattern


@dataclass(frozen=True, slots=True)
class Delegation:
    task: Task
    execution: Execution


class DelegateTask:
    def __init__(
        self,
        *,
        teams: TeamRepository,
        agents: AgentRepository,
        run_agent: RunAgent,
        default_team: str = "default",
    ) -> None:
        self._teams = teams
        self._agents = agents
        self._run_agent = run_agent
        self._default_team = default_team

    async def team_of(self, supervisor: AgentId) -> Team:
        """The active team, provided `supervisor` leads it."""
        team = await self._teams.get(active_team() or self._default_team)
        if team.pattern is not TeamPattern.SUPERVISOR:
            raise TeamError(f"team '{team.id}' does not use the supervisor pattern")
        if team.supervisor != supervisor:
            raise AuthorizationError(
                f"'{supervisor}' is not the supervisor of team '{team.id}' and cannot delegate"
            )
        return team

    async def members(self, supervisor: AgentId) -> list[Agent]:
        team = await self.team_of(supervisor)
        return [await self._agents.get(member) for member in team.members]

    async def __call__(
        self,
        supervisor: AgentId,
        to_agent: str,
        objective: str,
        *,
        context: str = "",
        constraints: Sequence[str] = (),
        required_skills: Sequence[str] = (),
    ) -> Delegation:
        if is_delegating():
            raise TaskError("nested delegation is not allowed: answer with your own result")
        team = await self.team_of(supervisor)
        if to_agent not in team.members:
            members = ", ".join(team.members) or "none"
            raise TaskError(
                f"'{to_agent}' is not a member of team '{team.id}' (members: {members})"
            )
        if not objective.strip():
            raise ValidationError("the objective must not be empty")
        member = await self._agents.get(to_agent)
        unknown = [s for s in required_skills if s not in member.skills]
        if unknown:
            raise ValidationError(
                f"agent '{member.id}' does not have the skills: {', '.join(unknown)} "
                f"(it has: {', '.join(member.skills) or 'none'})"
            )
        task = Task(
            task_id=uuid.uuid4().hex,
            from_agent=supervisor,
            to_agent=member.id,
            objective=objective.strip(),
            context={"text": context.strip()} if context.strip() else {},
            constraints=tuple(c.strip() for c in constraints if c.strip()),
            required_skills=tuple(SkillId(s) for s in required_skills),
        )
        with delegating():
            execution = await self._run_agent(
                member.id, render_task(task), sender=supervisor, task_id=task.task_id
            )
        return Delegation(task, execution)


def render_task(task: Task) -> str:
    """The task as the member reads it: its request, in plain text."""
    parts = [task.objective]
    if text := task.context.get("text"):
        parts.append(f"Context:\n{text}")
    if task.constraints:
        parts.append("Constraints:\n" + "\n".join(f"- {c}" for c in task.constraints))
    if task.required_skills:
        parts.append("Use these skills: " + ", ".join(task.required_skills))
    return "\n\n".join(parts)
