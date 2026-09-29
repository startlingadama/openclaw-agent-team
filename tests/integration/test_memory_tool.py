"""An agent writes its own memory through `memory.update`, end to end on real files."""

import asyncio

import pytest

from openclaw.application.agents.runtime import AgentRuntime
from openclaw.application.memory.tools import MEMORY_UPDATE, MemoryToolProvider
from openclaw.domain.agents.decision import CallTool, Finish
from openclaw.domain.memory.model import MemoryLayer
from openclaw.domain.messages.model import AgentMessage
from openclaw.domain.shared.errors import ToolError, ValidationError
from openclaw.domain.tasks.execution import ExecutionStatus
from openclaw.domain.tools.model import RiskLevel, ToolCall
from openclaw.infrastructure.memory.markdown.repository import MarkdownMemoryRepository
from tests.unit.fakes import FakeApprovals, FakeSkills, ScriptedLLM, make_agent


@pytest.fixture
def world(tmp_path):
    for name in ("github", "linkedin"):
        (tmp_path / "agents" / name).mkdir(parents=True)
        (tmp_path / "agents" / name / "MEMORY.md").write_text(f"# Memory {name}\n")
        (tmp_path / "agents" / name / "USER.md").write_text("# User\n")
    repo = MarkdownMemoryRepository(tmp_path / "agents", tmp_path / "workspace")
    return tmp_path, repo, MemoryToolProvider(repo)


def run_agent(repo, tools, script, agent):
    runtime = AgentRuntime(
        llm=ScriptedLLM(script),
        tools=tools,
        skills=FakeSkills(),
        memory=repo,
        approvals=FakeApprovals(approved=False),
    )
    message = AgentMessage("user", agent.id, "t1", "remember this")
    return asyncio.run(runtime.run(agent, message))


def agent_named(name, allowed=(MEMORY_UPDATE,)):
    from dataclasses import replace

    return replace(make_agent(allowed=allowed), id=name)


def test_the_tool_is_a_write_tool_with_a_spec(world):
    _, _, tools = world
    spec = tools.get_spec(MEMORY_UPDATE)
    assert spec.risk_level is RiskLevel.WRITE and spec.input_schema["required"] == [
        "section",
        "content",
    ]
    assert tools.get_spec("memory.other") is None


def test_agent_writes_its_own_memory_and_reads_it_next_run(world):
    tmp, repo, tools = world
    call = CallTool(ToolCall(MEMORY_UPDATE, {"section": "Lessons", "content": "use uv"}))
    result = run_agent(repo, tools, [call, Finish("saved")], agent_named("github"))
    assert result.status is ExecutionStatus.COMPLETED
    assert "## Lessons\n\nuse uv" in (tmp / "agents/github/MEMORY.md").read_text()
    # the next run sees it in its context
    llm = ScriptedLLM([Finish("ok")])
    runtime = AgentRuntime(
        llm=llm, tools=tools, skills=FakeSkills(), memory=repo, approvals=FakeApprovals()
    )
    asyncio.run(runtime.run(agent_named("github"), AgentMessage("u", "github", "t2", "hi")))
    assert "use uv" in llm.contexts[0].memory[MemoryLayer.AGENT]


def test_user_layer_goes_to_user_md(world):
    tmp, repo, tools = world
    args = {"layer": "user", "section": "Preferences", "content": "Python"}
    run_agent(
        repo, tools, [CallTool(ToolCall(MEMORY_UPDATE, args)), Finish("ok")], agent_named("github")
    )
    assert "Python" in (tmp / "agents/github/USER.md").read_text()


def test_an_agent_cannot_write_another_agents_memory(world):
    tmp, repo, tools = world
    args = {"section": "Lessons", "content": "hacked", "agent_id": "linkedin"}
    run_agent(
        repo, tools, [CallTool(ToolCall(MEMORY_UPDATE, args)), Finish("ok")], agent_named("github")
    )
    assert "hacked" not in (tmp / "agents/linkedin/MEMORY.md").read_text()
    assert "hacked" in (tmp / "agents/github/MEMORY.md").read_text()  # the caller's own memory


def test_an_agent_without_the_permission_cannot_write(world):
    tmp, repo, tools = world
    call = CallTool(ToolCall(MEMORY_UPDATE, {"section": "Lessons", "content": "x"}))
    result = run_agent(repo, tools, [call, Finish("ok")], agent_named("github", allowed=()))
    assert (tmp / "agents/github/MEMORY.md").read_text() == "# Memory github\n"
    assert result.status is ExecutionStatus.COMPLETED  # the denial is an observation


def test_invalid_arguments_are_rejected(world):
    _, _, tools = world
    bad = [
        {"section": 1, "content": "x"},
        {"section": "S", "content": None},
        {"section": "S", "content": "x", "layer": "shared_team"},
    ]
    for args in bad:
        with pytest.raises(ValidationError):
            asyncio.run(tools.execute(ToolCall(MEMORY_UPDATE, args), "github"))
    with pytest.raises(ToolError):
        asyncio.run(tools.execute(ToolCall("memory.nope"), "github"))


def test_memory_tool_through_the_tool_registry(world):
    from openclaw.domain.tools.registry import ToolRegistry

    tmp, repo, provider = world
    registry = ToolRegistry([provider])
    call = CallTool(ToolCall(MEMORY_UPDATE, {"section": "Lessons", "content": "via registry"}))
    run_agent(repo, registry, [call, Finish("ok")], agent_named("github"))
    assert "via registry" in (tmp / "agents/github/MEMORY.md").read_text()
    # a registered tool is still unusable without an explicit permission
    run_agent(repo, registry, [call, Finish("ok")], agent_named("linkedin", allowed=()))
    assert "via registry" not in (tmp / "agents/linkedin/MEMORY.md").read_text()
