"""RunTeam: the task goes to the supervisor, inside that team's scope (fake ports only)."""

import asyncio

import pytest

from openclaw.application.teams.context import active_team
from openclaw.application.teams.run_team import RunTeam
from openclaw.domain.shared.errors import TeamError
from openclaw.domain.tasks.execution import Execution, ExecutionStatus
from openclaw.domain.teams.team import Team, TeamPattern
from tests.unit.test_delegation import FakeTeams

RESEARCH = Team("research", TeamPattern.SUPERVISOR, ("web",), "lead")
SOLO = Team("solo", TeamPattern.SUPERVISOR, ("email",), "ceo")
CHAT = Team("chat", TeamPattern.PEER_TO_PEER, ("a", "b"))


class SpyRunAgent:
    def __init__(self):
        self.calls = []

    async def __call__(self, agent_id, task, *, sender=None, task_id=None):
        self.calls.append((agent_id, task, active_team()))
        return Execution("e1", "t1", agent_id, ExecutionStatus.COMPLETED, "ok", None, 1, ())


def build(*teams):
    spy = SpyRunAgent()
    return RunTeam(teams=FakeTeams(*teams), run_agent=spy), spy


def test_the_supervisor_runs_the_task_inside_the_team_scope():
    run_team, spy = build(RESEARCH, SOLO)
    execution = asyncio.run(run_team("research", "compare A and B"))
    assert execution.answer == "ok"
    assert spy.calls == [("lead", "compare A and B", "research")]
    assert active_team() is None  # the scope does not outlive the run


def test_an_unknown_team_is_an_error():
    run_team, spy = build(RESEARCH)
    with pytest.raises(TeamError, match="unknown team 'nope'"):
        asyncio.run(run_team("nope", "x"))
    assert spy.calls == []


def test_only_supervisor_teams_can_run():
    run_team, spy = build(CHAT)
    with pytest.raises(TeamError, match="only supervisor teams"):
        asyncio.run(run_team("chat", "x"))
    assert spy.calls == []


def test_list_teams_returns_every_team_sorted():
    run_team, _ = build(SOLO, CHAT, RESEARCH)
    teams = asyncio.run(run_team.list_teams())
    assert [t.id for t in teams] == ["chat", "research", "solo"]
