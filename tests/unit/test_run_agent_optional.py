"""RunAgent and `tools.optional`: an absent optional tool is tolerated, any other one refuses."""

import asyncio

import pytest

from openclaw.application.agents.run_agent import MissingToolsError, RunAgent
from openclaw.domain.agents.model import Agent, AgentProfile
from openclaw.domain.tasks.execution import Execution, ExecutionStatus
from openclaw.domain.tools.permissions import ToolPermissions
from tests.unit.fakes import FakeTools


class OneAgent:
    def __init__(self, agent: Agent) -> None:
        self._agent = agent

    async def get(self, agent_id: str) -> Agent:
        return self._agent

    async def list_ids(self) -> list[str]:
        return [self._agent.id]


class SpyRuntime:
    def __init__(self) -> None:
        self.runs: list[str] = []

    async def run(self, agent, message):
        self.runs.append(message.content)
        return Execution("e1", "t1", agent.id, ExecutionStatus.COMPLETED, "ok", None, 1, ())


def build(permissions: ToolPermissions, *served: str):
    tools = FakeTools()
    for name in served:
        tools.add(name)
    agent = Agent(id="w", profile=AgentProfile(soul="# Soul"), tool_permissions=permissions)
    runtime = SpyRuntime()
    return RunAgent(agents=OneAgent(agent), runtime=runtime, tools=tools), runtime


def run(run_agent: RunAgent):
    return asyncio.run(run_agent("w", "go"))


def test_an_absent_optional_tool_does_not_refuse_the_agent():
    perms = ToolPermissions.of(["a"], ["b"], optional=["b"])
    run_agent, runtime = build(perms, "a")  # "b" is served by no provider
    assert run(run_agent).answer == "ok"
    assert runtime.runs == ["go"]


def test_an_absent_tool_that_is_not_optional_still_refuses_before_the_runtime():
    perms = ToolPermissions.of(["a", "c"], ["b"], optional=["b"])
    run_agent, runtime = build(perms, "a")  # "c" is missing and required
    with pytest.raises(MissingToolsError) as exc:
        run(run_agent)
    assert exc.value.missing == ("c",)  # the optional "b" is not reported
    assert runtime.runs == []


def test_without_optional_an_absent_tool_refuses_as_before():
    run_agent, runtime = build(ToolPermissions.of(["a"], ["b"]), "a")
    with pytest.raises(MissingToolsError) as exc:
        run(run_agent)
    assert exc.value.missing == ("b",)
    assert runtime.runs == []


def test_a_served_optional_tool_is_used_normally():
    perms = ToolPermissions.of(["a"], ["b"], optional=["b"])
    run_agent, _ = build(perms, "a", "b")
    assert run(run_agent).status == ExecutionStatus.COMPLETED
