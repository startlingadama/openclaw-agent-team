from openclaw.entrypoints.cli import main


def test_cli_runs():
    assert main([]) == 0


def test_dotenv_file_is_loaded_without_touching_environment(tmp_path, monkeypatch):
    import os

    example = tmp_path / ".env"
    example.write_text(
        "DEEPSEEK_API_KEY=sk-test\nOPENCLAW_WEB_HOST=0.0.0.0\nOPENCLAW_WEB_PORT=9000\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENCLAW_WEB_HOST", raising=False)
    monkeypatch.delenv("OPENCLAW_WEB_PORT", raising=False)
    monkeypatch.setattr(cli, "_start_web_server", lambda host, port: 0)
    main(["web"])
    assert os.environ["DEEPSEEK_API_KEY"] == "sk-test"
    assert os.environ["OPENCLAW_WEB_HOST"] == "0.0.0.0"
    assert os.environ["OPENCLAW_WEB_PORT"] == "9000"


def test_web_command_starts_the_http_server(monkeypatch):
    seen = {}

    def fake_start(host, port):
        seen["host"] = host
        seen["port"] = port
        return 0

    monkeypatch.setattr(cli, "_start_web_server", fake_start)
    monkeypatch.delenv("OPENCLAW_WEB_HOST", raising=False)
    monkeypatch.delenv("OPENCLAW_WEB_PORT", raising=False)
    assert main(["web"]) == 0
    assert seen == {"host": "127.0.0.1", "port": 8000}


def test_cli_loads_secrets_from_dotenv(tmp_path, monkeypatch):
    import os

    (tmp_path / ".env").write_text("OPENCLAW_TEST_SECRET=from-dotenv\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENCLAW_TEST_SECRET", raising=False)
    try:
        main([])
        assert os.environ["OPENCLAW_TEST_SECRET"] == "from-dotenv"
    finally:
        os.environ.pop("OPENCLAW_TEST_SECRET", None)


def test_real_environment_wins_over_dotenv(tmp_path, monkeypatch):
    import os

    (tmp_path / ".env").write_text("OPENCLAW_TEST_SECRET=from-dotenv\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENCLAW_TEST_SECRET", "from-env")
    main([])
    assert os.environ["OPENCLAW_TEST_SECRET"] == "from-env"


# -- `openclaw run` --------------------------------------------------------------------------
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

from openclaw.application.agents.run_agent import MissingToolsError  # noqa: E402
from openclaw.domain.shared.errors import (  # noqa: E402
    AgentError,
    AuthenticationError,
    TeamError,
)
from openclaw.entrypoints import cli  # noqa: E402


class FakeApp:
    def __init__(self, result=None, error=None, skipped=None):
        self.result, self.error = result, error
        self.skipped = skipped or {}
        self.calls, self.closed = [], False

    async def run_agent(self, agent, task):
        self.calls.append((agent, task))
        if self.error:
            raise self.error
        return self.result

    async def run_team(self, team, task):
        self.team_calls = [*getattr(self, "team_calls", []), (team, task)]
        if self.error:
            raise self.error
        return self.result

    async def aclose(self):
        self.closed = True


def install(monkeypatch, app=None, build_error=None):
    seen = {}

    def fake_build(*, verbose=False):
        seen["verbose"] = verbose
        if build_error:
            raise build_error
        return app

    monkeypatch.setattr(cli, "build_app", fake_build)
    return seen


def done(answer="the answer"):
    return SimpleNamespace(status="completed", answer=answer, error=None)


def test_run_prints_the_answer(monkeypatch, capsys):
    app = FakeApp(done())
    seen = install(monkeypatch, app)
    assert main(["run", "google-research", "find", "BRVM news"]) == 0
    assert capsys.readouterr().out == "the answer\n"
    assert app.calls == [("google-research", "find BRVM news")]  # words joined, quoted or not
    assert app.closed and seen["verbose"] is False


def test_verbose_flag_reaches_the_bootstrap(monkeypatch):
    seen = install(monkeypatch, FakeApp(done()))
    assert main(["run", "-v", "google-research", "x"]) == 0
    assert seen["verbose"] is True


def test_a_failed_run_exits_1_with_the_error_on_stderr(monkeypatch, capsys):
    failed = SimpleNamespace(status="max_steps_exceeded", answer=None, error="no answer in 25")
    app = FakeApp(failed)
    install(monkeypatch, app)
    assert main(["run", "google-research", "x"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "no answer in 25" in captured.err
    assert "max_steps_exceeded" in captured.err and app.closed


def test_a_partial_answer_is_printed_but_still_exits_1(monkeypatch, capsys):
    partial = SimpleNamespace(
        status="max_steps_exceeded", answer="what I found", error="step limit of 25 reached"
    )
    app = FakeApp(partial)
    install(monkeypatch, app)
    assert main(["run", "google-research", "x"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "what I found\n"
    assert "incomplete: step limit of 25 reached" in captured.err and app.closed


def test_a_configuration_error_exits_2_and_names_the_key(monkeypatch, capsys):
    install(monkeypatch, build_error=AuthenticationError("DEEPSEEK_API_KEY is not set"))
    assert main(["run", "google-research", "x"]) == 2
    assert "DEEPSEEK_API_KEY" in capsys.readouterr().err


def test_missing_tools_exit_2_with_only_the_relevant_hints(monkeypatch, capsys):
    app = FakeApp(
        error=MissingToolsError("x", ("web.search", "github.get_issue")),
        skipped={
            "web.search": "no key for the configured search providers",
            "github": "not configured (GITHUB_TOKEN is not set)",
            "linkedin": "not configured (LINKEDIN_ACCESS_TOKEN is not set)",
        },
    )
    install(monkeypatch, app)
    assert main(["run", "x", "task"]) == 2
    err = capsys.readouterr().err
    assert "web.search" in err and "search providers" in err and "GITHUB_TOKEN" in err
    assert "linkedin" not in err  # not what this agent needs
    assert app.closed


def test_another_agent_error_has_no_hints(monkeypatch, capsys):
    app = FakeApp(error=AgentError("unknown agent 'x'"), skipped={"github": "not configured"})
    install(monkeypatch, app)
    assert main(["run", "x", "task"]) == 2
    err = capsys.readouterr().err
    assert "unknown agent 'x'" in err and "github" not in err


def test_run_needs_an_agent_and_a_task():
    for argv in (["run"], ["run", "google-research"]):
        with pytest.raises(SystemExit) as exc:
            main(argv)
        assert exc.value.code == 2


# -- `openclaw team` -------------------------------------------------------------------------


def test_team_run_prints_the_answer(monkeypatch, capsys):
    app = FakeApp(done("team answer"))
    seen = install(monkeypatch, app)
    assert main(["team", "run", "-v", "executive", "prepare", "the board memo"]) == 0
    assert capsys.readouterr().out == "team answer\n"
    assert app.team_calls == [("executive", "prepare the board memo")]
    assert app.closed and seen["verbose"] is True


def test_team_run_errors_exit_2(monkeypatch, capsys):
    app = FakeApp(error=TeamError("unknown team 'nope' (available: default)"))
    install(monkeypatch, app)
    assert main(["team", "run", "nope", "x"]) == 2
    assert "unknown team 'nope'" in capsys.readouterr().err
    assert app.closed


def test_a_failed_team_run_exits_1(monkeypatch, capsys):
    failed = SimpleNamespace(status="max_steps_exceeded", answer=None, error="no answer")
    install(monkeypatch, FakeApp(failed))
    assert main(["team", "run", "default", "x"]) == 1
    assert "no answer" in capsys.readouterr().err


def test_team_run_needs_a_team_and_a_task():
    for argv in (["team", "run"], ["team", "run", "default"]):
        with pytest.raises(SystemExit) as exc:
            main(argv)
        assert exc.value.code == 2


def test_team_without_subcommand_prints_help(capsys):
    assert main(["team"]) == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_team_list_needs_no_llm_key(tmp_path, monkeypatch, capsys):
    teams = tmp_path / "teams"
    (teams / "alpha").mkdir(parents=True)
    (teams / "alpha" / "team.yaml").write_text(
        "team:\n  id: alpha\n  pattern: supervisor\n  supervisor: ceo\n  members: [a, b]\n"
    )
    (teams / "broken").mkdir()
    (teams / "broken" / "team.yaml").write_text("team:\n  id: other\n")
    monkeypatch.setenv("OPENCLAW_HOME", str(tmp_path))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert main(["team", "list"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "alpha  supervisor  supervisor=ceo  members: a, b"
    assert out[1].startswith("broken  invalid:")  # one broken file does not hide the others


def test_team_list_with_no_teams(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OPENCLAW_HOME", str(tmp_path))
    assert main(["team", "list"]) == 0
    assert "no team defined" in capsys.readouterr().out
