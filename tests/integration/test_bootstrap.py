"""The composition root, on the real project files (agents/, skills/) copied to a temp home."""

import asyncio
import shutil
from pathlib import Path

import pytest

from openclaw.application.agents.run_agent import MissingToolsError
from openclaw.domain.agents.decision import CallTool, Finish, UseSkill
from openclaw.domain.shared.errors import AgentError, AuthenticationError, ValidationError
from openclaw.domain.tasks.execution import EventType, ExecutionStatus
from openclaw.domain.tools.model import ToolCall
from openclaw.entrypoints.bootstrap import DEFAULT_MAX_STEPS, Settings, build_app
from tests.unit.fakes import ScriptedLLM

PROJECT = Path(__file__).resolve().parents[2]


@pytest.fixture
def home(tmp_path):
    shutil.copytree(PROJECT / "agents", tmp_path / "agents")
    shutil.copytree(PROJECT / "skills", tmp_path / "skills")
    (tmp_path / "workspace" / "shared").mkdir(parents=True)
    return tmp_path


def env_for(home, **extra):
    return {"OPENCLAW_HOME": str(home), "DEEPSEEK_API_KEY": "sk-test", **extra}


def run(app, agent="google-research", task="find things"):
    async def go():
        try:
            return await app.run_agent(agent, task)
        finally:
            await app.aclose()

    return asyncio.run(go())


# -- settings -------------------------------------------------------------------------------
def test_settings_defaults_and_overrides(tmp_path):
    s = Settings.from_env({"OPENCLAW_HOME": str(tmp_path)})
    assert (s.agents_dir, s.skills_dir) == (tmp_path / "agents", tmp_path / "skills")
    assert s.workspace == tmp_path / "workspace"
    assert s.max_steps == DEFAULT_MAX_STEPS
    s = Settings.from_env(
        {"OPENCLAW_HOME": str(tmp_path), "OPENCLAW_WORKSPACE": "ws", "OPENCLAW_MAX_STEPS": "7"}
    )
    assert (s.workspace, s.max_steps) == (tmp_path / "ws", 7)


@pytest.mark.parametrize("bad", ["0", "-1", "many"])
def test_invalid_max_steps_is_refused(tmp_path, bad):
    with pytest.raises(ValidationError, match="OPENCLAW_MAX_STEPS"):
        Settings.from_env({"OPENCLAW_HOME": str(tmp_path), "OPENCLAW_MAX_STEPS": bad})


# -- LLM ------------------------------------------------------------------------------------
def test_missing_deepseek_key_stops_the_build(home):
    with pytest.raises(AuthenticationError, match="DEEPSEEK_API_KEY"):
        build_app({"OPENCLAW_HOME": str(home)})


def test_unsupported_llm_provider_is_refused(home):
    with pytest.raises(ValidationError, match="LLM_PROVIDER"):
        build_app(env_for(home, LLM_PROVIDER="mystery"))


def test_deepseek_reasoner_is_refused(home):
    with pytest.raises(ValidationError, match="tool calling"):
        build_app(env_for(home, DEEPSEEK_MODEL="deepseek-reasoner"))


# -- providers ------------------------------------------------------------------------------
def test_only_providers_with_credentials_are_registered(home):
    app = build_app(env_for(home, TAVILY_API_KEY="t", GITHUB_TOKEN="g"))
    names = set(app.tools.names)
    assert {"memory.update", "web.search", "web.open", "web.extract"} <= names
    assert "github.search_repository" in names
    assert not any(n.startswith(("google.", "linkedin.")) for n in names)
    assert set(app.skipped) - {"docs.compile_pdf"} == {"google", "linkedin", "code"}
    assert "GOOGLE_TOKEN_FILE" in app.skipped["google"]
    asyncio.run(app.aclose())


def test_all_providers_when_all_credentials_are_present(home):
    token = home / "token.json"
    token.write_text('{"refresh_token":"r","client_id":"c","client_secret":"s"}')
    app = build_app(
        env_for(
            home,
            BRAVE_SEARCH_API_KEY="b",
            GITHUB_TOKEN="g",
            GOOGLE_TOKEN_FILE=str(token),
            LINKEDIN_ACCESS_TOKEN="l",
        )
    )
    names = set(app.tools.names)
    for prefix in ("github.", "google.", "linkedin.", "web.", "memory."):
        assert any(n.startswith(prefix) for n in names), prefix
    assert set(app.skipped) - {"docs.compile_pdf"} == {"code"}  # sandbox not enabled
    asyncio.run(app.aclose())


def test_web_search_is_reported_when_no_search_key_is_set(home):
    app = build_app(env_for(home))
    assert "web.search" not in app.tools.names
    assert {"web.open", "web.extract"} <= set(app.tools.names)  # they need no key
    assert "TAVILY_API_KEY" in app.skipped["web.search"]
    asyncio.run(app.aclose())


def test_web_search_needs_a_key_for_a_configured_provider(home):
    # Only brave is allowed, but only a tavily key exists: nothing can serve web.search.
    app = build_app(env_for(home, WEB_SEARCH_PROVIDERS="brave", TAVILY_API_KEY="t"))
    assert "web.search" not in app.tools.names
    asyncio.run(app.aclose())


def test_a_configured_but_invalid_provider_is_skipped_not_fatal(home):
    app = build_app(
        env_for(
            home,
            LINKEDIN_ACCESS_TOKEN="l",
            LINKEDIN_API_VERSION="bad",
            GOOGLE_TOKEN_FILE=str(home / "missing.json"),
        )
    )
    assert app.skipped["linkedin"].startswith("invalid configuration")
    assert app.skipped["google"].startswith("invalid configuration")
    asyncio.run(app.aclose())


def test_a_bad_search_provider_name_disables_web_and_is_reported(home):
    app = build_app(env_for(home, WEB_SEARCH_PROVIDERS="altavista"))
    assert not any(n.startswith("web.") for n in app.tools.names)
    assert "altavista" in app.skipped["web"]
    asyncio.run(app.aclose())


# -- running --------------------------------------------------------------------------------
def test_agent_missing_a_tool_fails_before_the_first_llm_call(home):
    llm = ScriptedLLM([])
    app = build_app(env_for(home), llm=llm)  # no search key: web.search is not served
    with pytest.raises(MissingToolsError, match="web.search") as exc:
        run(app)
    assert exc.value.missing == ("web.search",)
    assert llm.contexts == []


def test_unknown_agent_is_an_explicit_error(home):
    app = build_app(env_for(home, TAVILY_API_KEY="t"), llm=ScriptedLLM([]))
    with pytest.raises(AgentError, match=r"unknown agent 'nobody'.*google-research"):
        run(app, agent="nobody")


def test_empty_task_is_refused(home):
    app = build_app(env_for(home, TAVILY_API_KEY="t"), llm=ScriptedLLM([]))
    with pytest.raises(AgentError, match="task"):
        run(app, task="   ")


def test_google_research_runs_end_to_end_on_real_adapters(home):
    llm = ScriptedLLM(
        [
            UseSkill("research/web-research"),
            CallTool(
                ToolCall(
                    "memory.update",
                    {"section": "Important Facts", "content": "The user studies BRVM."},
                )
            ),
            Finish("done"),
        ]
    )
    app = build_app(env_for(home, TAVILY_API_KEY="t"), llm=llm)
    result = run(app, task="research BRVM")

    assert (result.status, result.answer, result.steps) == (ExecutionStatus.COMPLETED, "done", 3)
    kinds = [e.type for e in result.events]
    assert EventType.SKILL_LOADED in kinds and EventType.TOOL_RESULT in kinds

    first = llm.contexts[0]
    assert "Google Research Agent" in first.profile.soul
    assert [s.id for s in first.available_skills] == [
        "research/web-research",
        "research/source-evaluation",
    ]
    assert {t.name for t in first.tools} == {
        "memory.update",
        "web.search",
        "web.open",
        "web.extract",
    }
    assert first.observations[0].content == "research BRVM"
    assert [s.metadata.id for s in llm.contexts[1].loaded_skills] == ["research/web-research"]

    # The real Markdown memory was written, with its history (ADR-005, ADR-021).
    memory = (home / "agents" / "google-research" / "MEMORY.md").read_text(encoding="utf-8")
    assert "The user studies BRVM." in memory
    assert (home / "agents" / "google-research" / "history" / "MEMORY.jsonl").is_file()


def test_the_step_guard_is_on_by_default(home):
    llm = ScriptedLLM([UseSkill("research/web-research")], repeat_last=True)
    app = build_app(env_for(home, TAVILY_API_KEY="t", OPENCLAW_MAX_STEPS="3"), llm=llm)
    result = run(app)
    assert (result.status, result.steps) == (ExecutionStatus.MAX_STEPS_EXCEEDED, 3)


def test_verbose_traces_the_steps_on_stderr(home, capsys):
    llm = ScriptedLLM([UseSkill("research/web-research"), Finish("ok")])
    app = build_app(env_for(home, TAVILY_API_KEY="t"), verbose=True, llm=llm)
    run(app)
    err = capsys.readouterr().err
    assert "skill_loaded skill=research/web-research" in err
    assert "llm_decision decision=finish" in err


def test_quiet_by_default(home, capsys):
    app = build_app(env_for(home, TAVILY_API_KEY="t"), llm=ScriptedLLM([Finish("ok")]))
    run(app)
    assert capsys.readouterr().err == ""
