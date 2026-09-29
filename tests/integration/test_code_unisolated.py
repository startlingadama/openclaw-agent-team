"""The explicit opt-in without network isolation (Windows), tested where it can run everywhere.

Isolation is made unavailable by replacing the check, so these tests use a real subprocess on any
platform. What is not isolated stays visible: no test here claims a network or file barrier.
"""

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from openclaw.domain.shared.errors import ValidationError
from openclaw.domain.tools.model import ToolCall
from openclaw.entrypoints.bootstrap import build_app
from openclaw.infrastructure.tools.code import (
    CodeConfig,
    CodeToolProvider,
    SandboxLimits,
    SubprocessSandbox,
)
from openclaw.infrastructure.tools.code import sandbox as sandbox_module
from tests.unit.fakes import ScriptedLLM

PROJECT = Path(__file__).resolve().parents[2]
AGENT = "code-executor"


def no_isolation(monkeypatch):
    def refuse():
        raise ValidationError("sandbox needs Linux with `unshare` (network isolation)")

    monkeypatch.setattr(SubprocessSandbox, "_check_isolation", staticmethod(refuse))


def provider(tmp_path, **limits):
    config = CodeConfig(tmp_path, SandboxLimits(**{"timeout_s": 5, **limits}), True)
    return CodeToolProvider(config)


def run(p, name, **arguments):
    return asyncio.run(p.execute(ToolCall(name, arguments), AGENT))


# -- the default stays closed ------------------------------------------------------------------
def test_without_the_opt_in_the_sandbox_still_refuses(monkeypatch):
    no_isolation(monkeypatch)
    with pytest.raises(ValidationError, match="unshare"):
        SubprocessSandbox()
    with pytest.raises(ValidationError, match="unshare"):
        CodeToolProvider(CodeConfig(Path("."), SandboxLimits()))


def test_the_opt_in_is_read_from_the_environment(tmp_path):
    base = {"OPENCLAW_CODE_SANDBOX": "subprocess"}
    assert not CodeConfig.from_env(base, tmp_path).allow_unisolated
    for value in ("true", "1", "YES", " on "):
        env = {**base, "OPENCLAW_CODE_ALLOW_UNISOLATED": value}
        assert CodeConfig.from_env(env, tmp_path).allow_unisolated, value
    env = {**base, "OPENCLAW_CODE_ALLOW_UNISOLATED": "false"}
    assert not CodeConfig.from_env(env, tmp_path).allow_unisolated


def test_where_isolation_exists_the_opt_in_changes_nothing(monkeypatch):
    monkeypatch.setattr(SubprocessSandbox, "_check_isolation", staticmethod(lambda: None))
    assert SubprocessSandbox(allow_unisolated=True).isolated is True


def test_the_opt_in_warns_loudly(monkeypatch, caplog):
    no_isolation(monkeypatch)
    with caplog.at_level("WARNING"):
        sandbox = SubprocessSandbox(allow_unisolated=True)
    assert sandbox.isolated is False
    assert "WITHOUT network isolation" in caplog.text


# -- what still applies without isolation ------------------------------------------------------
def test_a_script_runs_in_the_agents_directory(monkeypatch, tmp_path):
    no_isolation(monkeypatch)
    out = run(provider(tmp_path), "code.execute", script="import os; print(6 * 7, os.getcwd())")
    assert out["exit_code"] == 0
    assert out["stdout"].startswith("42 ")
    assert Path(out["stdout"].split(" ", 1)[1].strip()).resolve() == (tmp_path / AGENT).resolve()


def test_a_script_sees_no_secret_of_the_parent(monkeypatch, tmp_path):
    no_isolation(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-secret")
    script = "import os; print(sorted(k for k in os.environ if 'KEY' in k or 'TOKEN' in k))"
    out = run(provider(tmp_path), "code.execute", script=script)
    assert out["stdout"].strip() == "[]" and "sk-secret" not in out["stdout"]


def test_a_script_that_never_ends_is_stopped(monkeypatch, tmp_path):
    no_isolation(monkeypatch)
    out = run(provider(tmp_path, timeout_s=1), "code.execute", script="while True: pass")
    assert out["timed_out"] is True and out["exit_code"] is None


def test_the_output_is_capped(monkeypatch, tmp_path):
    no_isolation(monkeypatch)
    p = provider(tmp_path, max_output_bytes=1000)
    out = run(p, "code.execute", script="print('x' * 100000)")
    assert out["output_truncated"] is True and len(out["stdout"]) <= 1000


def test_a_script_still_calls_tools_through_the_broker(monkeypatch, tmp_path):
    no_isolation(monkeypatch)

    class Broker:
        async def call(self, call, caller):
            return {"echo": dict(call.arguments)}

    p = provider(tmp_path)
    p.attach(Broker())
    out = run(p, "code.execute", script="print(tools.call('data.read', n=3)['echo']['n'])")
    assert out["stdout"] == "3\n"


# -- the Windows branch (helpers), checked from any platform -----------------------------------
def test_the_windows_environment_is_scrubbed(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox_module, "_is_windows", lambda: True)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-secret")
    env = sandbox_module._child_env(tmp_path)
    assert "sk-secret" not in "".join(env.values())
    assert {"SystemRoot", "ComSpec", "PATH", "TEMP"} <= set(env)
    assert env["USERPROFILE"] == str(tmp_path)


def test_the_windows_shell_and_process_options(monkeypatch):
    monkeypatch.setattr(sandbox_module, "_is_windows", lambda: True)
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False)
    assert SubprocessSandbox.shell_argv("dir") == ["cmd.exe", "/c", "dir"]
    options = sandbox_module._process_options(SandboxLimits())
    assert options == {"creationflags": 512}  # no preexec_fn, no start_new_session on Windows


def test_the_windows_kill_stops_the_whole_tree(monkeypatch):
    monkeypatch.setattr(sandbox_module, "_is_windows", lambda: True)
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: calls.append(argv))

    class Process:
        pid = 4321

        def kill(self):
            calls.append("kill")

    sandbox_module._kill(Process())
    assert calls == [["taskkill", "/F", "/T", "/PID", "4321"], "kill"]


# -- through the composition root --------------------------------------------------------------
def env_for(tmp_path, **extra):
    for name in ("agents", "skills", "teams"):
        shutil.copytree(PROJECT / name, tmp_path / name, dirs_exist_ok=True)
    (tmp_path / "workspace" / "shared").mkdir(parents=True, exist_ok=True)
    return {
        "OPENCLAW_HOME": str(tmp_path),
        "DEEPSEEK_API_KEY": "sk-test",
        "OPENCLAW_CODE_SANDBOX": "subprocess",
        **extra,
    }


def test_the_tools_are_offered_only_with_the_opt_in(monkeypatch, tmp_path):
    no_isolation(monkeypatch)
    closed = build_app(env_for(tmp_path), llm=ScriptedLLM([]))
    assert "code" in closed.skipped and "code.execute" not in closed.tools.names
    asyncio.run(closed.aclose())

    opened = build_app(
        env_for(tmp_path, OPENCLAW_CODE_ALLOW_UNISOLATED="true"), llm=ScriptedLLM([])
    )
    assert "code" not in opened.skipped
    assert {"code.execute", "code.terminal", "code.read_file"} <= set(opened.tools.names)
    asyncio.run(opened.aclose())
