"""Document tool provider: `docs.*` tools of the writer agent (ADR-026).

The agent prepares documents as Markdown (`.md`) and LaTeX (`.tex`) and compiles a LaTeX document
to PDF. Nothing here publishes, sends or contacts anyone.

Tools (names follow `<provider>.<action>`):

- `docs.read`         read a `.md` or `.tex` document                    READ
- `docs.write`        create or overwrite a `.md` or `.tex` document     WRITE
- `docs.patch`        replace one exact, unique text in a document       WRITE
- `docs.compile_pdf`  compile a `.tex` document to a PDF next to it      WRITE

Files live under one directory (`OPENCLAW_DOCS_DIR`, default `<workspace>/shared/reports`). Paths
are relative to it and come from an LLM: never trusted (no absolute path, no `..`, no dotfile, no
symbolic link that leaves the directory). Each document should live in its own subdirectory:
compilation can only read and write inside the directory of the `.tex` file.

`docs.compile_pdf` is only offered when a LaTeX engine is installed and the sandbox mechanism
(network isolation) is usable; otherwise `compile_unavailable` says why and the composition root
reports it. The restrictions of the compilation are described in `latex.py`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from openclaw.domain.shared.errors import ToolError, ValidationError
from openclaw.domain.tools.model import RiskLevel, ToolSpec
from openclaw.infrastructure.tools.base import ToolSet, clip, req_str
from openclaw.infrastructure.tools.code.sandbox import (
    SandboxLimits,
    SandboxResult,
    SubprocessSandbox,
)
from openclaw.infrastructure.tools.docs import latex
from openclaw.infrastructure.tools.docs.files import DocumentFiles

MAX_FILE_CHARS = 200_000
EXTENSIONS = frozenset({".md", ".tex"})
DEFAULT_TIMEOUT_S = 60.0
_TRUE = frozenset({"1", "true", "yes", "on"})


class Runner(Protocol):
    """What the compile tool needs from a sandbox (`SubprocessSandbox` satisfies it)."""

    limits: SandboxLimits

    async def run(
        self, argv: list[str], cwd: Path, *, env: Mapping[str, str] | None = None
    ) -> SandboxResult: ...


@dataclass(frozen=True, slots=True)
class DocsConfig:
    root: Path
    engine: str = latex.DEFAULT_ENGINE
    engine_path: str | None = None  # absolute path found on this machine; None = no engine
    timeout_s: float = DEFAULT_TIMEOUT_S
    classes: frozenset[str] = frozenset(latex.DEFAULT_CLASSES)
    packages: frozenset[str] = frozenset(latex.DEFAULT_PACKAGES)
    allow_unisolated: bool = False  # explicit opt-in: compile without network isolation (ADR-027)

    @staticmethod
    def root_from_env(env: Mapping[str, str], default_root: Path) -> Path:
        """The documents directory alone (the download route needs no engine settings)."""
        raw_dir = env.get("OPENCLAW_DOCS_DIR", "").strip()
        return (Path(raw_dir) if raw_dir else default_root).resolve()

    @classmethod
    def from_env(cls, env: Mapping[str, str], default_root: Path) -> DocsConfig:
        engine = env.get("OPENCLAW_DOCS_LATEX_ENGINE", "").strip().lower() or latex.DEFAULT_ENGINE
        if engine not in latex.ENGINES:
            raise ValidationError(
                f"OPENCLAW_DOCS_LATEX_ENGINE: unknown engine '{engine}' "
                f"(available: {', '.join(latex.ENGINES)})"
            )
        raw_timeout = env.get("OPENCLAW_DOCS_LATEX_TIMEOUT", "").strip()
        try:
            timeout = float(raw_timeout) if raw_timeout else DEFAULT_TIMEOUT_S
        except ValueError as exc:
            raise ValidationError("OPENCLAW_DOCS_LATEX_TIMEOUT must be a number") from exc
        if timeout <= 0:
            raise ValidationError("OPENCLAW_DOCS_LATEX_TIMEOUT must be > 0")
        raw_packages = env.get("OPENCLAW_DOCS_LATEX_PACKAGES", "").strip()
        packages = (
            frozenset(p.strip() for p in raw_packages.split(",") if p.strip())
            if raw_packages
            else frozenset(latex.DEFAULT_PACKAGES)
        )
        return cls(
            root=cls.root_from_env(env, default_root),
            engine=engine,
            engine_path=latex.find_engine(engine),
            timeout_s=timeout,
            packages=packages,
            allow_unisolated=env.get("OPENCLAW_DOCS_ALLOW_UNISOLATED", "").strip().lower() in _TRUE,
        )


_PATH = {"type": "string", "description": "path relative to the documents directory (.md or .tex)"}
_SPECS = {
    "docs.read": ToolSpec(
        name="docs.read",
        description="Read a Markdown (.md) or LaTeX (.tex) document (relative path).",
        input_schema={"type": "object", "properties": {"path": _PATH}, "required": ["path"]},
        output_schema={"type": "string"},
        risk_level=RiskLevel.READ,
    ),
    "docs.write": ToolSpec(
        name="docs.write",
        description=(
            "Create or overwrite a Markdown (.md) or LaTeX (.tex) document (relative path; parent "
            "directories are created). Put each LaTeX document in its own subdirectory."
        ),
        input_schema={
            "type": "object",
            "properties": {"path": _PATH, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
        output_schema={"type": "string"},
        risk_level=RiskLevel.WRITE,
    ),
    "docs.patch": ToolSpec(
        name="docs.patch",
        description=(
            "Replace one exact text by another in a document. `old` must appear exactly once."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": _PATH,
                "old": {"type": "string"},
                "new": {"type": "string"},
            },
            "required": ["path", "old", "new"],
        },
        output_schema={"type": "string"},
        risk_level=RiskLevel.WRITE,
    ),
    "docs.compile_pdf": ToolSpec(
        name="docs.compile_pdf",
        description=(
            "Compile a LaTeX (.tex) document to a PDF written next to it. The engine runs with "
            "shell escape disabled, no network, a time limit, and can only read and write in the "
            "directory of the .tex file. Only the document classes and packages of the allowlist "
            "are accepted. One call is one engine run: call it again to resolve references and "
            "the table of contents. On failure the error holds an excerpt of the compilation "
            "log: correct the source and compile again."
        ),
        input_schema={"type": "object", "properties": {"path": _PATH}, "required": ["path"]},
        output_schema={"type": "object"},
        risk_level=RiskLevel.WRITE,
    ),
}
_COMPILE = "docs.compile_pdf"
_UNISOLATED_DESCRIPTION = (
    "Compile a LaTeX (.tex) document to a PDF written next to it. The engine runs with shell "
    "escape disabled, a time limit, and TeX's restricted file mode (it can only read and write "
    "in the directory of the .tex file). Only the document classes and packages of the "
    "allowlist are accepted. One call is one engine run: call it again to resolve references "
    "and the table of contents. On failure the error holds an excerpt of the compilation log: "
    "correct the source and compile again."
)


class DocsToolProvider(ToolSet):
    """A ToolProvider. Reads and writes documents in one directory and compiles LaTeX."""

    def __init__(self, config: DocsConfig, sandbox: Runner | None = None) -> None:
        super().__init__()
        self._config = config
        config.root.mkdir(parents=True, exist_ok=True)
        self._files = DocumentFiles(config.root)
        self._runner: Runner | None = None
        self.compile_unavailable: str | None = None
        self.compile_isolated = (
            True  # False only with the explicit opt-in, where isolation is absent
        )
        self._setup_compilation(sandbox)
        for name, spec in _SPECS.items():
            if name == _COMPILE and self._runner is None:
                continue
            if name == _COMPILE and not self.compile_isolated:
                spec = replace(spec, description=_UNISOLATED_DESCRIPTION)
            self._add(spec, self._handler(name))

    async def aclose(self) -> None:
        return None

    def _setup_compilation(self, sandbox: Runner | None) -> None:
        config = self._config
        if config.engine_path is None:
            self.compile_unavailable = (
                f"no LaTeX engine found ({config.engine} is not installed on this machine)"
            )
            return
        if sandbox is not None:
            self._runner = sandbox
            return
        limits = SandboxLimits(
            timeout_s=config.timeout_s,
            cpu_s=int(config.timeout_s) + 10,
            memory_mb=1024,
            max_output_bytes=64_000,
            max_file_bytes=50_000_000,
            max_open_files=256,
        )
        try:
            # Fails closed without network isolation, unless the opt-in is set (ADR-027).
            sandbox = SubprocessSandbox(limits, allow_unisolated=config.allow_unisolated)
        except ValidationError as exc:
            self.compile_unavailable = (
                f"LaTeX compilation needs network isolation: {exc} "
                "(set OPENCLAW_DOCS_ALLOW_UNISOLATED=true to compile without it, see .env.example)"
            )
            return
        self._runner = sandbox
        self.compile_isolated = sandbox.isolated

    def _handler(self, name: str):
        async def handle(args: Mapping[str, Any]) -> Any:
            return await self._run(name, args)

        return handle

    async def _run(self, name: str, args: Mapping[str, Any]) -> Any:
        path = self._resolve(req_str(args, "path", max_len=500))
        if name == "docs.read":
            return _read(path)
        if name == "docs.write":
            return self._write(path, _text(args, "content"))
        if name == "docs.patch":
            return self._patch(path, _text(args, "old"), _text(args, "new"))
        if name == _COMPILE:
            return await self._compile(path)
        raise ToolError(f"unknown docs tool: {name}")

    # -- paths -------------------------------------------------------------------------------
    def _resolve(self, relative: str) -> Path:
        """The path inside the documents directory, or a ValidationError (see `files.py`)."""
        return self._files.resolve(relative, EXTENSIONS)

    def _shown(self, path: Path) -> str:
        return self._files.relative(path)

    # -- documents ---------------------------------------------------------------------------
    def _write(self, path: Path, content: str) -> str:
        if path.is_dir():
            raise ValidationError("path must name a file")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"written: {self._shown(path)} ({len(content)} characters)"

    def _patch(self, path: Path, old: str, new: str) -> str:
        if not old:
            raise ValidationError("'old' must not be empty")
        if not path.is_file():
            raise ToolError("file not found")
        content = path.read_text(encoding="utf-8")
        count = content.count(old)
        if count != 1:
            raise ToolError(f"'old' must appear exactly once in the file (found {count})")
        path.write_text(content.replace(old, new, 1), encoding="utf-8")
        return f"patched: {self._shown(path)}"

    # -- compilation -------------------------------------------------------------------------
    async def _compile(self, tex: Path) -> dict[str, object]:
        runner, engine_path = self._runner, self._config.engine_path
        assert runner is not None and engine_path is not None  # the tool is only served then
        if tex.suffix.lower() != ".tex":
            raise ValidationError("only a .tex document can be compiled")
        if not tex.is_file():
            raise ToolError("file not found")
        directory = tex.parent
        self._check_directory(directory)
        pdf = tex.with_suffix(".pdf")
        pdf.unlink(missing_ok=True)  # a failed run must not leave a stale PDF that looks fresh
        if _windows():  # no `env` program: the settings and the engine's PATH go by the sandbox
            argv = latex.build_command(engine_path, tex.name, use_env=False)
            result = await runner.run(argv, directory, env=latex.windows_environment(engine_path))
        else:
            argv = latex.build_command(engine_path, tex.name)
            result = await runner.run(argv, directory)
        if result.timed_out:
            raise ToolError(
                f"compilation stopped: time limit reached ({runner.limits.timeout_s:g} s)"
                + _detail(self._log(tex, result))
            )
        if result.output_truncated:
            raise ToolError("compilation stopped: the engine wrote too much output")
        if result.exit_code != 0 or not _is_pdf(pdf):
            raise ToolError(
                f"compilation failed (exit code {result.exit_code})"
                + _detail(self._log(tex, result))
            )
        return {"pdf": self._shown(pdf), "tex": self._shown(tex), "bytes": pdf.stat().st_size}

    def _check_directory(self, directory: Path) -> None:
        """Nothing that could lead out of the directory, and only allowed classes and packages."""
        count = 0
        for current, dirs, files in os.walk(directory, followlinks=False):
            for name in (*dirs, *files):
                if (Path(current) / name).is_symlink():
                    raise ValidationError(
                        "the document directory contains a symbolic link: compilation refused"
                    )
            for name in files:
                if not name.lower().endswith(".tex"):
                    continue
                count += 1
                if count > latex.MAX_TEX_FILES:
                    raise ValidationError("too many .tex files in the document directory")
                source = Path(current) / name
                if source.stat().st_size > latex.MAX_TEX_BYTES:
                    raise ValidationError(f"{name} is too large to compile")
                bad = latex.forbidden(
                    source.read_text(encoding="utf-8", errors="replace"),
                    self._config.classes,
                    self._config.packages,
                )
                if bad:
                    raise ValidationError(
                        f"not allowed in {name}: {', '.join(bad)}. Allowed classes: "
                        f"{', '.join(sorted(self._config.classes))}; allowed packages: "
                        f"{', '.join(sorted(self._config.packages))}"
                    )

    def _log(self, tex: Path, result: SandboxResult) -> str:
        """The engine's own log when it exists, else what it printed."""
        log_file = tex.with_suffix(".log")
        text = ""
        if log_file.is_file() and not log_file.is_symlink():
            with log_file.open("rb") as handle:
                text = handle.read(1_000_000).decode("utf-8", errors="replace")
        return latex.log_excerpt(text or result.stdout or result.stderr)


def _windows() -> bool:
    return os.name == "nt"


def _detail(excerpt: str) -> str:
    return f"\n--- log excerpt ---\n{excerpt}" if excerpt else ""


def _is_pdf(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    with path.open("rb") as handle:
        return handle.read(4) == b"%PDF"


def _text(args: Mapping[str, Any], name: str) -> str:
    value = args.get(name)
    if not isinstance(value, str):
        raise ValidationError(f"'{name}' must be a string")
    if len(value) > MAX_FILE_CHARS:
        raise ValidationError(f"'{name}' is too long (max {MAX_FILE_CHARS} characters)")
    return value


def _read(path: Path) -> str:
    if not path.is_file():
        raise ToolError("file not found")
    if path.stat().st_size > MAX_FILE_CHARS * 4:
        raise ToolError("file is too large to read")
    return clip(path.read_text(encoding="utf-8", errors="replace"), MAX_FILE_CHARS)
