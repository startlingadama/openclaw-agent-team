"""The writer agent through the composition root, on the real project files (ADR-026).

The LLM is scripted. One scripted LLM plays every agent in turn. Documents go to a temporary
directory (`OPENCLAW_DOCS_DIR`).
"""

import asyncio
import shutil
from pathlib import Path

import pytest

from openclaw.application.agents.run_agent import MissingToolsError
from openclaw.domain.agents.decision import CallTool, Finish
from openclaw.domain.tasks.execution import ExecutionStatus
from openclaw.domain.tools.model import ToolCall
from openclaw.entrypoints.bootstrap import build_app
from openclaw.entrypoints.web import build_http_api
from openclaw.infrastructure.tools.docs import latex
from tests.unit.fakes import FakeApprovals, ListSink, ScriptedLLM

PROJECT = Path(__file__).resolve().parents[2]
AGENT = "writer"
PUBLISHING = ("github.create", "linkedin.", "google.send", "google.create", "team.")
GOOD_TEX = "\\documentclass{article}\n\\begin{document}\nHello\n\\end{document}\n"


@pytest.fixture
def home(tmp_path):
    for name in ("agents", "skills", "teams"):
        shutil.copytree(PROJECT / name, tmp_path / name)
    (tmp_path / "workspace" / "shared").mkdir(parents=True)
    return tmp_path


def env_for(home, **extra):
    env = {
        "OPENCLAW_HOME": str(home),
        "DEEPSEEK_API_KEY": "sk-test",
        "TAVILY_API_KEY": "t",
        "OPENCLAW_DOCS_DIR": str(home / "docs"),
    }
    return env | extra


def run_agent(app, agent, task="go"):
    async def go():
        try:
            return await app.run_agent(agent, task)
        finally:
            await app.aclose()

    return asyncio.run(go())


def delegate():
    return CallTool(ToolCall("team.delegate", {"agent": AGENT, "objective": "Write a note"}))


def write(path, content):
    return CallTool(ToolCall("docs.write", {"path": path, "content": content}))


# -- the profile and its tools -----------------------------------------------------------------
def test_the_profile_and_the_skills_load(home):
    app = build_app(env_for(home), llm=ScriptedLLM([]))
    agent = asyncio.run(app.agents.get(AGENT))
    assert agent.role == "Documentation specialist"
    assert "expert technical writer" in agent.profile.soul
    assert list(agent.skills) == [
        "writing/technical-documentation",
        "writing/report-writing",
        "writing/editing-proofreading",
        "writing/latex-documents",
    ]
    for skill_id in agent.skills:
        skill = asyncio.run(app.skills.load(skill_id))
        assert skill.metadata.id == skill_id and skill.instructions
    asyncio.run(app.aclose())


def test_every_tool_of_the_agent_is_served_and_none_publishes(home):
    app = build_app(env_for(home), llm=ScriptedLLM([]))
    agent = asyncio.run(app.agents.get(AGENT))
    permissions = agent.tool_permissions
    declared = permissions.allowed | permissions.approval_required
    if "docs.compile_pdf" in app.skipped:
        pytest.skip(app.skipped["docs.compile_pdf"])
    assert declared <= set(app.tools.names)
    assert permissions.approval_required == {"docs.compile_pdf"}
    assert declared == {
        "memory.update",
        "web.search",
        "web.open",
        "web.extract",
        "docs.read",
        "docs.write",
        "docs.patch",
        "docs.compile_pdf",
    }
    assert not any(name.startswith(PUBLISHING) for name in declared)
    asyncio.run(app.aclose())


def test_the_writer_is_a_member_of_the_default_team_only(home):
    app = build_app(env_for(home), llm=ScriptedLLM([]))
    members = {
        team_id: asyncio.run(app.teams.get(team_id)).members
        for team_id in asyncio.run(app.teams.list_ids())
    }
    assert AGENT in members["default"]
    assert [t for t, m in members.items() if AGENT in m] == ["default"]
    asyncio.run(app.aclose())


def test_without_a_latex_engine_the_agent_runs_without_the_compile_tool(home, monkeypatch):
    monkeypatch.setattr(latex, "find_engine", lambda name: None)  # a machine without LaTeX
    llm = ScriptedLLM([write("d/doc.tex", GOOD_TEX), Finish("source written, no PDF here")])
    app = build_app(env_for(home), llm=llm)
    assert "no LaTeX engine" in app.skipped["docs.compile_pdf"]
    execution = run_agent(app, AGENT)
    assert (execution.status, execution.answer) == (
        ExecutionStatus.COMPLETED,
        "source written, no PDF here",
    )
    offered = {spec.name for spec in llm.contexts[0].tools}
    assert "docs.compile_pdf" not in offered
    assert {"docs.read", "docs.write", "docs.patch"} <= offered
    assert (home / "docs" / "d" / "doc.tex").read_text() == GOOD_TEX


def test_without_a_latex_engine_a_compile_call_is_denied_not_run(home, monkeypatch):
    monkeypatch.setattr(latex, "find_engine", lambda name: None)
    approvals = FakeApprovals(approved=True)
    compile_call = CallTool(ToolCall("docs.compile_pdf", {"path": "d/doc.tex"}))
    llm = ScriptedLLM([write("d/doc.tex", GOOD_TEX), compile_call, Finish("no PDF")])
    app = build_app(env_for(home), llm=llm, approvals=approvals)
    execution = run_agent(app, AGENT)
    assert execution.status == ExecutionStatus.COMPLETED
    assert approvals.requests == []  # never reached: the tool does not exist here
    assert not (home / "docs" / "d" / "doc.pdf").exists()


def test_another_missing_tool_still_refuses_the_writer_before_any_llm_call(home):
    llm = ScriptedLLM([])
    env = env_for(home)
    env.pop("TAVILY_API_KEY")  # web.* is not optional for the writer
    app = build_app(env, llm=llm)
    if "web.search" not in app.skipped:
        pytest.skip("a web provider is available without a key on this machine")
    with pytest.raises(MissingToolsError, match="web.search"):
        run_agent(app, AGENT)
    assert llm.contexts == []


def test_the_compile_tool_is_declared_optional_and_stays_under_approval(home):
    app = build_app(env_for(home), llm=ScriptedLLM([]))
    permissions = asyncio.run(app.agents.get(AGENT)).tool_permissions
    assert permissions.optional == {"docs.compile_pdf"}
    assert permissions.approval_required == {"docs.compile_pdf"}
    asyncio.run(app.aclose())


# -- delegation and approvals ----------------------------------------------------------------
def test_the_ceo_delegates_and_the_document_is_written_without_approval(home):
    approvals = FakeApprovals(approved=True)
    llm = ScriptedLLM(
        [delegate(), write("note/note.md", "# Note"), Finish("written"), Finish("ok")]
    )
    app = build_app(env_for(home), llm=llm, sink=ListSink(), approvals=approvals)
    if "docs.compile_pdf" in app.skipped:
        pytest.skip(app.skipped["docs.compile_pdf"])
    execution = run_agent(app, "ceo", "Write a note")
    assert (execution.status, execution.answer) == (ExecutionStatus.COMPLETED, "ok")
    assert (home / "docs" / "note" / "note.md").read_text() == "# Note"
    assert approvals.requests == []  # writing a document has no external effect


def test_compiling_asks_approval_with_the_id_of_the_specialist(home):
    approvals = FakeApprovals(approved=True)
    compile_call = CallTool(ToolCall("docs.compile_pdf", {"path": "d/doc.tex"}))
    llm = ScriptedLLM(
        [delegate(), write("d/doc.tex", GOOD_TEX), compile_call, Finish("built"), Finish("done")]
    )
    app = build_app(env_for(home), llm=llm, sink=ListSink(), approvals=approvals)
    if "docs.compile_pdf" in app.skipped:
        pytest.skip(app.skipped["docs.compile_pdf"])
    run_agent(app, "ceo")
    assert [(a.agent_id, a.call.name) for a in approvals.requests] == [(AGENT, "docs.compile_pdf")]
    assert (home / "docs" / "d" / "doc.pdf").read_bytes().startswith(b"%PDF")


def test_a_refused_compilation_produces_no_pdf(home):
    approvals = FakeApprovals(approved=False)
    compile_call = CallTool(ToolCall("docs.compile_pdf", {"path": "d/doc.tex"}))
    llm = ScriptedLLM(
        [delegate(), write("d/doc.tex", GOOD_TEX), compile_call, Finish("no"), Finish("-")]
    )
    app = build_app(env_for(home), llm=llm, sink=ListSink(), approvals=approvals)
    if "docs.compile_pdf" in app.skipped:
        pytest.skip(app.skipped["docs.compile_pdf"])
    run_agent(app, "ceo")
    assert [a.call.name for a in approvals.requests] == ["docs.compile_pdf"]
    assert not (home / "docs" / "d" / "doc.pdf").exists()


# -- WebChat ---------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_webchat_lists_the_agent_with_the_real_permission_of_each_tool(home):
    api = build_http_api(env_for(home), llm=ScriptedLLM([]))
    agent = next(a for a in await api.list_agents() if a["id"] == AGENT)
    assert agent["role"] == "Documentation specialist"
    permissions = {tool["name"]: tool["permission"] for tool in agent["tools"]}
    assert permissions["docs.compile_pdf"] == "APPROVAL REQUIRED"
    assert permissions["docs.read"] == "READ"
    assert permissions["docs.write"] == permissions["docs.patch"] == "WRITE"
    assert {skill["name"] for skill in agent["skills"]} == {
        "Technical Documentation",
        "Report Writing",
        "Editing Proofreading",
        "Latex Documents",
    }
    default = next(t for t in await api.list_teams() if t["id"] == "default")
    responsibilities = {m["agentId"]: m["responsibility"] for m in default["members"]}
    assert responsibilities[AGENT] == "Documentation specialist"
    await api.aclose()
