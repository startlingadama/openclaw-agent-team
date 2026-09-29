"""Code tool provider: `code.*` tools of the code-executor agent (ADR-020, ADR-025).

Everything runs in the sandbox (`sandbox.py`); files live in a private directory per calling
agent (the caller is set by the runtime, never by the LLM). Paths are relative to that directory
and cannot leave it. Arguments come from an LLM: never trusted.

Tools (names follow `<provider>.<action>`):

- `code.execute`     run a short Python script                      WRITE
- `code.terminal`    run a shell command                            WRITE
- `code.read_file`   read a text file                               READ
- `code.write_file`  create or overwrite a text file                WRITE
- `code.patch_file`  replace one exact, unique text in a file       WRITE
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openclaw.domain.agents.model import AgentId
from openclaw.domain.shared.errors import OpenClawError, ToolError, ValidationError
from openclaw.domain.tools.model import RiskLevel, ToolCall, ToolSpec
from openclaw.domain.tools.ports import ScriptToolPort
from openclaw.infrastructure.tools.base import clip, req_str
from openclaw.infrastructure.tools.code.prelude import PRELUDE
from openclaw.infrastructure.tools.code.sandbox import (
    SandboxLimits,
    SandboxResult,
    SubprocessSandbox,
)

MAX_SCRIPT_CHARS = 20_000
MAX_COMMAND_CHARS = 4_000
MAX_FILE_CHARS = 200_000
MAX_REPLY_CHARS = 1_000_000  # largest tool result handed back to a script
NO_RECURSION = frozenset({"code.execute"})  # a script does not start another script
_AGENT_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_MECHANISMS = ("subprocess",)
_TRUE = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True, slots=True)
class CodeConfig:
    workdir: Path
    limits: SandboxLimits = SandboxLimits()
    allow_unisolated: bool = False  # explicit opt-in, see sandbox.py

    @classmethod
    def from_env(cls, env: Mapping[str, str], default_workdir: Path) -> CodeConfig:
        mechanism = env.get("OPENCLAW_CODE_SANDBOX", "").strip().lower()
        if mechanism not in _MECHANISMS:
            raise ValidationError(
                f"OPENCLAW_CODE_SANDBOX: unknown mechanism '{mechanism}' "
                f"(available: {', '.join(_MECHANISMS)})"
            )
        raw_dir = env.get("OPENCLAW_CODE_WORKDIR", "").strip()
        defaults = SandboxLimits()
        limits = SandboxLimits(
            timeout_s=_number(env, "OPENCLAW_CODE_TIMEOUT", defaults.timeout_s, float),
            cpu_s=_number(env, "OPENCLAW_CODE_CPU_SECONDS", defaults.cpu_s, int),
            memory_mb=_number(env, "OPENCLAW_CODE_MEMORY_MB", defaults.memory_mb, int),
            max_output_bytes=_number(
                env, "OPENCLAW_CODE_MAX_OUTPUT_BYTES", defaults.max_output_bytes, int
            ),
        )
        unisolated = env.get("OPENCLAW_CODE_ALLOW_UNISOLATED", "").strip().lower() in _TRUE
        return cls(Path(raw_dir).resolve() if raw_dir else default_workdir, limits, unisolated)


def _number(env: Mapping[str, str], key: str, default: Any, kind: type) -> Any:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        value = kind(raw)
    except ValueError as exc:
        raise ValidationError(f"{key} must be a number") from exc
    if value <= 0:
        raise ValidationError(f"{key} must be > 0")
    return value


_SPECS = {
    "code.execute": ToolSpec(
        name="code.execute",
        description=(
            "Run a short Python 3 script in the sandbox (no network, time, memory and output "
            "limits). Print what you need: stdout and stderr are the observation. In the script, "
            '`tools.call("<tool name>", **arguments)` calls one of your own tools and returns '
            "its result; it raises ToolCallError when the tool is denied or fails. "
            "The permissions of the agent apply to each call. `code.execute` cannot be called "
            "from a script."
        ),
        input_schema={
            "type": "object",
            "properties": {"script": {"type": "string"}},
            "required": ["script"],
        },
        output_schema={"type": "object"},
        risk_level=RiskLevel.WRITE,
    ),
    "code.terminal": ToolSpec(
        name="code.terminal",
        description=(
            "Run a shell command in the sandbox terminal (no network, same limits). The working "
            "directory is your sandbox directory."
        ),
        input_schema={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
        output_schema={"type": "object"},
        risk_level=RiskLevel.WRITE,
    ),
    "code.read_file": ToolSpec(
        name="code.read_file",
        description="Read a text file of your sandbox directory (relative path).",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        output_schema={"type": "string"},
        risk_level=RiskLevel.READ,
    ),
    "code.write_file": ToolSpec(
        name="code.write_file",
        description=(
            "Create or overwrite a text file of your sandbox directory (relative path; "
            "parent directories are created)."
        ),
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
        output_schema={"type": "string"},
        risk_level=RiskLevel.WRITE,
    ),
    "code.patch_file": ToolSpec(
        name="code.patch_file",
        description=(
            "Replace one exact text by another in a file of your sandbox directory. `old` must "
            "appear exactly once."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old": {"type": "string"},
                "new": {"type": "string"},
            },
            "required": ["path", "old", "new"],
        },
        output_schema={"type": "string"},
        risk_level=RiskLevel.WRITE,
    ),
}


class CodeToolProvider:
    """A ToolProvider. Runs code and commands in the sandbox only."""

    tool_names = frozenset(_SPECS)

    def __init__(self, config: CodeConfig, sandbox: SubprocessSandbox | None = None) -> None:
        self._workdir = config.workdir
        self._sandbox = sandbox or SubprocessSandbox(config.limits, config.allow_unisolated)
        self._broker: ScriptToolPort | None = None

    def attach(self, broker: ScriptToolPort) -> None:
        """Where the tool calls of scripts go (set by the composition root)."""
        self._broker = broker

    def get_spec(self, name: str) -> ToolSpec | None:
        return _SPECS.get(name)

    async def aclose(self) -> None:
        return None

    async def execute(self, call: ToolCall, caller: AgentId) -> Any:
        root = self._root(caller)
        args = call.arguments
        if call.name == "code.execute":
            script = req_str(args, "script", max_len=MAX_SCRIPT_CHARS)
            argv = self._sandbox.python_argv(PRELUDE + "\n" + script)

            async def on_call(payload: str) -> str:
                return await self._serve(payload, caller)

            return self._report(await self._sandbox.run_channel(argv, root, on_call))
        if call.name == "code.terminal":
            command = req_str(args, "command", max_len=MAX_COMMAND_CHARS)
            return self._report(await self._sandbox.run(self._sandbox.shell_argv(command), root))
        if call.name == "code.read_file":
            return _read(_resolve(root, req_str(args, "path", max_len=500)))
        if call.name == "code.write_file":
            return _write(root, req_str(args, "path", max_len=500), _text(args, "content"))
        if call.name == "code.patch_file":
            return _patch(root, req_str(args, "path", max_len=500), _text(args, "old"), args)
        raise ToolError(f"unknown code tool: {call.name}")

    async def _serve(self, payload: str, caller: AgentId) -> str:
        """One request of a script -> one answer line. Errors go back to the script."""
        try:
            request = json.loads(payload)
            name, arguments = request["tool"], request.get("arguments", {})
            if not isinstance(name, str) or not isinstance(arguments, dict):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            return _reply(False, error="malformed tool request")
        if name in NO_RECURSION:
            return _reply(False, error=f"'{name}' cannot be called from a script")
        if self._broker is None:
            return _reply(False, error="tools are not available to scripts")
        try:
            result = await self._broker.call(ToolCall(name, arguments), caller)
        except OpenClawError as exc:
            return _reply(False, error=str(exc))
        answer = _reply(True, result=result)
        if len(answer) > MAX_REPLY_CHARS:
            return _reply(False, error="the tool result is too large for a script")
        return answer

    def _root(self, caller: AgentId) -> Path:
        if not _AGENT_ID.match(str(caller)):
            raise ValidationError("invalid agent id")
        root = self._workdir / str(caller)
        root.mkdir(parents=True, exist_ok=True)
        return root.resolve()

    def _report(self, result: SandboxResult) -> dict[str, object]:
        data = result.as_dict()
        data["stdout"] = clip(result.stdout, self._sandbox.limits.max_output_bytes)
        return data


def _reply(ok: bool, **fields: Any) -> str:
    return json.dumps({"ok": ok, **fields}, default=str)


def _text(args: Mapping[str, Any], name: str) -> str:
    value = args.get(name)
    if not isinstance(value, str):
        raise ValidationError(f"'{name}' must be a string")
    if len(value) > MAX_FILE_CHARS:
        raise ValidationError(f"'{name}' is too long (max {MAX_FILE_CHARS} characters)")
    return value


def _resolve(root: Path, relative: str) -> Path:
    """The path inside `root`, or a ValidationError: absolute paths, `..` and symlinks that lead
    out are refused."""
    if os.path.isabs(relative) or "\x00" in relative:
        raise ValidationError("path must be relative to the sandbox directory")
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValidationError("path leaves the sandbox directory")
    return path


def _read(path: Path) -> str:
    if not path.is_file():
        raise ToolError("file not found")
    if path.stat().st_size > MAX_FILE_CHARS * 4:
        raise ToolError("file is too large to read")
    return clip(path.read_text(encoding="utf-8", errors="replace"), MAX_FILE_CHARS)


def _write(root: Path, relative: str, content: str) -> str:
    path = _resolve(root, relative)
    if path == root or path.is_dir():
        raise ValidationError("path must name a file")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"written: {relative} ({len(content)} characters)"


def _patch(root: Path, relative: str, old: str, args: Mapping[str, Any]) -> str:
    new = _text(args, "new")
    if not old:
        raise ValidationError("'old' must not be empty")
    path = _resolve(root, relative)
    if not path.is_file():
        raise ToolError("file not found")
    content = path.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise ToolError(f"'old' must appear exactly once in the file (found {count})")
    path.write_text(content.replace(old, new, 1), encoding="utf-8")
    return f"patched: {relative}"
