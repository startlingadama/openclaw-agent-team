"""The code tools on the real sandbox (a real subprocess, files in a temporary directory)."""

import asyncio
import json
import time

import pytest

from openclaw.domain.shared.errors import ToolError, ValidationError
from openclaw.domain.tools.model import ToolCall
from openclaw.infrastructure.tools.code import (
    CodeConfig,
    CodeToolProvider,
    SandboxLimits,
    SubprocessSandbox,
)

try:
    SubprocessSandbox()
except ValidationError as exc:  # no `unshare --net` here: the tools are not offered either
    pytest.skip(f"sandbox not available: {exc}", allow_module_level=True)

AGENT = "code-executor"


class Broker:
    def __init__(self, delay=0.0):
        self.calls, self.delay = [], delay

    async def call(self, call, caller):
        self.calls.append((call.name, dict(call.arguments), caller))
        await asyncio.sleep(self.delay)
        if call.name == "data.fail":
            raise ToolError("backend down")
        return {"echo": dict(call.arguments)}


def provider(tmp_path, broker=None, **limits):
    config = CodeConfig(tmp_path, SandboxLimits(**{"timeout_s": 5, **limits}))
    p = CodeToolProvider(config)
    if broker is not None:
        p.attach(broker)
    return p


def run(p, name, **arguments):
    return asyncio.run(p.execute(ToolCall(name, arguments), AGENT))


# -- configuration ---------------------------------------------------------------------------
def test_the_mechanism_must_be_named(tmp_path):
    with pytest.raises(ValidationError, match="OPENCLAW_CODE_SANDBOX"):
        CodeConfig.from_env({"OPENCLAW_CODE_SANDBOX": "docker"}, tmp_path)
    config = CodeConfig.from_env(
        {"OPENCLAW_CODE_SANDBOX": "subprocess", "OPENCLAW_CODE_TIMEOUT": "3"}, tmp_path
    )
    assert config.limits.timeout_s == 3
    with pytest.raises(ValidationError, match="OPENCLAW_CODE_MEMORY_MB"):
        CodeConfig.from_env(
            {"OPENCLAW_CODE_SANDBOX": "subprocess", "OPENCLAW_CODE_MEMORY_MB": "-1"}, tmp_path
        )


def test_every_declared_tool_has_a_matching_spec(tmp_path):
    p = provider(tmp_path)
    assert p.tool_names == {
        "code.execute",
        "code.terminal",
        "code.read_file",
        "code.write_file",
        "code.patch_file",
    }
    assert all(p.get_spec(n).name == n for n in p.tool_names)


# -- confinement and limits ------------------------------------------------------------------
def test_a_script_has_no_network(tmp_path):
    script = (
        "import socket\n"
        "try:\n socket.create_connection(('1.1.1.1', 53), 2)\n print('reached')\n"
        "except OSError:\n print('no network')\n"
    )
    assert run(provider(tmp_path), "code.execute", script=script)["stdout"] == "no network\n"


def test_a_script_sees_no_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-secret")
    out = run(provider(tmp_path), "code.execute", script="import os; print(sorted(os.environ))")
    assert "sk-secret" not in json.dumps(out) and "ghp-secret" not in json.dumps(out)
    assert "DEEPSEEK_API_KEY" not in out["stdout"] and "GITHUB_TOKEN" not in out["stdout"]
    terminal = run(provider(tmp_path), "code.terminal", command="env")
    assert "sk-secret" not in terminal["stdout"] and "ghp-secret" not in terminal["stdout"]


def test_a_script_that_never_ends_is_stopped(tmp_path):
    started = time.monotonic()
    out = run(provider(tmp_path, timeout_s=1), "code.execute", script="while True: pass")
    assert out["timed_out"] is True and out["exit_code"] is None
    assert time.monotonic() - started < 5


def test_the_output_is_capped(tmp_path):
    out = run(provider(tmp_path, max_output_bytes=1000), "code.execute", script="print('x'*50000)")
    assert out["output_truncated"] is True and len(out["stdout"]) <= 1000


def test_memory_is_limited(tmp_path):
    out = run(provider(tmp_path, memory_mb=64), "code.execute", script="b = bytearray(400*2**20)")
    assert out["exit_code"] != 0 and "MemoryError" in out["stderr"]


def test_the_terminal_runs_in_the_agents_own_directory(tmp_path):
    out = run(provider(tmp_path), "code.terminal", command="pwd; echo hi > note.txt")
    assert out["stdout"].splitlines()[0] == str((tmp_path / AGENT).resolve())
    assert (tmp_path / AGENT / "note.txt").read_text().strip() == "hi"


def test_each_agent_has_its_own_directory(tmp_path):
    p = provider(tmp_path)
    asyncio.run(p.execute(ToolCall("code.write_file", {"path": "a.txt", "content": "1"}), "one"))
    with pytest.raises(ToolError, match="not found"):
        asyncio.run(p.execute(ToolCall("code.read_file", {"path": "a.txt"}), "two"))


def test_an_invalid_agent_id_is_refused(tmp_path):
    with pytest.raises(ValidationError):
        asyncio.run(
            provider(tmp_path).execute(ToolCall("code.read_file", {"path": "a"}), "../evil")
        )


# -- files -----------------------------------------------------------------------------------
def test_write_read_and_patch(tmp_path):
    p = provider(tmp_path)
    run(p, "code.write_file", path="src/a.py", content="x = 1\ny = 2\n")
    assert run(p, "code.read_file", path="src/a.py") == "x = 1\ny = 2\n"
    run(p, "code.patch_file", path="src/a.py", old="y = 2", new="y = 3")
    assert run(p, "code.read_file", path="src/a.py") == "x = 1\ny = 3\n"


def test_patch_needs_exactly_one_match(tmp_path):
    p = provider(tmp_path)
    run(p, "code.write_file", path="a.txt", content="a a")
    with pytest.raises(ToolError, match="found 2"):
        run(p, "code.patch_file", path="a.txt", old="a", new="b")
    with pytest.raises(ToolError, match="found 0"):
        run(p, "code.patch_file", path="a.txt", old="zzz", new="b")


@pytest.mark.parametrize("path", ["../x", "/etc/passwd", "a/../../x"])
def test_paths_cannot_leave_the_sandbox_directory(tmp_path, path):
    p = provider(tmp_path)
    with pytest.raises(ValidationError):
        run(p, "code.read_file", path=path)
    with pytest.raises(ValidationError):
        run(p, "code.write_file", path=path, content="x")


def test_a_symlink_cannot_lead_out(tmp_path):
    p = provider(tmp_path)
    run(p, "code.terminal", command="ln -s /etc/passwd link")
    with pytest.raises(ValidationError, match="leaves"):
        run(p, "code.read_file", path="link")


# -- calling tools from a script -------------------------------------------------------------
def test_a_script_calls_tools_through_the_broker(tmp_path):
    broker = Broker()
    script = (
        "r = tools.call('data.read', table='t', limit=3)\n"
        "print('got', r['echo']['table'])\n"
        "print('again', tools.call('data.read', table='u')['echo']['table'])\n"
    )
    out = run(provider(tmp_path, broker), "code.execute", script=script)
    assert out["exit_code"] == 0 and out["stdout"] == "got t\nagain u\n"
    assert broker.calls == [
        ("data.read", {"table": "t", "limit": 3}, AGENT),
        ("data.read", {"table": "u"}, AGENT),
    ]


def test_a_tool_error_reaches_the_script_as_an_exception(tmp_path):
    script = (
        "try:\n tools.call('data.fail')\nexcept ToolCallError as e:\n print('caught:', e)\n"
    )
    out = run(provider(tmp_path, Broker()), "code.execute", script=script)
    assert out["stdout"] == "caught: backend down\n"


def test_output_around_a_call_keeps_its_order(tmp_path):
    script = "print('a', end='')\ntools.call('data.read')\nprint('b')\nprint('c')\n"
    out = run(provider(tmp_path, Broker()), "code.execute", script=script)
    assert out["stdout"] == "ab\nc\n"


def test_a_script_cannot_start_a_script(tmp_path):
    broker = Broker()
    script = (
        "try:\n tools.call('code.execute', script='1')\n"
        "except ToolCallError as e:\n print(e)\n"
    )
    out = run(provider(tmp_path, broker), "code.execute", script=script)
    assert "cannot be called from a script" in out["stdout"]
    assert broker.calls == []


def test_without_a_broker_scripts_have_no_tools(tmp_path):
    script = "try:\n tools.call('data.read')\nexcept ToolCallError as e:\n print(e)\n"
    out = run(provider(tmp_path), "code.execute", script=script)
    assert "not available" in out["stdout"]


def test_time_spent_serving_a_call_does_not_count_against_the_timeout(tmp_path):
    # e.g. a human takes their time to approve
    out = run(
        provider(tmp_path, Broker(delay=2.0), timeout_s=1),
        "code.execute",
        script="tools.call('data.read')\nprint('done')",
    )
    assert out["timed_out"] is False and out["stdout"] == "done\n"


def test_a_script_that_hangs_after_a_call_is_still_stopped(tmp_path):
    out = run(
        provider(tmp_path, Broker(), timeout_s=1),
        "code.execute",
        script="tools.call('data.read')\nwhile True: pass",
    )
    assert out["timed_out"] is True


def test_a_malformed_request_gets_an_error_not_a_crash(tmp_path):
    script = (
        "import sys\n"
        "sys.stdout.write('\\x00OPENCLAW-CALL not json\\n'); sys.stdout.flush()\n"
        "print(sys.stdin.readline().strip())\n"
    )
    out = run(provider(tmp_path, Broker()), "code.execute", script=script)
    assert "malformed" in out["stdout"]
