"""Tool calls made by a script: same policy, same approvals, same trace as a direct call."""

import asyncio

import pytest

from openclaw.application.agents.lineage import execution_scope
from openclaw.application.agents.script_tools import ScriptToolBroker
from openclaw.domain.shared.errors import ApprovalError, ToolError
from openclaw.domain.tasks.execution import EventType
from openclaw.domain.tools.model import RiskLevel, ToolCall
from tests.unit.fakes import FakeApprovals, FakeTools, ListSink, make_agent


class Agents:
    def __init__(self, agent):
        self.agent = agent

    async def get(self, agent_id):
        return self.agent

    async def list_ids(self):
        return [self.agent.id]


def make(agent, approvals=None):
    tools, sink = FakeTools(), ListSink()
    tools.add("data.read", RiskLevel.READ, lambda call: {"rows": 3})
    tools.add("data.write", RiskLevel.WRITE, lambda call: "written")
    tools.add("data.other", RiskLevel.READ)
    approvals = approvals or FakeApprovals(approved=True)
    broker = ScriptToolBroker(agents=Agents(agent), tools=tools, approvals=approvals, sink=sink)
    return broker, tools, approvals, sink


def call(broker, name, **arguments):
    async def go():
        with execution_scope("exec-1", "task-1"):
            return await broker.call(ToolCall(name, arguments), "test")

    return asyncio.run(go())


def test_a_permitted_read_tool_runs_and_is_traced_as_a_script_call():
    broker, tools, approvals, sink = make(make_agent(allowed=["data.read"]))
    assert call(broker, "data.read") == {"rows": 3}
    assert approvals.requests == []
    assert [e.type for e in sink.events] == [EventType.TOOL_CALLED, EventType.TOOL_RESULT]
    assert {e.execution_id for e in sink.events} == {"exec-1"}
    assert {e.task_id for e in sink.events} == {"task-1"}
    assert all(e.agent_id == "test" and e.data["via"] == "script" for e in sink.events)


def test_a_tool_the_agent_does_not_have_is_denied_and_never_runs():
    broker, tools, _, sink = make(make_agent(allowed=["data.read"]))
    with pytest.raises(ToolError, match="denied"):
        call(broker, "data.other")
    assert tools.calls == []
    assert [e.type for e in sink.events] == [EventType.POLICY_DENIED]


def test_an_unknown_tool_is_denied():
    broker, tools, _, _ = make(make_agent(allowed=["data.nothing"]))
    with pytest.raises(ToolError, match="unknown tool"):
        call(broker, "data.nothing")
    assert tools.calls == []


def test_approval_required_asks_the_human_with_the_agent_id():
    broker, tools, approvals, sink = make(make_agent(approval_required=["data.write"]))
    assert call(broker, "data.write") == "written"
    (request,) = approvals.requests
    assert (request.agent_id, request.execution_id, request.call.name) == (
        "test",
        "exec-1",
        "data.write",
    )
    types = [e.type for e in sink.events]
    assert types == [
        EventType.APPROVAL_REQUESTED,
        EventType.APPROVAL_RESOLVED,
        EventType.TOOL_CALLED,
        EventType.TOOL_RESULT,
    ]


def test_a_rejected_call_does_not_run():
    broker, tools, _, _ = make(
        make_agent(approval_required=["data.write"]), FakeApprovals(approved=False)
    )
    with pytest.raises(ToolError, match="rejected"):
        call(broker, "data.write")
    assert tools.calls == []


def test_an_approval_failure_means_no_execution():
    broker, tools, _, _ = make(
        make_agent(approval_required=["data.write"]), FakeApprovals(error=ApprovalError("no ui"))
    )
    with pytest.raises(ToolError, match="rejected"):
        call(broker, "data.write")
    assert tools.calls == []


def test_a_failing_tool_is_reported_and_traced():
    broker, tools, _, sink = make(make_agent(allowed=["data.read"]))

    def boom(_call):
        raise ToolError("backend down")

    tools.handlers["data.read"] = boom
    with pytest.raises(ToolError, match="backend down"):
        call(broker, "data.read")
    assert sink.events[-1].data["status"] == "error"


def test_outside_a_run_nothing_is_executed():
    broker, tools, _, _ = make(make_agent(allowed=["data.read"]))
    with pytest.raises(ToolError, match="during an agent run"):
        asyncio.run(broker.call(ToolCall("data.read", {}), "test"))
    assert tools.calls == []
