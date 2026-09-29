"""Sandbox: a hardened subprocess with limits (ADR-025).

Mechanism: the command runs in a child process started through `unshare --net`, so it has no
network at all (not even loopback to the host), with CPU-time, memory (address space), file-size
and open-files limits, a wall-clock timeout, a capped output, a scrubbed environment (no
provider credentials, REQUIREMENTS section 21) and its own working directory.

Fail closed: when network isolation is not available on this machine the sandbox refuses to be
built, so the provider is reported in `App.skipped` and nothing ever runs unisolated.

Explicit opt-in (`allow_unisolated`, `OPENCLAW_CODE_ALLOW_UNISOLATED`): where isolation is not
available (Windows), the child still runs with a wall-clock timeout, a capped output, a scrubbed
environment and its own working directory, but WITHOUT network isolation and, where the `resource`
module is missing, without CPU, memory and file-size limits. Such code can read any file the
user can read and use the network: it is for a local machine, with every run approved by a human.
Where isolation is available it is always used, the opt-in changes nothing.

stdin/stdout also carry the channel between a script and its caller (ADR-025), see
`run_channel`: a line of the script's stdout that holds `CALL_MARKER` is a request, the answer is
one line written on the script's stdin. Any other output is the script's own output. The time a
request takes to be served (a tool, a human approval) does not count against the timeout.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from openclaw.domain.shared.errors import ToolError, ValidationError

log = logging.getLogger(__name__)

_ISOLATION = ("unshare", "--net", "--map-root-user", "--")
_SAFE_PATH = "/usr/local/bin:/usr/bin:/bin"
CALL_MARKER = "\x00OPENCLAW-CALL "
CHANNEL_LINE_LIMIT = 2_000_000  # longest request line accepted from a script

CallHandler = Callable[[str], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class SandboxLimits:
    timeout_s: float = 10.0
    cpu_s: int = 10
    memory_mb: int = 256
    max_output_bytes: int = 64_000
    max_file_bytes: int = 10_000_000
    max_open_files: int = 64

    def __post_init__(self) -> None:
        for name in ("timeout_s", "cpu_s", "memory_mb", "max_output_bytes", "max_file_bytes"):
            if getattr(self, name) <= 0:
                raise ValidationError(f"sandbox limit '{name}' must be > 0")


@dataclass(frozen=True, slots=True)
class SandboxResult:
    exit_code: int | None  # None: killed (timeout or output cap)
    stdout: str
    stderr: str
    timed_out: bool = False
    output_truncated: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
            "output_truncated": self.output_truncated,
        }


class SubprocessSandbox:
    def __init__(self, limits: SandboxLimits | None = None, allow_unisolated: bool = False) -> None:
        self.limits = limits or SandboxLimits()
        self.isolated = True
        try:
            self._check_isolation()
        except ValidationError as exc:
            if not allow_unisolated:
                raise
            self.isolated = False
            log.warning(
                "code sandbox runs WITHOUT network isolation (%s): executed code can read your "
                "files and use the network; read each script before approving it",
                exc,
            )

    @staticmethod
    def _check_isolation() -> None:
        if sys.platform != "linux" or shutil.which("unshare") is None:
            raise ValidationError("sandbox needs Linux with `unshare` (network isolation)")
        probe = subprocess.run(  # noqa: S603 - fixed argv
            [*_ISOLATION, "true"], capture_output=True, timeout=10, check=False
        )
        if probe.returncode != 0:
            raise ValidationError("network isolation is not permitted here (`unshare --net`)")

    @staticmethod
    def python_argv(script: str) -> list[str]:
        """Isolated interpreter (`-I`: no user site, no PYTHON* variables)."""
        return [sys.executable, "-I", "-c", script]

    @staticmethod
    def shell_argv(command: str) -> list[str]:
        if _is_windows():
            return ["cmd.exe", "/c", command]
        return ["/bin/sh", "-c", command]

    async def _spawn(
        self,
        argv: list[str],
        cwd: Path,
        limit: int = 2**16,
        env: Mapping[str, str] | None = None,
    ) -> asyncio.subprocess.Process:
        try:
            return await asyncio.create_subprocess_exec(
                *(_ISOLATION if self.isolated else ()),
                *argv,
                cwd=cwd,
                env={**_child_env(cwd), **(env or {})},
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=limit,
                **_process_options(self.limits),
            )
        except OSError as exc:
            raise ToolError(f"sandbox could not start: {type(exc).__name__}") from exc

    async def run(
        self, argv: list[str], cwd: Path, *, env: Mapping[str, str] | None = None
    ) -> SandboxResult:
        """Run a command to the end (or to a limit). Nothing is ever written on its stdin.

        `env` adds variables to the scrubbed environment of the child (the caller chooses them:
        never a credential)."""
        process = await self._spawn(argv, cwd, env=env)
        out, err, state = bytearray(), bytearray(), _State()
        budget = self.limits.max_output_bytes
        assert process.stdin is not None
        process.stdin.close()
        timed_out = False
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    _pump(process.stdout, out, budget, process, state),
                    _pump(process.stderr, err, budget, process, state),
                ),
                timeout=self.limits.timeout_s,
            )
            await asyncio.wait_for(process.wait(), timeout=2)
        except TimeoutError:
            timed_out = True
            _kill(process)
            await _reap(process)
        return _result(process, out, err, timed_out, state)

    async def run_channel(self, argv: list[str], cwd: Path, on_call: CallHandler) -> SandboxResult:
        """Run a script that may send requests to `on_call` (see the module docstring)."""
        process = await self._spawn(argv, cwd, CHANNEL_LINE_LIMIT)
        out, err, state = bytearray(), bytearray(), _State()
        budget = self.limits.max_output_bytes
        assert process.stdout is not None and process.stdin is not None
        err_task = asyncio.create_task(_pump(process.stderr, err, budget, process, state))
        loop = asyncio.get_running_loop()
        used, timed_out = 0.0, False
        try:
            while True:
                remaining = self.limits.timeout_s - used
                if remaining <= 0:
                    timed_out = True
                    break
                started = loop.time()
                try:
                    raw = await asyncio.wait_for(process.stdout.readline(), remaining)
                except TimeoutError:
                    timed_out = True
                    break
                except ValueError:  # a line longer than the channel accepts
                    state.truncated = True
                    break
                used += loop.time() - started  # serving a request below is not counted
                if not raw:
                    break
                head, marker, payload = raw.decode("utf-8", "replace").partition(CALL_MARKER)
                if head:
                    data = head.encode("utf-8")
                    room = budget - len(out)
                    out += data[:room]
                    if len(data) > room:  # more output than allowed: stop the script
                        state.truncated = True
                        break
                if marker:
                    reply = await _answer(on_call, payload.strip())
                    try:
                        process.stdin.write(reply.encode("utf-8") + b"\n")
                        await process.stdin.drain()
                    except (BrokenPipeError, ConnectionResetError):
                        break
        finally:
            if timed_out or state.truncated:
                _kill(process)
            with contextlib.suppress(BrokenPipeError, ConnectionResetError, OSError):
                process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except TimeoutError:
                timed_out = True
                _kill(process)
                await _reap(process)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(err_task, timeout=2)
            err_task.cancel()
        return _result(process, out, err, timed_out, state)


@dataclass
class _State:
    truncated: bool = False


async def _pump(
    stream: asyncio.StreamReader | None,
    buffer: bytearray,
    budget: int,
    process: asyncio.subprocess.Process,
    state: _State,
) -> None:
    """Copies a stream into `buffer`; beyond `budget` the process is stopped."""
    while stream is not None and (chunk := await stream.read(4096)):
        room = budget - len(buffer)
        buffer += chunk[:room]
        if len(chunk) > room:
            state.truncated = True
            _kill(process)
            return


async def _answer(on_call: CallHandler, payload: str) -> str:
    try:
        return await on_call(payload)
    except Exception as exc:  # the channel never breaks the sandbox: the script gets an error
        return json.dumps({"ok": False, "error": f"tool channel failed: {type(exc).__name__}"})


async def _reap(process: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(Exception):
        await asyncio.wait_for(process.wait(), timeout=2)


def _result(
    process: asyncio.subprocess.Process,
    out: bytearray,
    err: bytearray,
    timed_out: bool,
    state: _State,
) -> SandboxResult:
    killed = timed_out or state.truncated
    return SandboxResult(
        exit_code=None if killed else process.returncode,
        stdout=bytes(out).decode("utf-8", "replace"),
        stderr=bytes(err).decode("utf-8", "replace"),
        timed_out=timed_out,
        output_truncated=state.truncated,
    )


def _is_windows() -> bool:
    return os.name == "nt"


def _child_env(cwd: Path) -> dict[str, str]:
    """The only environment the child sees: no provider credential, nothing inherited."""
    if _is_windows():
        root = os.environ.get("SystemRoot", r"C:\Windows")
        return {
            "SystemRoot": root,
            "ComSpec": rf"{root}\System32\cmd.exe",
            "PATH": rf"{root}\System32;{root}",
            "TEMP": str(cwd),
            "TMP": str(cwd),
            "USERPROFILE": str(cwd),
            "PYTHONIOENCODING": "utf-8",
        }
    return {"PATH": _SAFE_PATH, "HOME": str(cwd), "LANG": "C.UTF-8"}


def _process_options(limits: SandboxLimits) -> dict[str, object]:
    """How the child is started: its own process group, and resource limits where they exist."""
    if _is_windows():
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}  # type: ignore[attr-defined]
    return {
        "start_new_session": True,  # so the whole group can be killed
        "preexec_fn": _apply_limits(limits),
    }


def _kill(process: asyncio.subprocess.Process) -> None:
    if _is_windows():  # the whole tree, not only the shell or interpreter that was started
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(  # noqa: S603 - fixed argv
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                timeout=5,
                check=False,
            )
        with contextlib.suppress(ProcessLookupError, PermissionError):
            process.kill()
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, 9)


def _apply_limits(limits: SandboxLimits):
    # `resource` is Unix only: imported here, never at module load, so the package still
    # imports on Windows (where the sandbox refuses to be built, see `_check_isolation`).
    import resource

    def apply() -> None:
        memory = limits.memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_s, limits.cpu_s))
        resource.setrlimit(resource.RLIMIT_FSIZE, (limits.max_file_bytes, limits.max_file_bytes))
        resource.setrlimit(resource.RLIMIT_NOFILE, (limits.max_open_files, limits.max_open_files))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    return apply
