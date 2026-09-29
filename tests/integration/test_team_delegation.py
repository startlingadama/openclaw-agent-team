"""CEO -> specialist through the composition root, on the real project files.

One scripted LLM plays every agent in turn: the runtime is shared, so the script is the sequence
of decisions in the order the agents take them.
"""

import asyncio
import shutil
from pathlib import Path

import pytest

from openclaw.domain.agents.decision import CallTool, Finish
from openclaw.domain.agents.state import ObservationKind
from openclaw.domain.tasks.execution import EventType, ExecutionStatus
from openclaw.domain.tools.model import ToolCall
from openclaw.entrypoints.bootstrap import Settings, build_app
from tests.unit.fakes import ListSink, ScriptedLLM

PROJECT = Path(__file__).resolve().parents[2]


@pytest.fixture
def home(tmp_path):
    for name in ("agents", "skills", "teams"):
        shutil.copytree(PROJECT / name, tmp_path / name)
    (tmp_path / "workspace" / "shared").mkdir(parents=True)
    return tmp_path


def start(home, script, **env):
    llm, sink = ScriptedLLM(script), ListSink()
    app = build_app(
        {
            "OPENCLAW_HOME": str(home),
            "DEEPSEEK_API_KEY": "sk-test",
            "TAVILY_API_KEY": "t",  # google-research needs web.search to be served
            **env,
        },
        llm=llm,
        sink=sink,
    )

    async def go():
        try:
            return await app.run_agent("ceo", "Compare A and B")
        finally:
            await app.aclose()

    return asyncio.run(go()), llm, sink


def delegate(agent="google-research", **extra):
    arguments = {"agent": agent, "objective": "Compare A and B", **extra}
    return CallTool(ToolCall("team.delegate", arguments))


def test_settings_team_defaults_and_override(tmp_path):
    assert Settings.from_env({"OPENCLAW_HOME": str(tmp_path)}).team == "default"
    env = {"OPENCLAW_HOME": str(tmp_path), "OPENCLAW_TEAM": "research"}
    assert Settings.from_env(env).team == "research"
    assert Settings.from_env(env).teams_dir == tmp_path / "teams"


def test_team_tools_are_always_registered(home):
    app = build_app({"OPENCLAW_HOME": str(home), "DEEPSEEK_API_KEY": "sk-test"})
    assert {"team.members", "team.delegate"} <= set(app.tools.names)


def test_the_ceo_delegates_and_synthesizes(home):
    execution, llm, sink = start(
        home,
        [
            CallTool(ToolCall("team.members", {})),
            delegate(context="for a board memo"),
            Finish("member answer"),
            Finish("final synthesis"),
        ],
    )
    assert (execution.status, execution.answer) == (ExecutionStatus.COMPLETED, "final synthesis")

    # step 2 was the CEO reading the member list, step 4 was the member reasoning
    member_context = llm.contexts[2]
    assert member_context.agent_id == "google-research"
    request = member_context.observations[0]
    assert request.kind is ObservationKind.USER_REQUEST
    assert request.content.startswith("Compare A and B") and "for a board memo" in request.content

    # the member's answer came back to the CEO as a tool result (data), with its status
    ceo_after = llm.contexts[3]
    assert ceo_after.agent_id == "ceo"
    result = ceo_after.observations[-1]
    assert result.kind is ObservationKind.TOOL_RESULT and result.source == "team.delegate"
    assert '"answer": "member answer"' in result.content
    assert '"status": "completed"' in result.content

    # the trace shows who asked whom, under the same task id
    started = [e for e in sink.events if e.type is EventType.TASK_STARTED]
    assert [(e.agent_id, e.data["sender"]) for e in started] == [
        ("ceo", "user"),
        ("google-research", "ceo"),
    ]
    assert started[0].task_id != started[1].task_id
    assert started[1].task_id in result.content


def test_a_member_cannot_delegate_again(home):
    execution, _, sink = start(
        home,
        [
            delegate(),
            delegate("github"),  # google-research tries to pass the work on
            Finish("member answer"),
            Finish("final"),
        ],
    )
    assert execution.status is ExecutionStatus.COMPLETED
    denied = [e for e in sink.events if e.type is EventType.POLICY_DENIED]
    assert [(e.agent_id, e.data["tool"]) for e in denied] == [("google-research", "team.delegate")]


def test_the_team_limits_who_can_be_delegated_to(home):
    # in the `research` team, linkedin is not a member
    execution, llm, _ = start(
        home, [delegate("linkedin"), Finish("no linkedin here")], OPENCLAW_TEAM="research"
    )
    assert execution.answer == "no linkedin here"
    error = llm.contexts[1].observations[-1]
    assert error.kind is ObservationKind.TOOL_ERROR
    assert "not a member of team 'research'" in error.content
    assert "google-research, github" in error.content


def test_a_failing_member_is_reported_to_the_ceo(home):
    from openclaw.domain.shared.errors import LLMError

    execution, llm, _ = start(
        home,
        [delegate(), LLMError("provider down"), Finish("could not get it")],
    )
    assert execution.answer == "could not get it"
    result = llm.contexts[2].observations[-1]
    assert result.kind is ObservationKind.TOOL_RESULT
    assert '"status": "failed"' in result.content and "provider down" in result.content


def test_an_unknown_team_is_a_tool_error_for_the_ceo(home):
    execution, llm, _ = start(home, [delegate(), Finish("nothing")], OPENCLAW_TEAM="nope")
    assert execution.status is ExecutionStatus.COMPLETED
    assert "unknown team 'nope'" in llm.contexts[1].observations[-1].content


def test_a_member_whose_tools_are_missing_is_refused_before_any_llm_call(home):
    # no search key: google-research cannot run, and the CEO is told why
    llm, sink = ScriptedLLM([delegate(), Finish("reported")]), ListSink()
    app = build_app({"OPENCLAW_HOME": str(home), "DEEPSEEK_API_KEY": "sk-test"}, llm=llm, sink=sink)
    execution = asyncio.run(app.run_agent("ceo", "go"))
    asyncio.run(app.aclose())
    assert execution.answer == "reported"
    error = llm.contexts[1].observations[-1]
    assert error.kind is ObservationKind.TOOL_ERROR
    assert "needs tools that are not available: web.search" in error.content
    assert [c.agent_id for c in llm.contexts] == ["ceo", "ceo"]  # the member never reasoned


# -- running a team, and approvals asked by a specialist ---------------------------------------


def start_team(home, team, script, approvals=None, **env):
    llm, sink = ScriptedLLM(script), ListSink()
    app = build_app(
        {
            "OPENCLAW_HOME": str(home),
            "DEEPSEEK_API_KEY": "sk-test",
            "TAVILY_API_KEY": "t",
            "GITHUB_TOKEN": "ghp_test",
            **env,
        },
        llm=llm,
        sink=sink,
        approvals=approvals,
    )

    async def go():
        try:
            return await app.run_team(team, "Open an issue about the bug")
        finally:
            await app.aclose()

    return asyncio.run(go()), llm, sink


def test_run_team_starts_the_supervisor_within_that_team_without_the_env_variable(home):
    # OPENCLAW_TEAM is not set (default team): `research` still governs delegation
    execution, _, sink = start_team(
        home, "research", [delegate("linkedin"), Finish("linkedin is not in this team")]
    )
    assert execution.status is ExecutionStatus.COMPLETED
    started = [e for e in sink.events if e.type is EventType.TASK_STARTED]
    assert [e.agent_id for e in started] == ["ceo"]  # linkedin was refused: never started


def test_an_approval_asked_by_a_specialist_names_that_specialist(home):
    from tests.unit.fakes import FakeApprovals

    approvals = FakeApprovals(approved=False)
    create = CallTool(ToolCall("github.create_issue", {"repo": "o/r", "title": "Bug"}))
    execution, _, _ = start_team(
        home,
        "default",
        [delegate("github", objective="Open an issue"), create, Finish("rejected"), Finish("ok")],
        approvals=approvals,
    )
    assert execution.status is ExecutionStatus.COMPLETED
    assert [(a.agent_id, a.call.name) for a in approvals.requests] == [
        ("github", "github.create_issue")
    ]


# -- the persistent trace (ARCHITECTURE section 19) ---------------------------------------------


def run_traced(home, script, *, verbose=False, sink=None, **env):
    app = build_app(
        {
            "OPENCLAW_HOME": str(home),
            "DEEPSEEK_API_KEY": "sk-test",
            "TAVILY_API_KEY": "t",
            **env,
        },
        llm=ScriptedLLM(script),
        sink=sink,
        verbose=verbose,
    )

    async def go():
        try:
            return await app.run_agent("ceo", "Compare A and B")
        finally:
            await app.aclose()

    return asyncio.run(go())


def traces(home):
    import json

    found = {}
    for directory in sorted((home / "executions").iterdir()):
        summary = json.loads((directory / "execution.json").read_text())
        found[summary["agent_id"]] = (directory, summary)
    return found


def test_a_delegation_leaves_two_linked_traces(home):
    import json

    execution = run_traced(
        home,
        [
            CallTool(ToolCall("team.members", {})),
            delegate(context="for a board memo"),
            Finish("member answer"),
            Finish("final synthesis"),
        ],
    )
    assert execution.status is ExecutionStatus.COMPLETED
    found = traces(home)
    assert set(found) == {"ceo", "google-research"}
    (ceo_dir, ceo), (member_dir, member) = found["ceo"], found["google-research"]

    assert ceo["status"] == member["status"] == "completed"
    assert (
        ceo["parent_execution_id"] is None and member["parent_execution_id"] == ceo["execution_id"]
    )
    assert (ceo["sender"], member["sender"]) == ("user", "ceo")
    assert ceo["steps"] == 3 and member["steps"] == 1  # members, delegate, finish / finish

    (child,) = ceo["children"]
    assert child["execution_id"] == member["execution_id"] and child["trace"] == member_dir.name
    assert child["task_id"] == member["task_id"] != ceo["task_id"]
    assert "final synthesis" in (ceo_dir / "result.md").read_text()
    assert "member answer" in (member_dir / "result.md").read_text()

    # the supervisor's own trace shows the delegation and what came back, under the same task id
    events = [json.loads(x) for x in (ceo_dir / "events.jsonl").read_text().splitlines()]
    assert events[0]["type"] == "task_started" and events[-1]["type"] == "final_result"
    result = next(
        e for e in events if e["data"].get("tool") == "team.delegate" and "output" in e["data"]
    )
    assert member["task_id"] in result["data"]["output"]


def test_two_runs_do_not_share_a_directory(home):
    for _ in range(2):
        run_traced(home, [Finish("done")])
    assert len(list((home / "executions").iterdir())) == 2


def test_trace_off_writes_nothing(home):
    run_traced(home, [Finish("done")], OPENCLAW_TRACE="off")
    assert not (home / "executions").exists()


def test_verbose_prints_and_still_traces(home, capsys):
    run_traced(home, [Finish("done")], verbose=True)
    assert "[ceo] llm_decision" in capsys.readouterr().err
    assert "ceo" in traces(home)


def test_an_injected_sink_replaces_the_default_trace(home):
    sink = ListSink()
    run_traced(home, [Finish("done")], sink=sink)
    assert sink.events and not (home / "executions").exists()


def test_a_real_delegation_can_be_read_back_with_the_cli(home, monkeypatch, capsys):
    from openclaw.entrypoints.cli import main

    run_traced(
        home,
        [delegate(context="for a board memo"), Finish("member answer"), Finish("final synthesis")],
    )
    monkeypatch.setenv("OPENCLAW_HOME", str(home))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    assert main(["runs"]) == 0
    (line,) = capsys.readouterr().out.splitlines()
    assert " ceo " in line and "completed" in line and line.endswith("Compare A and B")

    assert main(["runs", "--all"]) == 0
    assert len(capsys.readouterr().out.splitlines()) == 2

    assert main(["show", line.split()[0]]) == 0
    text = capsys.readouterr().out
    assert text.startswith("user -> ceo: completed")
    assert "    ceo -> google-research: completed" in text
    assert "final synthesis" in text and "member answer" in text
    assert "for a board memo" in text  # what the CEO asked of the specialist
