"""DelegateTask and the team tools, with fake ports (no files, no LLM)."""

import asyncio

import pytest

from openclaw.application.teams.context import active_team, team_scope
from openclaw.application.teams.delegate import DelegateTask, render_task
from openclaw.application.teams.tools import TEAM_DELEGATE, TEAM_MEMBERS, TeamToolProvider
from openclaw.domain.agents.model import Agent, AgentProfile
from openclaw.domain.shared.errors import (
    AgentError,
    AuthorizationError,
    TaskError,
    TeamError,
    ValidationError,
)
from openclaw.domain.tasks.execution import Execution, ExecutionStatus
from openclaw.domain.teams.team import Team, TeamPattern
from openclaw.domain.tools.model import RiskLevel, ToolCall
from openclaw.domain.tools.permissions import ToolPermissions


def agent(agent_id, skills=(), tools=()):
    return Agent(
        id=agent_id,
        profile=AgentProfile(soul="# Soul"),
        skills=tuple(skills),
        tool_permissions=ToolPermissions.of(tools),
    )


class FakeTeams:
    def __init__(self, *teams):
        self.teams = {t.id: t for t in teams}

    async def get(self, team_id):
        if team_id not in self.teams:
            raise TeamError(f"unknown team '{team_id}'")
        return self.teams[team_id]

    async def list_ids(self):
        return sorted(self.teams)


class FakeAgents:
    def __init__(self, *agents):
        self.agents = {a.id: a for a in agents}

    async def get(self, agent_id):
        if agent_id not in self.agents:
            raise AgentError(f"unknown agent '{agent_id}'")
        return self.agents[agent_id]

    async def list_ids(self):
        return sorted(self.agents)


class FakeRunAgent:
    def __init__(self, status=ExecutionStatus.COMPLETED, answer="done", error=None, hook=None):
        self.status, self.answer, self.error, self.hook = status, answer, error, hook
        self.calls = []

    async def __call__(self, agent_id, task, *, sender=None, task_id=None):
        self.calls.append({"agent": agent_id, "task": task, "sender": sender, "task_id": task_id})
        if self.hook:
            await self.hook()
        return Execution("exec-1", task_id, agent_id, self.status, self.answer, self.error, 3, ())


DEFAULT = Team("default", TeamPattern.SUPERVISOR, ("research", "email"), "ceo")
OTHER = Team("solo", TeamPattern.SUPERVISOR, ("email",), "ceo")
P2P = Team("chat", TeamPattern.PEER_TO_PEER, ("research", "email"))


def build(run_agent=None, teams=(DEFAULT, OTHER, P2P)):
    run_agent = run_agent or FakeRunAgent()
    delegate = DelegateTask(
        teams=FakeTeams(*teams),
        agents=FakeAgents(
            agent("research", ["research/web-research"], ["web.search", "memory.update"]),
            agent("email", ["google/email-search"], ["google.search_email"]),
            agent("ceo"),
        ),
        run_agent=run_agent,
    )
    return delegate, run_agent


def run(coro):
    return asyncio.run(coro)


# -- DelegateTask ---------------------------------------------------------------------------
def test_delegation_runs_the_member_with_the_supervisor_as_sender():
    delegate, runner = build()
    result = run(
        delegate(
            "ceo",
            "research",
            "Compare A and B",
            context="  for a board memo ",
            constraints=["cite sources", " "],
        )
    )
    call = runner.calls[0]
    assert (call["agent"], call["sender"]) == ("research", "ceo")
    assert call["task_id"] == result.task.task_id
    assert call["task"].startswith("Compare A and B")
    assert "Context:\nfor a board memo" in call["task"]
    assert "- cite sources" in call["task"]
    assert result.task.from_agent == "ceo"
    assert result.task.to_agent == "research"
    assert result.task.constraints == ("cite sources",)
    assert result.execution.answer == "done"


def test_render_task_is_just_the_objective_when_nothing_else_is_given():
    delegate, _ = build()
    result = run(delegate("ceo", "email", "  Summarize the inbox "))
    assert render_task(result.task) == "Summarize the inbox"


def test_required_skills_must_belong_to_the_member():
    delegate, runner = build()
    result = run(delegate("ceo", "research", "x", required_skills=["research/web-research"]))
    assert "Use these skills: research/web-research" in runner.calls[0]["task"]
    assert result.task.required_skills == ("research/web-research",)
    with pytest.raises(ValidationError, match="does not have the skills: google/email-search"):
        run(delegate("ceo", "research", "x", required_skills=["google/email-search"]))


def test_only_members_can_receive_a_task():
    delegate, runner = build()
    with pytest.raises(TaskError, match=r"'github' is not a member.*research, email"):
        run(delegate("ceo", "github", "x"))
    with pytest.raises(TaskError, match="not a member"):
        run(delegate("ceo", "ceo", "x"))  # the supervisor is not its own member
    assert runner.calls == []


def test_only_the_supervisor_can_delegate():
    delegate, runner = build()
    with pytest.raises(AuthorizationError, match="not the supervisor"):
        run(delegate("research", "email", "x"))
    assert runner.calls == []


def test_empty_objective_is_refused():
    delegate, _ = build()
    with pytest.raises(ValidationError, match="objective"):
        run(delegate("ceo", "research", "   "))


def test_unknown_member_agent_is_an_agent_error():
    delegate, _ = build(teams=(Team("default", TeamPattern.SUPERVISOR, ("ghost",), "ceo"),))
    with pytest.raises(AgentError, match="unknown agent 'ghost'"):
        run(delegate("ceo", "ghost", "x"))


def test_a_team_that_is_not_a_supervisor_team_cannot_be_delegated_in():
    delegate, _ = build()
    with team_scope("chat"), pytest.raises(TeamError, match="supervisor pattern"):
        run(delegate("ceo", "research", "x"))


def test_team_scope_selects_the_team_and_is_restored():
    delegate, _ = build()
    assert active_team() is None
    with team_scope("solo"):
        assert active_team() == "solo"
        with pytest.raises(TaskError, match=r"team 'solo' \(members: email\)"):
            run(delegate("ceo", "research", "x"))
    assert active_team() is None
    assert run(delegate("ceo", "research", "x")).execution.agent_id == "research"


def test_unknown_team_is_an_error():
    delegate, _ = build()
    with team_scope("nope"), pytest.raises(TeamError, match="unknown team 'nope'"):
        run(delegate("ceo", "research", "x"))


def test_delegation_is_one_level_deep():
    nested = []

    async def hook():
        try:
            await delegate("ceo", "email", "again")
        except TaskError as exc:
            nested.append(str(exc))

    delegate, runner = build(FakeRunAgent(hook=hook))
    run(delegate("ceo", "research", "x"))
    assert nested and "nested delegation" in nested[0]
    assert len(runner.calls) == 1
    # and the flag is released afterwards
    run(delegate("ceo", "email", "y"))
    assert len(runner.calls) == 2


def test_the_delegation_flag_is_released_when_the_member_fails():
    async def hook():
        raise AgentError("boom")

    delegate, _ = build(FakeRunAgent(hook=hook))
    with pytest.raises(AgentError):
        run(delegate("ceo", "research", "x"))
    delegate2, runner2 = build()
    run(delegate2("ceo", "research", "x"))
    assert len(runner2.calls) == 1


# -- tools ----------------------------------------------------------------------------------
def provider(runner=None):
    delegate, runner = build(runner)
    return TeamToolProvider(delegate), runner


def execute(tool, arguments, caller="ceo", prov=None):
    prov = prov or provider()[0]
    return run(prov.execute(ToolCall(tool, arguments), caller))


def test_tool_specs():
    prov, _ = provider()
    assert prov.tool_names == {TEAM_MEMBERS, TEAM_DELEGATE}
    members, delegate = prov.get_spec(TEAM_MEMBERS), prov.get_spec(TEAM_DELEGATE)
    assert members.risk_level is RiskLevel.READ
    assert delegate.risk_level is RiskLevel.WRITE  # never retried automatically
    assert delegate.input_schema["required"] == ["agent", "objective"]
    assert prov.get_spec("team.other") is None


def test_members_lists_skills_and_tools():
    out = execute(TEAM_MEMBERS, {})
    assert out == [
        {
            "agent": "research",
            "skills": ["research/web-research"],
            "tools": ["memory.update", "web.search"],
        },
        {"agent": "email", "skills": ["google/email-search"], "tools": ["google.search_email"]},
    ]


def test_members_is_for_the_supervisor_only():
    with pytest.raises(AuthorizationError):
        execute(TEAM_MEMBERS, {}, caller="research")


def test_delegate_returns_the_member_result():
    prov, runner = provider()
    out = execute(TEAM_DELEGATE, {"agent": "research", "objective": "Compare A and B"}, prov=prov)
    assert out["agent"] == "research"
    assert (out["status"], out["answer"], out["error"]) == ("completed", "done", None)
    assert out["steps"] == 3
    assert out["task_id"] == runner.calls[0]["task_id"]


def test_a_failed_member_run_is_a_result_not_an_error():
    prov, _ = provider(
        FakeRunAgent(status=ExecutionStatus.FAILED, answer=None, error="LLM unavailable")
    )
    out = execute(TEAM_DELEGATE, {"agent": "research", "objective": "x"}, prov=prov)
    assert (out["status"], out["answer"], out["error"]) == ("failed", None, "LLM unavailable")


def test_a_partial_answer_keeps_its_status():
    prov, _ = provider(
        FakeRunAgent(
            status=ExecutionStatus.MAX_STEPS_EXCEEDED, answer="partial", error="step limit"
        )
    )
    out = execute(TEAM_DELEGATE, {"agent": "email", "objective": "x"}, prov=prov)
    assert (out["status"], out["answer"]) == ("max_steps_exceeded", "partial")


@pytest.mark.parametrize(
    "arguments",
    [
        {"agent": "", "objective": "x"},
        {"agent": 3, "objective": "x"},
        {"agent": "research", "objective": None},
        {"agent": "research", "objective": "x", "context": 5},
        {"agent": "research", "objective": "x", "constraints": "one"},
        {"agent": "research", "objective": "x", "required_skills": [1]},
    ],
)
def test_delegate_arguments_are_validated(arguments):
    prov, runner = provider()
    with pytest.raises(ValidationError):
        execute(TEAM_DELEGATE, arguments, prov=prov)
    assert runner.calls == []


def test_the_llm_cannot_choose_the_supervisor_or_the_team():
    prov, runner = provider()
    execute(
        TEAM_DELEGATE,
        {"agent": "research", "objective": "x", "supervisor": "research", "team": "solo"},
        prov=prov,
    )
    assert runner.calls[0]["sender"] == "ceo"
    assert runner.calls[0]["agent"] == "research"  # 'solo' has no 'research': it was ignored
