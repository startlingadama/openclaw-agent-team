"""The code-executor agent through the composition root, on the real project files.

The sandbox is a real subprocess; the LLM is scripted. One scripted LLM plays every agent in turn.
"""

import asyncio
import shutil
from pathlib import Path

import pytest

from openclaw.application.agents.run_agent import MissingToolsError
from openclaw.domain.agents.decision import CallTool, Finish
from openclaw.domain.shared.errors import ValidationError
from openclaw.domain.tasks.execution import EventType, ExecutionStatus
from openclaw.domain.tools.model import ToolCall
from openclaw.entrypoints.bootstrap import build_app
from openclaw.entrypoints.web import build_http_api
from openclaw.infrastructure.tools.code import SubprocessSandbox
from tests.unit.fakes import FakeApprovals, ListSink, ScriptedLLM

try:
    SubprocessSandbox()
except ValidationError as exc:  # no `unshare --net` here: the code tools are not offered either
    pytest.skip(f"sandbox not available: {exc}", allow_module_level=True)

PROJECT = Path(__file__).resolve().parents[2]
AGENT = "code-executor"


@pytest.fixture
def home(tmp_path):
    for name in ("agents", "skills", "teams"):
        shutil.copytree(PROJECT / name, tmp_path / name)
    (tmp_path / "workspace" / "shared").mkdir(parents=True)
    return tmp_path


def env_for(home, sandbox=True, **extra):
    env = {"OPENCLAW_HOME": str(home), "DEEPSEEK_API_KEY": "sk-test", "TAVILY_API_KEY": "t"}
    if sandbox:
        env |= {
            "OPENCLAW_CODE_SANDBOX": "subprocess",
            "OPENCLAW_CODE_WORKDIR": str(home / "sandbox"),
        }
    return env | extra


def execute(script):
    return CallTool(ToolCall("code.execute", {"script": script}))


def run_agent(app, agent, task="go"):
    async def go():
        try:
            return await app.run_agent(agent, task)
        finally:
            await app.aclose()

    return asyncio.run(go())


# -- the profile and its tools -----------------------------------------------------------------
def test_every_tool_of_the_agent_is_served_by_a_provider(home):
    app = build_app(env_for(home), llm=ScriptedLLM([]))
    agent = asyncio.run(app.agents.get(AGENT))
    declared = agent.tool_permissions.allowed | agent.tool_permissions.approval_required
    assert declared <= set(app.tools.names)
    assert agent.tool_permissions.approval_required == frozenset()
    assert {"code.execute", "code.terminal"} <= agent.tool_permissions.allowed
    assert "code.read_file" in agent.tool_permissions.allowed
    assert list(agent.skills) == ["code/code-execution", "code/data-analysis", "code/testing"]
    assert "secure code execution" in agent.profile.soul
    asyncio.run(app.aclose())


def test_the_skills_of_the_agent_load(home):
    app = build_app(env_for(home), llm=ScriptedLLM([]))
    agent = asyncio.run(app.agents.get(AGENT))
    for skill_id in agent.skills:
        skill = asyncio.run(app.skills.load(skill_id))
        assert skill.metadata.id == skill_id and skill.instructions
    asyncio.run(app.aclose())


def test_without_the_sandbox_the_agent_is_refused_before_any_llm_call(home):
    llm = ScriptedLLM([])
    app = build_app(env_for(home, sandbox=False), llm=llm)
    assert "code" in app.skipped
    with pytest.raises(MissingToolsError, match="code.execute") as exc:
        run_agent(app, AGENT)
    assert {"code.execute", "code.terminal", "code.read_file"} <= set(exc.value.missing)
    assert llm.contexts == []


# -- delegation, no approval and scripts -----------------------------------------------------------
def delegate():
    arguments = {"agent": AGENT, "objective": "Compute 6 * 7"}
    return CallTool(ToolCall("team.delegate", arguments))


def test_the_ceo_delegates_to_the_code_executor_and_nothing_is_asked(home):
    approvals, sink = FakeApprovals(approved=True), ListSink()
    llm = ScriptedLLM([delegate(), execute("print(6 * 7)"), Finish("42"), Finish("it is 42")])
    app = build_app(env_for(home), llm=llm, sink=sink, approvals=approvals)
    execution = run_agent(app, "ceo", "What is 6 * 7?")

    assert (execution.status, execution.answer) == (ExecutionStatus.COMPLETED, "it is 42")
    assert approvals.requests == []
    # the script output came back to the specialist as its observation
    assert llm.contexts[2].agent_id == AGENT
    assert "42" in llm.contexts[2].observations[-1].content


def test_an_execution_runs_at_once_even_if_every_approval_would_be_refused(home):
    approvals = FakeApprovals(approved=False)
    script = [delegate(), execute("open('created.txt', 'w').write('x')"), Finish("ok"), Finish("-")]
    llm = ScriptedLLM(script)
    app = build_app(env_for(home), llm=llm, approvals=approvals, sink=ListSink())
    run_agent(app, "ceo")
    assert approvals.requests == []
    assert list((home / "sandbox").rglob("created.txt"))


def test_a_script_call_goes_through_the_policy_without_approval(home):
    approvals, sink = FakeApprovals(approved=True), ListSink()
    script = (
        "tools.call('code.write_file', path='a.txt', content='hello')\n"
        "print(tools.call('code.read_file', path='a.txt'))\n"
        "print(tools.call('code.terminal', command='cat a.txt')['stdout'])\n"
    )
    llm = ScriptedLLM([delegate(), execute(script), Finish("done"), Finish("final")])
    app = build_app(env_for(home), llm=llm, sink=sink, approvals=approvals)
    run_agent(app, "ceo")

    # neither `code.execute` nor the `code.terminal` called by the script asked anything
    assert approvals.requests == []
    assert (home / "sandbox" / AGENT / "a.txt").read_text() == "hello"
    # the calls made by the script are traced, in the execution that started it
    via_script = [e for e in sink.events if e.data.get("via") == "script"]
    assert via_script and {e.agent_id for e in via_script} == {AGENT}


def test_a_script_cannot_use_a_tool_the_agent_may_not_use(home):
    approvals, sink = FakeApprovals(approved=True), ListSink()
    script = (
        "try:\n"
        "    tools.call('github.create_issue', repo='o/r', title='x')\n"
        "except ToolCallError as e:\n"
        "    print('denied')\n"
    )
    llm = ScriptedLLM([delegate(), execute(script), Finish("done"), Finish("final")])
    app = build_app(env_for(home, GITHUB_TOKEN="ghp_test"), llm=llm, sink=sink, approvals=approvals)
    run_agent(app, "ceo")
    assert approvals.requests == []
    assert any(e.type is EventType.POLICY_DENIED for e in sink.events)
    assert "denied" in llm.contexts[2].observations[-1].content


# -- WebChat -------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_webchat_lists_the_agent_with_the_real_permission_of_each_tool(home):
    api = build_http_api(env_for(home), llm=ScriptedLLM([]))
    agent = next(a for a in await api.list_agents() if a["id"] == AGENT)
    permissions = {tool["name"]: tool["permission"] for tool in agent["tools"]}
    assert permissions == {
        "code.execute": "WRITE",
        "code.terminal": "WRITE",
        "code.read_file": "READ",
        "code.write_file": "WRITE",
        "code.patch_file": "WRITE",
        "memory.update": permissions["memory.update"],
        "web.search": "READ",
        "web.extract": "READ",
    }
    assert {skill["name"] for skill in agent["skills"]} == {
        "Code Execution",
        "Data Analysis",
        "Testing",
    }
    default = next(t for t in await api.list_teams() if t["id"] == "default")
    assert AGENT in {member["agentId"] for member in default["members"]}
    await api.aclose()


@pytest.mark.asyncio
async def test_webchat_no_longer_shows_every_tool_as_read(home):
    api = build_http_api(env_for(home, GITHUB_TOKEN="ghp_test"), llm=ScriptedLLM([]))
    github = next(a for a in await api.list_agents() if a["id"] == "github")
    permissions = {tool["name"]: tool["permission"] for tool in github["tools"]}
    assert permissions["github.create_issue"] == "APPROVAL REQUIRED"
    assert permissions["github.search_code"] == "READ"
    await api.aclose()
