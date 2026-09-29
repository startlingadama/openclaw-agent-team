"""LaTeX compilation helpers of the document tools (ADR-026).

Compiling LaTeX is running code written by an LLM from content that may be untrusted
(REQUIREMENTS section 21). The restrictions applied here:

- shell escape disabled (`-no-shell-escape` and `shell_escape=f`);
- TeX's paranoid file mode (`openin_any=p`, `openout_any=p`): no absolute path, no `..`, no dotfile,
  so the source reads and writes only inside the directory of the document;
- no network (the sandbox starts the engine through `unshare --net`, see the code sandbox);
- wall-clock time limit, CPU, memory and file-size limits, capped output (`SandboxLimits`);
- scrubbed environment (the sandbox passes no credential; TeX settings are given with `env`);
- a document class and package allowlist, checked on every `.tex` file of the directory. This is a
  guard rail for the agent, not the security boundary: the boundary is the list above.

The engine is looked up on the machine; its absence is reported, never fatal.
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Mapping
from pathlib import Path

ENGINES = ("xelatex", "pdflatex", "lualatex")
DEFAULT_ENGINE = "xelatex"
DEFAULT_CLASSES = ("article", "report")
DEFAULT_PACKAGES = (
    "fontspec",
    "geometry",
    "hyperref",
    "amsmath",
    "graphicx",
    "booktabs",
    "listings",
)
MAX_TEX_FILES = 200
MAX_TEX_BYTES = 1_000_000
LOG_EXCERPT_CHARS = 3_000
# TeX settings of every run: paranoid file mode (no absolute path, no `..`, no dotfile) and no
# shell escape.
TEX_SETTINGS = {"openin_any": "p", "openout_any": "p", "shell_escape": "f"}
# Windows only: locations (no credential) a TeX distribution needs to find its own files and
# configuration, since the sandbox scrubs the environment.
_WINDOWS_PATHS = ("APPDATA", "LOCALAPPDATA", "ProgramData", "ProgramFiles", "SystemDrive")

_COMMENT = re.compile(r"(?<!\\)%.*")
_CLASS = re.compile(r"\\documentclass\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")
_PACKAGE = re.compile(r"\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")
_ERROR_LINE = re.compile(r"^(!|.+:\d+: )")


def find_engine(name: str) -> str | None:
    """Absolute path of the engine on this machine, or None."""
    return shutil.which(name) if name in ENGINES else None


def build_command(
    engine_path: str, tex_name: str, *, env_path: str | None = None, use_env: bool = True
) -> list[str]:
    """The argv of one compilation. The TeX settings go through `env`, because the sandbox
    scrubs the environment of the child. Where there is no `env` program (Windows), `use_env`
    is False and the same settings are given by `windows_environment` instead."""
    engine = Path(engine_path).name
    argv = []
    if use_env:
        argv += [env_path or shutil.which("env") or "/usr/bin/env"]
        argv += [f"{name}={value}" for name, value in TEX_SETTINGS.items()]
    argv += [
        engine_path,
        "-no-shell-escape",
        "-interaction=nonstopmode",
        "-halt-on-error",
        "-file-line-error",
    ]
    if engine.startswith("lualatex"):
        argv.append("--nosocket")
    argv.append(tex_name)
    return argv


def windows_environment(
    engine_path: str, environ: Mapping[str, str] | None = None
) -> dict[str, str]:
    """What the sandbox adds to the child environment on Windows: the TeX settings, a PATH that
    holds the engine's own directory, and the locations of the TeX distribution. Nothing else of
    the parent environment is passed (no credential)."""
    source = os.environ if environ is None else environ
    root = source.get("SystemRoot", r"C:\Windows")
    path = ";".join([str(Path(engine_path).parent), rf"{root}\System32", root])
    passed = {name: source[name] for name in _WINDOWS_PATHS if source.get(name)}
    return {**TEX_SETTINGS, **passed, "PATH": path}


def declared(source: str) -> tuple[list[str], list[str]]:
    """(document classes, packages) a source declares, comments ignored."""
    text = _COMMENT.sub("", source)
    classes = [c.strip() for m in _CLASS.findall(text) for c in m.split(",") if c.strip()]
    packages = [p.strip() for m in _PACKAGE.findall(text) for p in m.split(",") if p.strip()]
    return classes, packages


def forbidden(source: str, classes: frozenset[str], packages: frozenset[str]) -> list[str]:
    """The document classes and packages of `source` that are not allowed."""
    used_classes, used_packages = declared(source)
    bad = [f"class {c}" for c in used_classes if c not in classes]
    bad += [f"package {p}" for p in used_packages if p not in packages]
    return sorted(set(bad))


def log_excerpt(log: str, limit: int = LOG_EXCERPT_CHARS) -> str:
    """The useful part of a compilation log: around the first error, else the end."""
    lines = log.splitlines()
    first = next((i for i, line in enumerate(lines) if _ERROR_LINE.match(line)), None)
    chosen = lines[max(0, first - 2) : first + 14] if first is not None else lines[-40:]
    text = "\n".join(chosen).strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + " [...truncated]"
    return text
